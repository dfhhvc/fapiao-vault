"""校验规则：统一社会信用代码校验位、发票号码格式、抬头合规。

这里的规则全部来自公开国家标准与发票编码规范，可离线复现：
- 统一社会信用代码：GB 32100-2015
- 纳税人识别号：15 位（旧）/ 18 位（统一社会信用代码）/ 20 位（数电票部分主体）
"""

from __future__ import annotations

import re
from decimal import Decimal

# GB 32100-2015：统一社会信用代码使用的 31 个字符（刻意剔除了 I、O、S、V、Z）
USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
# 前 17 位对应的加权因子
USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]

_USCC_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")
_OLD_TAX_RE = re.compile(r"^\d{15}$")
_DIGITS_RE = re.compile(r"^\d+$")


def uscc_check_digit(first17: str) -> str | None:
    """计算统一社会信用代码第 18 位校验码。输入非法时返回 None。"""
    if len(first17) != 17 or not _USCC_RE.match(first17 + "0"):
        return None
    total = 0
    for ch, w in zip(first17, USCC_WEIGHTS):
        idx = USCC_ALPHABET.find(ch)
        if idx < 0:
            return None
        total += idx * w
    rem = 31 - (total % 31)
    if rem == 31:
        rem = 0
    return USCC_ALPHABET[rem]


def validate_uscc(code: str) -> tuple[bool, str]:
    """校验统一社会信用代码。返回 (是否通过, 说明)。"""
    code = (code or "").strip().upper()
    if not code:
        return False, "为空"
    if len(code) != 18:
        return False, f"长度应为 18 位，实际 {len(code)} 位"
    if not _USCC_RE.match(code):
        return False, "含非法字符（应为 0-9 与 A-HJ-NPQRTUWXY，无 I/O/S/V/Z）"
    expected = uscc_check_digit(code[:17])
    if expected is None:
        return False, "前 17 位无法解析"
    if code[17] != expected:
        return False, f"校验位错误（应为 {expected}，实际 {code[17]}）"
    return True, "通过"


def validate_tax_id(code: str) -> tuple[bool, str]:
    """校验纳税人识别号：兼容 15 位旧号 / 18 位统一社会信用代码 / 20 位（部分主体）。

    注意：15 位旧纳税人识别号无公开校验位算法，此处只做格式校验。
    """
    code = (code or "").strip().upper()
    if not code:
        return False, "为空"
    if _DIGITS_RE.match(code) and len(code) == 15:
        return True, "通过（15 位旧号，无校验位）"
    if len(code) == 20 and _DIGITS_RE.match(code):
        return True, "通过（20 位，无公开校验位）"
    if len(code) == 18:
        return validate_uscc(code)
    return False, f"无法识别的税号格式（长度 {len(code)}）"


def classify_invoice_number(invoice_code: str, invoice_number: str) -> str:
    """按号码长度判断票种。"""
    code = (invoice_code or "").strip()
    num = (invoice_number or "").strip()
    if not num:
        return "未知"
    if len(num) == 20 and not code:
        return "数电票（全电发票）"
    if len(num) == 8 and len(code) == 12:
        return "增值税电子/纸质发票"
    if len(num) == 8 and not code:
        return "发票号码 8 位（缺发票代码）"
    if len(num) == 20 and code:
        return "数电票（附带代码，异常）"
    return f"未知（代码 {len(code)} 位 / 号码 {len(num)} 位）"


def validate_invoice_number(invoice_code: str, invoice_number: str) -> tuple[bool, str]:
    """发票号码格式校验。"""
    num = (invoice_number or "").strip()
    code = (invoice_code or "").strip()
    if not num:
        return False, "发票号码缺失"
    if not _DIGITS_RE.match(num):
        return False, "发票号码应为纯数字"
    if len(num) == 20:
        if code:
            return False, "数电票（20 位号码）不应同时存在发票代码"
        return True, "通过（数电票 20 位）"
    if len(num) == 8:
        if code and len(code) != 12:
            return False, f"发票代码应为 12 位，实际 {len(code)} 位"
        return True, "通过（8 位号码）"
    return False, f"发票号码长度异常（{len(num)} 位）"


def check_buyer(
    buyer_name: str,
    buyer_tax_id: str,
    expected_names: list[str],
    expected_tax_ids: list[str],
) -> list[str]:
    """抬头合规校验：发票抬头是否与公司要求一致。

    这是报销最常见的退单原因——抬头写错、税号写错。返回问题列表（空 = 通过）。
    """
    issues: list[str] = []
    names = [n.strip() for n in expected_names if n and n.strip()]
    ids = [t.strip().upper() for t in expected_tax_ids if t and t.strip()]

    if names:
        actual = (buyer_name or "").strip()
        if not actual:
            issues.append("购买方名称为空，无法与公司抬头比对")
        elif not any(actual == n or actual in n or n in actual for n in names):
            issues.append(f"抬头不符：发票为「{actual}」，应开「{' / '.join(names)}」")

    if ids:
        actual_id = (buyer_tax_id or "").strip().upper()
        if not actual_id:
            issues.append("购买方纳税人识别号为空")
        elif actual_id not in ids:
            issues.append(f"税号不符：发票为「{actual_id}」，应为「{' / '.join(ids)}」")

    return issues


def validate_invoice_tax_id(inv) -> list[str]:
    """对发票上出现的税号做格式/校验位检查。"""
    issues: list[str] = []
    for label, value in (
        ("购买方税号", inv.buyer_tax_id),
        ("销售方税号", inv.seller_tax_id),
    ):
        if not value:
            continue
        ok, msg = validate_tax_id(value)
        if not ok:
            issues.append(f"{label}「{value}」异常：{msg}")
    return issues


def check_arithmetic(inv) -> list[str]:
    """金额勾稽：金额 + 税额 是否等于价税合计（允许 1 分钱误差）。"""
    issues: list[str] = []
    if inv.amount is not None and inv.tax is not None and inv.total is not None:
        diff = (inv.amount + inv.tax - inv.total).copy_abs()
        if diff > Decimal("0.01"):
            issues.append(f"金额不勾稽：金额 {inv.amount} + 税额 {inv.tax} ≠ 价税合计 {inv.total}")
    return issues
