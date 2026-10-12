"""归档与报表输出测试。"""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tools import make_fixtures

from fapiao_vault.archive import archive_invoices, build_filename, sanitize, unique_path
from fapiao_vault.models import Invoice
from fapiao_vault.pipeline import scan_directory
from fapiao_vault.report import build_summary, write_csv, write_html, write_json, write_xlsx


class TestSanitize(unittest.TestCase):
    def test_removes_illegal_chars(self):
        self.assertEqual(sanitize('a/b\\c:d*e?f"g<h>i|j'), "abcdefghij")

    def test_collapses_spaces(self):
        self.assertEqual(sanitize("示例 服务   有限公司"), "示例服务有限公司")

    def test_empty_becomes_unknown(self):
        self.assertEqual(sanitize("///"), "未知")

    def test_truncates(self):
        self.assertLessEqual(len(sanitize("测" * 200, max_len=10)), 10)


class TestBuildFilename(unittest.TestCase):
    def _inv(self):
        i = Invoice(
            invoice_number="25117000000000000001",
            invoice_date="2026-09-01",
            seller_name="示例服务有限公司",
            invoice_type="电子发票（普通发票）",
        )
        i.total = Decimal("25.75")
        return i

    def test_default_style(self):
        name = build_filename(self._inv())
        self.assertIn("2026-09-01", name)
        self.assertIn("示例服务有限公司", name)
        self.assertIn("25.75元", name)
        self.assertIn("25117000000000000001", name)

    def test_number_first_style(self):
        name = build_filename(self._inv(), style="number_first")
        self.assertTrue(name.startswith("25117000000000000001"))

    def test_no_illegal_chars(self):
        i = self._inv()
        i.seller_name = 'a/b:c*d?e"f'
        name = build_filename(i)
        for ch in '\\/:*?"<>|':
            self.assertNotIn(ch, name)


class TestUniquePath(unittest.TestCase):
    def test_appends_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.pdf").write_text("x", encoding="utf-8")
            (d / "a_2.pdf").write_text("x", encoding="utf-8")
            p = unique_path(d, "a", ".pdf")
            self.assertEqual(p.name, "a_3.pdf")


class TestReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_fixtures.main(str(cls.dir), quiet=True)
        cls.result = scan_directory(cls.dir)
        cls.summary = build_summary(cls.result)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_summary_totals(self):
        t = self.summary["totals"]
        self.assertEqual(t["scanned"], 7)
        self.assertEqual(t["ok"], 5)
        self.assertEqual(t["duplicate_groups"], 1)
        # 25.75 * 2 + 123.60 + 318.00 + 51.50
        self.assertEqual(t["total"], "544.60")

    def test_summary_groups(self):
        self.assertTrue(self.summary["by_month"])
        self.assertTrue(self.summary["by_seller"])
        self.assertTrue(self.summary["by_type"])
        months = {m["key"] for m in self.summary["by_month"]}
        self.assertIn("2026-09", months)

    def test_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_json(self.summary, Path(d) / "s.json")
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["totals"]["ok"], 5)

    def test_write_csv_has_bom_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_csv(self.summary, Path(d) / "s.csv")
            raw = p.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # utf-8-sig，Excel 不乱码
            text = raw.decode("utf-8-sig")
            self.assertIn("发票号码", text)
            self.assertEqual(len(text.strip().splitlines()), 1 + 7)

    def test_write_html_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_html(self.summary, Path(d) / "r.html")
            text = p.read_text(encoding="utf-8")
            self.assertIn("<!DOCTYPE html>", text)
            self.assertIn("25117000000000000001", text)
            # 自包含：不引用任何外部资源
            self.assertNotIn("http://", text)
            self.assertNotIn("https://", text)
            self.assertNotIn("<script src=", text)
            self.assertNotIn("<link ", text)

    def test_html_escapes_payload(self):
        with tempfile.TemporaryDirectory() as d:
            summary = dict(self.summary)
            summary["rows"] = [dict(self.summary["rows"][0], seller_name="<img src=x>")]
            p = write_html(summary, Path(d) / "e.html")
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("</script><img", text)

    def test_write_xlsx_or_skip(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_xlsx(self.summary, Path(d) / "s.xlsx")
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                self.assertIsNone(p)
            else:
                self.assertIsNotNone(p)
                self.assertTrue(p.exists())
                self.assertGreater(p.stat().st_size, 2000)

    def test_summary_has_cross_period_section(self):
        self.assertIn("cross_period", self.summary)
        self.assertEqual(self.summary["totals"]["cross_period"], 0)

    def test_history_written_by_tool_can_be_loaded(self):
        # 回归：write_json 写出的台账必须能被 load_history 读回。
        # 历史版本 load_history 找 "invoices" 键而 write_json 写 "rows" 键，
        # 导致 --history 跨期查重静默失效。
        from fapiao_vault.dedup import load_history

        with tempfile.TemporaryDirectory() as d:
            p = write_json(self.summary, Path(d) / "台账.json")
            history = load_history(p)
            # rows 含 1 组重复票（同 uid 两份文件），uid→month 字典按 uid 去重属预期
            unique_uids = {r["uid"] for r in self.summary["rows"]}
            self.assertEqual(len(history), len(unique_uids))
            self.assertIn("2026-09", set(history.values()))

    def test_scan_with_history_flags_cross_period(self):
        # 历史台账月份与本次不同 → 命中，且发票被写入问题标记
        with tempfile.TemporaryDirectory() as d:
            hist = Path(d) / "h.json"
            hist.write_text(
                json.dumps({"rows": [{"uid": "25117000000000000001", "month": "2026-08"}]}),
                encoding="utf-8",
            )
            result = scan_directory(self.dir, history_path=hist)
            hits = {inv.invoice_number for inv, _ in result.cross_period}
            self.assertIn("25117000000000000001", hits)
            flagged = next(i for i in result.invoices if i.invoice_number == "25117000000000000001")
            self.assertTrue(any("历史台账" in issue for issue in flagged.issues))

    def test_scan_with_same_month_history_not_flagged(self):
        # 月份相同 → 同一报销期内重扫，属正常操作，不应命中
        with tempfile.TemporaryDirectory() as d:
            hist = Path(d) / "h.json"
            hist.write_text(
                json.dumps({"rows": [{"uid": "25117000000000000001", "month": "2026-09"}]}),
                encoding="utf-8",
            )
            result = scan_directory(self.dir, history_path=hist)
            self.assertEqual(result.cross_period, [])


class TestArchive(unittest.TestCase):
    def test_archive_copies_files(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            src_dir = Path(src)
            make_fixtures.main(str(src_dir), quiet=True)
            result = scan_directory(src_dir)
            done, errors = archive_invoices(result.ok_invoices, Path(dst))
            self.assertEqual(done, 5)
            self.assertEqual(errors, [])
            files = list(Path(dst).rglob("*"))
            self.assertEqual(len([f for f in files if f.is_file()]), 5)
            # 按月份分目录
            self.assertTrue((Path(dst) / "2026-09").is_dir())

    def test_archive_keeps_original(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            src_dir = Path(src)
            make_fixtures.main(str(src_dir), quiet=True)
            before = len(list(src_dir.iterdir()))
            result = scan_directory(src_dir)
            archive_invoices(result.ok_invoices, Path(dst))
            self.assertEqual(len(list(src_dir.iterdir())), before)

    def test_archive_missing_source(self):
        i = Invoice(source_file="/definitely/not/here.pdf", invoice_number="X1")
        done, errors = archive_invoices([i], Path(tempfile.mkdtemp()))
        self.assertEqual(done, 0)
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
