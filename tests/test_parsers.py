"""解析器测试：在真实生成的文件上做端到端验证（PDF / OFD / XML）。"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tools import make_fixtures

from fapiao_vault.parsers import parse_file
from fapiao_vault.pipeline import scan_directory


class TestParsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_fixtures.main(str(cls.dir), quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # ---------- PDF ----------

    def test_pdf_text_layer(self):
        inv = parse_file(self.dir / "01_数电票_出租车_42.50元.pdf")
        self.assertEqual(inv.file_format, "pdf")
        self.assertEqual(inv.parse_method, "pdf-text-layer")
        self.assertEqual(inv.invoice_number, "25117000000000000001")
        self.assertEqual(inv.invoice_date, "2026-09-01")
        self.assertEqual(inv.total, Decimal("25.75"))
        self.assertEqual(inv.amount, Decimal("25.00"))
        self.assertEqual(inv.tax, Decimal("0.75"))
        self.assertEqual(inv.buyer_name, "测试用科技有限公司")
        self.assertEqual(inv.seller_name, "示例服务有限公司")
        self.assertEqual(inv.buyer_tax_id, make_fixtures.BUYER_TAX)
        self.assertEqual(inv.seller_tax_id, make_fixtures.SELLER_TAX)
        self.assertEqual(inv.items, ["*运输服务*客运服务费"])
        self.assertEqual(inv.confidence, "high")
        self.assertEqual(inv.issues, [])

    # ---------- OFD ----------

    def test_ofd_stdlib_only(self):
        inv = parse_file(self.dir / "02_数电票_住宿_123.60元.ofd")
        self.assertEqual(inv.file_format, "ofd")
        self.assertEqual(inv.parse_method, "ofd-text")
        self.assertEqual(inv.invoice_number, "25117000000000000002")
        self.assertEqual(inv.invoice_date, "2026-09-03")
        self.assertEqual(inv.total, Decimal("123.60"))
        self.assertEqual(inv.seller_name, "示例酒店管理有限公司")
        self.assertEqual(inv.confidence, "high")

    # ---------- XML ----------

    def test_digital_invoice_xml(self):
        inv = parse_file(self.dir / "03_数电票_服务费_318.00元.xml")
        self.assertEqual(inv.file_format, "xml")
        self.assertEqual(inv.parse_method, "invoice-xml")
        self.assertEqual(inv.invoice_number, "25117000000000000003")
        self.assertEqual(inv.invoice_date, "2026-09-05")
        self.assertEqual(inv.total, Decimal("318.00"))
        self.assertEqual(inv.amount, Decimal("300.00"))
        self.assertEqual(inv.tax, Decimal("18.00"))
        self.assertEqual(inv.buyer_tax_id, make_fixtures.BUYER_TAX)
        self.assertEqual(inv.confidence, "high")

    # ---------- 边界情况 ----------

    def test_image_is_skipped_with_hint(self):
        inv = parse_file(self.dir / "06_扫描件_图片发票.jpg")
        self.assertEqual(inv.parse_method, "skipped-image")
        self.assertEqual(inv.confidence, "low")
        self.assertTrue(any("不做 OCR" in i for i in inv.issues))

    def test_non_invoice_xml_is_flagged(self):
        inv = parse_file(self.dir / "07_不是发票.xml")
        self.assertEqual(inv.confidence, "low")
        self.assertTrue(inv.issues)

    def test_unsupported_extension(self):
        p = self.dir / "abc.xyz"
        p.write_text("hi", encoding="utf-8")
        inv = parse_file(p)
        self.assertEqual(inv.parse_method, "unsupported")

    def test_sha256_present_and_stable(self):
        a = parse_file(self.dir / "01_数电票_出租车_42.50元.pdf")
        b = parse_file(self.dir / "04_重复下载_同一张票.pdf")
        self.assertTrue(len(a.file_sha256) == 64)
        self.assertEqual(a.file_sha256, b.file_sha256)

    # ---------- 流水线 ----------

    def test_scan_directory(self):
        result = scan_directory(self.dir)
        self.assertEqual(result.scanned_count, 7)
        self.assertEqual(len(result.ok_invoices), 5)
        self.assertEqual(len(result.failed_invoices), 2)
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(result.duplicates[0].uid, "25117000000000000001")

    def test_scan_with_company_check(self):
        result = scan_directory(
            self.dir,
            expected_names=[make_fixtures.BUYER_NAME],
            expected_tax_ids=[make_fixtures.BUYER_TAX],
        )
        wrong = [i for i in result.invoices if i.invoice_number == "25117000000000000005"]
        self.assertEqual(len(wrong), 1)
        self.assertTrue(any("抬头不符" in i for i in wrong[0].issues))

    def test_scan_non_recursive(self):
        result = scan_directory(self.dir, recursive=False)
        self.assertEqual(result.scanned_count, 7)

    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            result = scan_directory(Path(d))
            self.assertEqual(result.scanned_count, 0)
            self.assertEqual(result.invoices, [])


if __name__ == "__main__":
    unittest.main()
