"""字段抽取测试：覆盖数电票的两种真实文本层版式。"""

import unittest
from decimal import Decimal

from fapiao_vault.fields import (
    chinese_amount_to_decimal,
    extract_from_text,
    extract_parties,
    split_parties,
    text_views,
    to_decimal,
)

# 版式一：分区标记完整（多数数电票 PDF 抽取后长这样）
SAMPLE_BLOCK = """电子发票（普通发票）
发票号码：25117000000000000001
开票日期：2026年09月01日
购买方信息
名称：测试用科技有限公司
统一社会信用代码/纳税人识别号：91330100MA2TETX11W
销售方信息
名称：示例服务有限公司
统一社会信用代码/纳税人识别号：91110108MA0DEM09Y6
项目名称 规格型号 单位 数量 单价 金额 税率/征收率 税额
*运输服务*客运服务费 次 1 25.00 25.00 3% 0.75
合 计 ¥25.00 ¥0.75
价税合计（大写）贰拾伍元柒角伍分 （小写）¥25.75
"""

# 版式二：纵向标签（购/买/方/信/息）被文本层压平并与字段值粘连
SAMPLE_GLUED = (
    "电子发票（普通发票）发票号码：25117000000000000009开票日期：2026年09月09日"
    "购名称：测试用科技有限公司买统一社会信用代码/纳税人识别号：91330100MA2TETX11W方信息"
    "销名称：示例服务有限公司售统一社会信用代码/纳税人识别号：91110108MA0DEM09Y6方信息"
    "项目名称规格型号单位数量单价金额税率/征收率税额"
    "*运输服务*客运服务费次125.0025.003%0.75"
    "合计¥25.00¥0.75价税合计（大写）贰拾伍元柒角伍分（小写）¥25.75"
)


class TestTextViews(unittest.TestCase):
    def test_views(self):
        raw, nl, flat = text_views("购 买 方\n名 称：A公司")
        self.assertEqual(raw.count(" "), 3)  # 「购 买 方\n名 称」中共 3 个空格
        self.assertNotIn(" ", nl)
        self.assertIn("\n", nl)
        self.assertNotIn("\n", flat)
        self.assertNotIn(" ", flat)
        self.assertTrue(flat.startswith("购买方"))


class TestAmountHelpers(unittest.TestCase):
    def test_to_decimal(self):
        self.assertEqual(to_decimal("1,234.56"), Decimal("1234.56"))
        self.assertEqual(to_decimal("¥25.75"), Decimal("25.75"))
        self.assertIsNone(to_decimal(""))
        self.assertIsNone(to_decimal("abc"))

    def test_chinese_simple(self):
        self.assertEqual(chinese_amount_to_decimal("贰拾伍元柒角伍分"), Decimal("25.75"))

    def test_chinese_integer(self):
        self.assertEqual(chinese_amount_to_decimal("叁佰壹拾捌元整"), Decimal("318.00"))

    def test_chinese_hundred(self):
        self.assertEqual(chinese_amount_to_decimal("壹佰贰拾叁元陆角整"), Decimal("123.60"))

    def test_chinese_wan(self):
        self.assertEqual(chinese_amount_to_decimal("壹万贰仟叁佰肆拾伍元整"), Decimal("12345.00"))

    def test_chinese_ten_no_unit_prefix(self):
        # 「拾元整」= 10.00，个位为单位时应视作 1
        self.assertEqual(chinese_amount_to_decimal("拾元整"), Decimal("10.00"))

    def test_none_for_garbage(self):
        self.assertIsNone(chinese_amount_to_decimal(""))


