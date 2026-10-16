"""查重引擎测试。"""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fapiao_vault.dedup import cross_period_duplicates, group_duplicates, load_history
from fapiao_vault.models import Invoice


def inv(number="", code="", date="", total=None, seller="", sha=""):
    i = Invoice(
        invoice_number=number,
        invoice_code=code,
        invoice_date=date,
        seller_name=seller,
        file_sha256=sha,
        source_file=f"/tmp/{number or sha or 'x'}.pdf",
    )
    i.total = Decimal(total) if total is not None else None
    return i


class TestGroupDuplicates(unittest.TestCase):
    def test_same_number_same_month(self):
        a = inv("25117000000000000001", date="2026-09-01", total="25.75")
        b = inv("25117000000000000001", date="2026-09-01", total="25.75")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].severity, "medium")
        self.assertIn("同一张发票出现多次", groups[0].reason)

    def test_same_number_different_month_is_high_risk(self):
        a = inv("25117000000000000001", date="2026-09-01", total="25.75")
        b = inv("25117000000000000001", date="2026-10-01", total="25.75")
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].severity, "high")
        self.assertIn("重复报销风险", groups[0].reason)

    def test_no_duplicates(self):
        a = inv("25117000000000000001", date="2026-09-01", total="25.75")
        b = inv("25117000000000000002", date="2026-09-02", total="30.00")
        self.assertEqual(group_duplicates([a, b]), [])

    def test_file_hash_duplicate_without_number(self):
        a = inv("", sha="a" * 64)
        b = inv("", sha="a" * 64)
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertTrue(any("文件内容完全一致" in g.reason for g in groups))

    def test_fallback_key_when_no_number(self):
        a = inv("", date="2026-09-01", total="25.75", seller="示例服务有限公司", sha="b" * 64)
        b = inv("", date="2026-09-01", total="25.75", seller="示例服务有限公司", sha="c" * 64)
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)
        self.assertIn("无发票号码", groups[0].reason)

    def test_hash_group_not_double_counted(self):
        a = inv("25117000000000000001", date="2026-09-01", total="25.75", sha="d" * 64)
        b = inv("25117000000000000001", date="2026-09-01", total="25.75", sha="d" * 64)
        groups = group_duplicates([a, b])
        self.assertEqual(len(groups), 1)

    def test_three_copies(self):
        items = [
            inv("25117000000000000003", date="2026-09-01", total="10.00"),
            inv("25117000000000000003", date="2026-09-01", total="10.00"),
            inv("25117000000000000003", date="2026-09-01", total="10.00"),
        ]
        groups = group_duplicates(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].invoices), 3)

    def test_sorted_high_risk_first(self):
        items = [
            inv("A0000000000000000001", date="2026-09-01"),
            inv("A0000000000000000001", date="2026-09-01"),
            inv("B0000000000000000002", date="2026-09-01"),
            inv("B0000000000000000002", date="2026-10-01"),
        ]
        groups = group_duplicates(items)
        self.assertEqual(groups[0].severity, "high")


class TestHistory(unittest.TestCase):
    def test_load_history_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_history(Path(d) / "nope.json"), {})

    def test_load_history_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_history(p), {})

    def test_load_history_list_form(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            p.write_text(
                json.dumps([{"uid": "A1", "month": "2026-09"}, {"uid": "A2"}]),
                encoding="utf-8",
            )
            self.assertEqual(load_history(p), {"A1": "2026-09", "A2": "unknown"})

    def test_load_history_dict_form(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            p.write_text(
                json.dumps({"invoices": [{"uid": "B1", "month": "2026-08"}]}),
                encoding="utf-8",
            )
            self.assertEqual(load_history(p), {"B1": "2026-08"})

    def test_load_history_rows_form_written_by_tool(self):
        # 本工具 write_json 实际写出的结构：顶层 dict + "rows" 键（含 uid 和 month）。
        # 历史版本 load_history 只认 "invoices"/列表，读不回自己的台账，此测试锁定该契约。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            p.write_text(
                json.dumps({"totals": {}, "rows": [{"uid": "C1", "month": "2026-09"}]}),
                encoding="utf-8",
            )
            self.assertEqual(load_history(p), {"C1": "2026-09"})

    def test_cross_period_flags_different_month_only(self):
        sep = inv("25117000000000000001", date="2026-09-01", total="25.75")
        october = inv("25117000000000000009", date="2026-10-01", total="30.00")
        same_month = inv("25117000000000000002", date="2026-09-01", total="40.00")
        history = {
            "25117000000000000001": "2026-08",  # 月份不同 → 命中
            "25117000000000000002": "2026-09",  # 月份相同 → 正常重扫，不命中
            "25117000000000000009": "unknown",  # 台账月份缺失 → 命中（无法确认同期）
        }
        hits = cross_period_duplicates([sep, october, same_month], history)
        self.assertEqual(
            {(i.invoice_number, m) for i, m in hits},
            {
                ("25117000000000000001", "2026-08"),
                ("25117000000000000009", "unknown"),
            },
        )

    def test_cross_period_empty_history(self):
        items = [inv("25117000000000000001", date="2026-09-01")]
        self.assertEqual(cross_period_duplicates(items, {}), [])


if __name__ == "__main__":
    unittest.main()
