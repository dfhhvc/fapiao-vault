"""校验规则测试。"""

import unittest

from fapiao_vault.validate import (
    check_arithmetic,
    check_buyer,
    classify_invoice_number,
    uscc_check_digit,
    validate_invoice_number,
    validate_tax_id,
    validate_uscc,
)


class TestUscc(unittest.TestCase):
    def test_check_digit_hand_computed(self):
        """手工验算过的样例（不依赖被测代码自身推导）。

        前缀 91330100MA2XXXXXX，按 GB 32100-2015 加权求和得 4183，
        4183 mod 31 = 29，校验位 = 31 - 29 = 2。
        """
        self.assertEqual(uscc_check_digit("91330100MA2XXXXXX"), "2")

    def test_validate_ok(self):
        ok, msg = validate_uscc("91330100MA2XXXXXX2")
        self.assertTrue(ok, msg)

    def test_validate_wrong_check_digit(self):
        ok, msg = validate_uscc("91330100MA2XXXXXX3")
        self.assertFalse(ok)
        self.assertIn("校验位错误", msg)

    def test_validate_illegal_char(self):
        # O / I / S / V / Z 是被国家标准刻意剔除的字符
        ok, _msg = validate_uscc("91330100MA2XXXXXXO")
        self.assertFalse(ok)

    def test_validate_length(self):
        ok, msg = validate_uscc("91330100")
        self.assertFalse(ok)
        self.assertIn("长度", msg)

    def test_empty(self):
        ok, _msg = validate_uscc("")
        self.assertFalse(ok)

    def test_check_digit_none_on_bad_input(self):
        self.assertIsNone(uscc_check_digit("short"))
        self.assertIsNone(uscc_check_digit("91330100MA2XXXXXXO"))  # 含非法字符 O


class TestTaxId(unittest.TestCase):
    def test_old_15_digit(self):
        ok, _ = validate_tax_id("110101123456789")
        self.assertTrue(ok)

    def test_18_digit_delegates_to_uscc(self):
        self.assertTrue(validate_tax_id("91330100MA2XXXXXX2")[0])
        self.assertFalse(validate_tax_id("91330100MA2XXXXXX3")[0])

    def test_bad_format(self):
        ok, msg = validate_tax_id("12345")
        self.assertFalse(ok)
        self.assertIn("无法识别", msg)


class TestInvoiceNumber(unittest.TestCase):
    def test_digital_invoice_20(self):
        ok, _ = validate_invoice_number("", "25117000000000000001")
        self.assertTrue(ok)

    def test_digital_with_code_is_invalid(self):
        ok, _msg = validate_invoice_number("123456789012", "25117000000000000001")
        self.assertFalse(ok)

    def test_classic_8_with_12_code(self):
        ok, _ = validate_invoice_number("011002100311", "12345678")
        self.assertTrue(ok)

    def test_classic_code_wrong_length(self):
        ok, msg = validate_invoice_number("123", "12345678")
        self.assertFalse(ok)
        self.assertIn("12 位", msg)

    def test_missing(self):
        self.assertFalse(validate_invoice_number("", "")[0])

    def test_classify(self):
        self.assertIn("数电票", classify_invoice_number("", "25117000000000000001"))
        self.assertIn("增值税", classify_invoice_number("011002100311", "12345678"))


class TestBuyerCheck(unittest.TestCase):
    def test_match(self):
        self.assertEqual(
            check_buyer(
                "测试用科技有限公司",
                "91330100MA2XXXXXX2",
                ["测试用科技有限公司"],
                ["91330100MA2XXXXXX2"],
            ),
            [],
        )

    def test_name_mismatch(self):
        issues = check_buyer("别的公司有限公司", "", ["测试用科技有限公司"], [])
        self.assertEqual(len(issues), 1)
        self.assertIn("抬头不符", issues[0])

    def test_tax_id_mismatch(self):
        issues = check_buyer(
            "测试用科技有限公司",
            "91330100MA2XXXXXX2",
            ["测试用科技有限公司"],
            ["91110108MA0DEM09Y6"],
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("税号不符", issues[0])

    def test_missing_buyer_flagged(self):
        issues = check_buyer("", "", ["测试用科技有限公司"], [])
        self.assertTrue(any("名称为空" in i for i in issues))

    def test_no_expectation_no_issue(self):
        self.assertEqual(check_buyer("任意公司", "任意", [], []), [])


class TestArithmetic(unittest.TestCase):
    def _inv(self, amount, tax, total):
        from decimal import Decimal

        from fapiao_vault.models import Invoice

        i = Invoice()
        i.amount = Decimal(amount)
        i.tax = Decimal(tax)
        i.total = Decimal(total)
        return i

    def test_consistent(self):
        self.assertEqual(check_arithmetic(self._inv("25.00", "0.75", "25.75")), [])

    def test_inconsistent(self):
        issues = check_arithmetic(self._inv("25.00", "0.75", "26.00"))
        self.assertEqual(len(issues), 1)
        self.assertIn("不勾稽", issues[0])

    def test_missing_values_skipped(self):
        from fapiao_vault.models import Invoice

        self.assertEqual(check_arithmetic(Invoice()), [])


if __name__ == "__main__":
    unittest.main()