class TestParties(unittest.TestCase):
    def test_split_block_layout(self):
        _, _, flat = text_views(SAMPLE_BLOCK)
        buyer, seller = split_parties(flat)
        self.assertIn("购买方信息", buyer)
        self.assertIn("销售方信息", seller)

    def test_split_when_markers_destroyed(self):
        _, _, flat = text_views(SAMPLE_GLUED)
        _buyer, seller = split_parties(flat)
        self.assertEqual(seller, "")  # 标记被打散，退化路径由 extract_parties 兜底

    def test_block_layout_parties(self):
        _, _, flat = text_views(SAMPLE_BLOCK)
        p = extract_parties(flat)
        self.assertEqual(p["buyer_name"], "测试用科技有限公司")
        self.assertEqual(p["seller_name"], "示例服务有限公司")
        self.assertEqual(p["buyer_tax_id"], "91330100MA2TETX11W")
        self.assertEqual(p["seller_tax_id"], "91110108MA0DEM09Y6")

    def test_glued_layout_parties(self):
        _, _, flat = text_views(SAMPLE_GLUED)
        p = extract_parties(flat)
        self.assertEqual(p["buyer_name"], "测试用科技有限公司")
        self.assertEqual(p["seller_name"], "示例服务有限公司")
        self.assertEqual(p["buyer_tax_id"], "91330100MA2TETX11W")
        self.assertEqual(p["seller_tax_id"], "91110108MA0DEM09Y6")

    def test_project_name_not_treated_as_party(self):
        _, _, flat = text_views(SAMPLE_BLOCK)
        p = extract_parties(flat)
        self.assertNotIn("规格型号", p["buyer_name"])
        self.assertNotIn("项目", p["buyer_name"])


