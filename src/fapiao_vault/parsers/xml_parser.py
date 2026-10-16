"""数电票 XML 解析：仅用 Python 标准库。

电子发票服务平台可以导出 XML 原件，字段是结构化的，直接读标签即可，
既不需要 OCR，也不会有识别误差。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from ..fields import chinese_amount_to_decimal, parse_date, text_views, to_decimal
from ..models import Invoice
from ..validate import classify_invoice_number

# 标签名（取本地名，忽略命名空间）→ Invoice 字段
_KEY_NUMBER = {"invoicenumber", "发票号码", "fphm"}
_KEY_CODE = {"invoicecode", "发票代码", "fpdm"}
_KEY_DATE = {"issuedate", "invoicedate", "开票日期", "kprq"}
_KEY_TOTAL = {
    "totalamountwithtax",
    "totaltaxincludedamount",
    "价税合计",
    "价税合计小写",
    "amountwithtax",
    "jshj",
}
_KEY_TOTAL_WORDS = {"totalamountwithtaxinwords", "价税合计大写", "jshjdx"}
_KEY_AMOUNT = {"totalamount", "amount", "amountwithouttax", "合计金额", "金额", "hjje"}
_KEY_TAX = {"totaltax", "taxamount", "合计税额", "税额", "hjse"}
_KEY_CHECK = {"checkcode", "校验码", "jym"}
_KEY_TYPE = {"invoicetype", "发票票种", "票种"}

_KEY_BUYER_NAME = {"buyername", "购买方名称", "购买方", "gfmc"}
_KEY_SELLER_NAME = {"sellername", "销售方名称", "销售方", "xfmc"}
_KEY_BUYER_ID = {"buyertaxid", "buyertaxpayerid", "购买方纳税人识别号", "购方税号", "gfsh"}
_KEY_SELLER_ID = {"sellertaxid", "sellertaxpayerid", "销售方纳税人识别号", "销方税号", "xfsh"}
_KEY_ITEM = {"itemname", "goodsname", "项目名称", "货物或应税劳务名称", "hwmc"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _flatten(root: ET.Element) -> list[tuple[str, str]]:
    """把 XML 拍平成 (标签本地名, 文本) 的列表，保持文档顺序。"""
    out: list[tuple[str, str]] = []
    for el in root.iter():
        if len(el) == 0 and el.text and el.text.strip():
            out.append((_local(el.tag), el.text.strip()))
    return out


def _norm_key(name: str) -> str:
    return re.sub(r"[\s_\-]", "", name).lower()


def parse_invoice_xml_bytes(
    data: bytes, source_file: str = "", file_format: str = "xml"
) -> Invoice:
    """解析一份数电票 XML。"""
    inv = Invoice(source_file=source_file, file_format=file_format)
    inv.parse_method = "invoice-xml"

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        inv.add_issue(f"XML 解析失败：{exc}")
        return inv

    pairs = _flatten(root)

    def find(keyset: set[str]) -> str:
        target = {_norm_key(k) for k in keyset}
        for name, value in pairs:
            if _norm_key(name) in target:
                return value
        return ""

    inv.invoice_number = find(_KEY_NUMBER)
    inv.invoice_code = find(_KEY_CODE)
    raw_date = find(_KEY_DATE)
    if raw_date:
        if re.match(r"^\d{8}$", raw_date):
            inv.invoice_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        elif re.match(r"^\d{4}-\d{1,2}-\d{1,2}", raw_date):
            y, m, d = re.split(r"[-/.]", raw_date[:10])
            inv.invoice_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        else:
            # 非标准格式时，借用文本规则兜底
            _, _, flat = text_views(f"开票日期:{raw_date}")
            inv.invoice_date = parse_date(flat)

    inv.total = to_decimal(find(_KEY_TOTAL))
    inv.amount = to_decimal(find(_KEY_AMOUNT))
    inv.tax = to_decimal(find(_KEY_TAX))
    inv.total_in_words = find(_KEY_TOTAL_WORDS)
    inv.check_code = find(_KEY_CHECK)

    words_value = chinese_amount_to_decimal(inv.total_in_words) if inv.total_in_words else None
    if words_value is not None and inv.total is not None and words_value != inv.total:
        inv.add_issue(f"大小写金额不一致：大写 {words_value}，小写 {inv.total}")

    if inv.total is None and inv.amount is not None and inv.tax is not None:
        inv.total = (inv.amount + inv.tax).quantize(Decimal("0.01"))
    if inv.amount is None and inv.total is not None and inv.tax is not None:
        inv.amount = (inv.total - inv.tax).quantize(Decimal("0.01"))

    inv.buyer_name = find(_KEY_BUYER_NAME)
    inv.seller_name = find(_KEY_SELLER_NAME)
    inv.buyer_tax_id = find(_KEY_BUYER_ID).upper()
    inv.seller_tax_id = find(_KEY_SELLER_ID).upper()

    items: list[str] = []
    target_item = {_norm_key(k) for k in _KEY_ITEM}
    for name, value in pairs:
        if _norm_key(name) in target_item and value not in items:
            items.append(value)
    inv.items = items[:12]

    inv.invoice_type = find(_KEY_TYPE) or classify_invoice_number(
        inv.invoice_code, inv.invoice_number
    )
    return inv


def parse_xml(path: Path) -> Invoice:
    try:
        data = path.read_bytes()
    except OSError as exc:
        inv = Invoice(source_file=str(path), file_format="xml")
        inv.add_issue(f"文件读取失败：{exc}")
        return inv
    return parse_invoice_xml_bytes(data, source_file=str(path), file_format="xml")
