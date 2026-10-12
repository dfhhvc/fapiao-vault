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
            self.assertEqual(load_history(Path(d) / "nope.json"), set())

    def test_load_history_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_history(p), set())

    def test_load_history_list_form(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            p.write_text(json.dumps([{"uid": "A1"}, {"uid": "A2"}]), encoding="utf-8")
            self.assertEqual(load_history(p), {"A1", "A2"})

    def test_load_history_dict_form(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            p.write_text(json.dumps({"invoices": [{"uid": "B1"}]}), encoding="utf-8")
            self.assertEqual(load_history(p), {"B1"})

    def test_cross_period(self):
        items = [inv("25117000000000000001"), inv("25117000000000000009")]
        hit = cross_period_duplicates(items, {"25117000000000000009"})
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].invoice_number, "25117000000000000009")


if __name__ == "__main__":
    unittest.main()