class TestExtractFromText(unittest.TestCase):
    def test_block_layout_full(self):
        inv = extract_from_text(SAMPLE_BLOCK)
        self.assertEqual(inv.invoice_number, "25117000000000000001")
        self.assertEqual(inv.invoice_date, "2026-09-01")
        self.assertEqual(inv.total, Decimal("25.75"))
        self.assertEqual(inv.amount, Decimal("25.00"))
        self.assertEqual(inv.tax, Decimal("0.75"))
        self.assertEqual(inv.total_in_words, "贰拾伍元柒角伍分")
        self.assertEqual(inv.buyer_name, "测试用科技有限公司")
        self.assertEqual(inv.seller_name, "示例服务有限公司")
        self.assertIn("电子发票（普通发票）", inv.invoice_type)
        self.assertEqual(inv.items, ["*运输服务*客运服务费"])
        self.assertEqual(inv.issues, [])

    def test_glued_layout_full(self):
        inv = extract_from_text(SAMPLE_GLUED)
        self.assertEqual(inv.invoice_number, "25117000000000000009")
        self.assertEqual(inv.invoice_date, "2026-09-09")
        self.assertEqual(inv.total, Decimal("25.75"))
        self.assertEqual(inv.buyer_name, "测试用科技有限公司")
        self.assertEqual(inv.seller_name, "示例服务有限公司")
        self.assertEqual(inv.items, ["*运输服务*客运服务费"])

    def test_case_mismatch_detected(self):
        text = SAMPLE_BLOCK.replace("贰拾伍元柒角伍分", "玖拾玖元整")
        inv = extract_from_text(text)
        self.assertTrue(any("大小写金额不一致" in i for i in inv.issues))

    def test_classic_vat_layout(self):
        text = """增值税电子普通发票
发票代码：011002100311
发票号码：12345678
开票日期：2026年08月15日
购买方 名称：测试用科技有限公司 纳税人识别号：91330100MA2TETX11W
销售方 名称：示例商贸有限公司 纳税人识别号：91110108MA0DEM09Y6
合计 ¥100.00 ¥13.00
价税合计（大写）壹佰壹拾叁元整 （小写）¥113.00
"""
        inv = extract_from_text(text)
        self.assertEqual(inv.invoice_code, "011002100311")
        self.assertEqual(inv.invoice_number, "12345678")
        self.assertEqual(inv.total, Decimal("113.00"))
        self.assertEqual(inv.buyer_name, "测试用科技有限公司")
        self.assertEqual(inv.seller_name, "示例商贸有限公司")

    def test_total_derived_when_missing(self):
        text = SAMPLE_BLOCK.replace("价税合计（大写）贰拾伍元柒角伍分 （小写）¥25.75", "")
        inv = extract_from_text(text)
        # 没有价税合计行时，用 金额 + 税额 反推
        self.assertEqual(inv.total, Decimal("25.75"))

    def test_twenty_digit_fallback(self):
        text = "电子发票（普通发票）号码25117000000000000077开票日期：2026年01月02日"
        inv = extract_from_text(text)
        self.assertEqual(inv.invoice_number, "25117000000000000077")
        self.assertTrue(any("兜底" in i for i in inv.issues))

    # ---------- 对抗性版式（真实 PDF 文本层的刁钻形态，回归锁定） ----------

    def test_name_head_glued_label(self):
        # 纵向标签字粘连在名称【头部】（历史版本只剥尾部，购/售 会留在名称里，
        # 导致抬头校验误报「抬头不符」）
        text = """电子发票（普通发票）
购买方信息
名称:购杭州云栖科技有限公司
统一社会信用代码:91330100MA2BCD1234
销售方信息
名称:售上海出行服务有限公司
开票日期:2026年09月01日
价税合计（小写）¥42.50
发票号码:25117000000000000001"""
        inv = extract_from_text(text)
        self.assertEqual(inv.buyer_name, "杭州云栖科技有限公司")
        self.assertEqual(inv.seller_name, "上海出行服务有限公司")

    def test_name_head_glued_unsafe_char_kept(self):
        # "销"是真实公司名首字（如销邦科技），不能被头部剥离误伤
        text = """电子发票（普通发票）
购买方信息
名称:销邦科技股份有限公司
统一社会信用代码:91330100MA2BCD1234
开票日期:2026年09月01日
价税合计（小写）¥42.50
发票号码:25117000000000000002"""
        inv = extract_from_text(text)
        self.assertEqual(inv.buyer_name, "销邦科技股份有限公司")

    def test_seller_name_not_greedy(self):
        # 名称后面没有终止词（税号缺失）时，名称不得贪婪吞掉后续票面文本
        # （历史版本终止词表缺「开票日期/价税合计」等，会吞到 80 字上限）
        text = """电子发票（普通发票）
购买方信息
名称:杭州云栖科技有限公司
统一社会信用代码:91330100MA2BCD1234
销售方信息
名称:上海出行服务有限公司
开票日期:2026-09-01
价税合计（小写）¥99.99
合计¥94.33¥5.66
发票号码:25117000000000000099"""
        inv = extract_from_text(text)
        self.assertEqual(inv.seller_name, "上海出行服务有限公司")
        self.assertEqual(inv.buyer_name, "杭州云栖科技有限公司")
        self.assertEqual(inv.total, Decimal("99.99"))

    def test_per_char_scattered_layout(self):
        # 逐字打散（最恶毒的真实 PDF 抽取形态：每个字之间都有空格）
        text = """电 子 发 票 （ 普 通 发 票 ）
购 买 方 信 息
名 称 : 杭 州 云 栖 科 技 有 限 公 司
统 一 社 会 信 用 代 码 : 9 1 3 3 0 1 0 0 M A 2 B C D 1 2 3 4
销 售 方 信 息
名 称 : 上 海 出 行 服 务 有 限 公 司
开 票 日 期 : 2 0 2 6 年 0 9 月 0 1 日
价 税 合 计 （ 小 写 ） ¥ 4 2 . 5 0
发 票 号 码 : 2 5 1 1 7 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1"""
        inv = extract_from_text(text)
        self.assertEqual(inv.invoice_number, "25117000000000000001")
        self.assertEqual(inv.invoice_date, "2026-09-01")
        self.assertEqual(inv.buyer_name, "杭州云栖科技有限公司")
        self.assertEqual(inv.seller_name, "上海出行服务有限公司")
        self.assertEqual(inv.buyer_tax_id, "91330100MA2BCD1234")
        self.assertEqual(inv.total, Decimal("42.50"))


if __name__ == "__main__":
    unittest.main()
