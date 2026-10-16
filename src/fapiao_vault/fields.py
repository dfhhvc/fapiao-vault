"""字段抽取：从发票文本层中还原结构化字段。

中文发票的版式高度固定，字段标签是强特征（"发票号码""价税合计""校验码"...），
因此用规则解析的准确率和可复现性都优于把发票图片交给大模型。

三种文本视图，各司其职：
- raw   ：原始文本，用于保留换行做分区
- nl    ：去掉空格/制表符但保留换行的文本，用于取公司名（避免把空格吃进去）
- flat  ：去掉全部空白字符的文本，用于取号码/金额等无空白字段
  （原因：PDF 文本层常把"购 买 方 信 息"逐字打散，先压平再匹配才稳）
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import Invoice
from .validate import classify_invoice_number

# ---------------------------------------------------------------- 文本视图

_WS_NO_NEWLINE = re.compile(r"[ \t\u3000\u00a0]+")
_ALL_WS = re.compile(r"\s+")


def text_views(text: str) -> tuple[str, str, str]:
    """返回 (raw, nl, flat) 三种视图。"""
    raw = text or ""
    nl = _WS_NO_NEWLINE.sub("", raw)
    flat = _ALL_WS.sub("", raw)
    return raw, nl, flat


# ---------------------------------------------------------------- 金额工具

_AMOUNT_RE = re.compile(r"[¥￥]?([0-9][0-9,]*\.[0-9]{2})")


def to_decimal(value: str) -> Decimal | None:
    """把 "1,234.56" / "¥1234.56" 之类转成 Decimal。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not s:
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


_CN_DIGITS = {
    "零": 0,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"拾": 10, "十": 10, "佰": 100, "百": 100, "仟": 1000, "千": 1000}


def _parse_simple(seg: str) -> int:
    total, cur = 0, 0
    for ch in seg:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
    return total + cur


def _parse_cn_int(seg: str) -> int:
    if not seg:
        return 0
    total = 0
    if "亿" in seg:
        head, _, tail = seg.partition("亿")
        total += _parse_cn_int(head) * 100000000
        seg = tail
    if "万" in seg:
        head, _, tail = seg.partition("万")
        total += _parse_simple(head) * 10000
        seg = tail
    return total + _parse_simple(seg)


def chinese_amount_to_decimal(text: str) -> Decimal | None:
    """把「壹仟贰佰叁拾肆元伍角陆分」这类大写金额转成 Decimal。

    用途：与小写金额交叉核对——两者不一致往往是发票被涂改的信号。
    """
    if not text:
        return None
    s = text.strip()
    for ch in ("人民币", "整", "正", " "):
        s = s.replace(ch, "")
    if not s:
        return None
    s = s.replace("圆", "元")

    int_part, sep, frac_part = s.partition("元")
    if not sep:
        # 没有"元"，可能是纯"伍角陆分"
        int_value = 0
        frac_part = s
    else:
        int_part = int_part.replace("角", "").replace("分", "")
        if int_part and all(c in _CN_DIGITS or c in _CN_UNITS or c in "万亿" for c in int_part):
            int_value = _parse_cn_int(int_part)
        else:
            int_value = 0

    jiao = fen = 0
    m = re.search(r"([零壹贰叁肆伍陆柒捌玖一二三四五六七八九])角", frac_part)
    if m:
        jiao = _CN_DIGITS[m.group(1)]
    m = re.search(r"([零壹贰叁肆伍陆柒捌玖一二三四五六七八九])分", frac_part)
    if m:
        fen = _CN_DIGITS[m.group(1)]

    if (
        int_value == 0
        and jiao == 0
        and fen == 0
        and not re.search(r"[零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九]", s)
    ):
        return None
    return (Decimal(int_value) + Decimal(jiao) / 10 + Decimal(fen) / 100).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- 字段正则

# 注意：以下正则全部作用于 flat 文本（无任何空白字符）

RE_INVOICE_CODE = re.compile(r"发票代码[:：]?(\d{10,12})")
RE_INVOICE_NUMBER = re.compile(r"发票号码[:：]?(\d{8,20})")
RE_CHECK_CODE = re.compile(r"校验码[:：]?(\d{16,20})")
RE_DATE_CN = re.compile(r"开票日期[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日")
RE_DATE_ISO = re.compile(r"开票日期[:：]?(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
RE_DATE_COMPACT = re.compile(r"开票日期[:：]?(\d{4})(\d{2})(\d{2})")

# 价税合计：数电票版式为「价税合计（大写）...（小写）¥123.45」
RE_TOTAL_XIAOXIE = re.compile(r"价税合计.{0,40}?[（(]小写[)）][¥￥]?([0-9][0-9,]*\.\d{2})")
RE_TOTAL_YEN = re.compile(r"价税合计[¥￥]([0-9][0-9,]*\.\d{2})")
RE_TOTAL_LOOSE = re.compile(r"[（(]小写[)）][¥￥]?([0-9][0-9,]*\.\d{2})")
RE_TOTAL_WORDS = re.compile(
    r"价税合计.{0,20}?[（(]大写[)）]([零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九拾佰仟万亿元圆角分整正]{2,40})"
)

# 「合计 ¥100.00 ¥6.00」→ 金额、税额（出现在表格末尾）
# 必须排除「价税合计」，否则会误取价税合计行的数字
RE_SUBTOTAL = re.compile(r"(?<!价税)[合台]计[¥￥]?([0-9][0-9,]*\.\d{2})[¥￥]?([0-9][0-9,]*\.\d{2})")
# 单行合计（只有金额没有税额）
RE_SUBTOTAL_SINGLE = re.compile(r"(?<!价税)[合台]计[¥￥]([0-9][0-9,]*\.\d{2})(?![0-9])")

RE_INVOICE_TITLE = re.compile(r"电子发票[（(]([^)）]{1,20})[)）]")
RE_VAT_TITLE = re.compile(
    r"(增值税电子(?:普通|专用)发票|电子发票(?:普通|专用)发票|机动车销售统一发票)"
)

RE_TAX_RATE = re.compile(r"(\d{1,2})%")

# 公司名标签。必须排除「项目名称/货物名称/商品名称」等干扰项，
# 否则会把明细行里的品名当成购买方。
# 名称字段的终止词：名称后面不可能出现这些票面结构词。
# 历史版本缺少"开票日期/价税合计"等词，当版式里名称后紧跟这些内容时
# （PDF 抽取顺序不保证版式顺序），.{2,80}? 会贪婪吞掉后续整段票面文本。
_NAME_STOP = (
    "统一社会信用代码|纳税人识别号|识别号|税号|地址|电话|开户行|开户银行|账号"
    "|开票日期|价税合计|发票号码|发票代码|校验码|密码区|备注|项目名称|货物名称"
    "|规格型号|单位|数量|单价|金额|税率|税额|收款人|复核|开票人|销售方|购买方"
)
RE_NAME = re.compile(
    r"(?<!项目)(?<!货物)(?<!商品)(?<!服务)(?<!劳务)(?<!产品)(?<!品名)"
    r"名称[:：]?(.{2,80}?)"
    rf"(?={_NAME_STOP}|$)"
)
RE_TAX_ID = re.compile(
    r"(?:统一社会信用代码/纳税人识别号|统一社会信用代码|纳税人识别号|识别号)[:：]?([0-9A-Za-z]{15,20})"
)
# 部分版式下税号没有标签，只有一串独立字符
RE_STANDALONE_USCC = re.compile(r"(?<![0-9A-Za-z])([0-9A-HJ-NPQRTUWXY]{18})(?![0-9A-Za-z])")

# 明细行的「*类别*品名」。品名后面紧跟数量/单价/金额（都是数字），
# 因此用「非数字」限位，避免把金额一起吞进品名。
RE_ITEM = re.compile(r"\*([^*\n]{1,20})\*([^\d\n%*]{1,30})")

_PARTY_BUYER_MARKERS = ("购买方信息", "购买方")
_PARTY_SELLER_MARKERS = ("销售方信息", "销售方")

# 数电票采用纵向标签（购/买/方/信/息），PDF 文本层压缩后这些字会粘到字段值尾部，
# 例如「杭州云栖科技有限公司买」。这里在保留词长下限的前提下从尾部剥离。
_LAYOUT_CHARS = set("购买方信息销售名称统一社会信用代码纳税人识别号地址电话")

# 头部粘连处理：整词标签 + 单字剥离。
# 单字仅限【现实中不可能是公司名首字】的纵向标签字（购/买/息/售）——
# "名/方/信/销"等可能是真实公司名首字（名创优品/方正/信泰/销邦科技），剥了就误伤。
_HEAD_LABEL_WORDS = re.compile(r"^(购买方信息|销售方信息|购买方|销售方|名称)+[:：]?")
_HEAD_LABEL_CHARS = set("购买息售")

# 常见计量单位：明细行的品名后面会紧跟单位字样，需从品名尾部剥离
_UNIT_CHARS = set("次张个台件套份箱瓶米吨升人棵只把条块台套副付副")


def _clean_name(value: str) -> str:
    s = (value or "").strip("：: 　\t")
    # 纵向标签可能粘连在名称头部或尾部（如「购杭州…公司」/「…公司买」），对称处理：
    # 头部先剥整词标签、再剥安全单字；尾部按版式字符剥离。
    s = _HEAD_LABEL_WORDS.sub("", s)
    while len(s) > 4 and s[0] in _HEAD_LABEL_CHARS and s[1] not in _HEAD_LABEL_CHARS:
        s = s[1:]
    while len(s) > 4 and s[-1] in _LAYOUT_CHARS:
        s = s[:-1]
    return s.strip("：: 　\t")


def _find_marker(flat: str, markers: tuple[str, ...]) -> int:
    """返回任一标记最早出现的位置，找不到返回 -1。"""
    best = -1
    for m in markers:
        i = flat.find(m)
        if i >= 0 and (best < 0 or i < best):
            best = i
    return best


def split_parties(flat: str) -> tuple[str, str]:
    """把发票文本切成「购买方区」和「销售方区」。

    以"销售方"的首次出现位置作为分界：标准票面上购买方在上、销售方在下。
    """
    b = _find_marker(flat, _PARTY_BUYER_MARKERS)
    s = _find_marker(flat, _PARTY_SELLER_MARKERS)
    if b < 0 and s < 0:
        return flat, ""
    if b < 0:
        return flat[:s], flat[s:]
    if s < 0 or s <= b:
        return flat[b:], ""
    return flat[b:s], flat[s:]


def _names_in(flat: str) -> list[str]:
    out: list[str] = []
    for m in RE_NAME.finditer(flat):
        name = _clean_name(m.group(1))
        if name and name not in out:
            out.append(name)
    return out


def _tax_ids_in(block: str) -> list[str]:
    """取区块内所有税号：先按标签取，不够再用独立 18 位统一社会信用代码兜底。"""
    out: list[str] = []
    for m in RE_TAX_ID.finditer(block):
        v = m.group(1).upper()
        if v not in out:
            out.append(v)
    if not out:
        for m in RE_STANDALONE_USCC.finditer(block):
            v = m.group(1).upper()
            if v not in out:
                out.append(v)
    return out


def extract_parties(flat: str) -> dict[str, str]:
    """识别购销双方信息。

    两条路径互相兜底：
    1. 按 购买方/销售方 分区后各取一份（版式标准时最准）
    2. 退化路径：把全文里的「名称」按出现顺序取前两个（分区标记被文本层打散时使用）
    """
    buyer_block, seller_block = split_parties(flat)

    buyer_name = ""
    m = RE_NAME.search(buyer_block)
    if m:
        buyer_name = _clean_name(m.group(1))
    seller_name = ""
    m = RE_NAME.search(seller_block)
    if m:
        seller_name = _clean_name(m.group(1))

    b_ids = _tax_ids_in(buyer_block)
    s_ids = _tax_ids_in(seller_block)

    all_names = _names_in(flat)
    all_ids = _tax_ids_in(flat)

    if not buyer_name and all_names:
        buyer_name = all_names[0]
    if not seller_name:
        for name in all_names:
            if name and name != buyer_name:
                seller_name = name
                break

    buyer_tax = b_ids[0] if b_ids else (all_ids[0] if all_ids else "")
    seller_tax = s_ids[0] if s_ids else ""
    if not seller_tax:
        for tid in all_ids:
            if tid and tid != buyer_tax:
                seller_tax = tid
                break

    return {
        "buyer_name": buyer_name,
        "seller_name": seller_name,
        "buyer_tax_id": buyer_tax,
        "seller_tax_id": seller_tax,
    }


def parse_date(flat: str) -> str:
    for rx in (RE_DATE_CN, RE_DATE_ISO, RE_DATE_COMPACT):
        m = rx.search(flat)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{int(y):04d}-{mo:02d}-{d:02d}"
    return ""


def parse_invoice_type(flat: str) -> str:
    m = RE_INVOICE_TITLE.search(flat)
    if m:
        return "电子发票（" + m.group(1) + "）"
    m = RE_VAT_TITLE.search(flat)
    if m:
        return m.group(1)
    if "数电票" in flat or "全电发票" in flat:
        return "数电票"
    return ""


def extract_from_text(text: str, source_file: str = "", file_format: str = "") -> Invoice:
    """从发票文本层抽取字段，返回一个 Invoice。"""
    _raw, nl, flat = text_views(text)
    inv = Invoice(source_file=source_file, file_format=file_format)

    m = RE_INVOICE_CODE.search(flat)
    if m:
        inv.invoice_code = m.group(1)

    # 发票号码：先精确匹配标签，再兜底用「发票号码」后紧跟的长数字
    m = RE_INVOICE_NUMBER.search(flat)
    if m:
        inv.invoice_number = m.group(1)
    if not inv.invoice_number:
        m = re.search(r"(\d{20})", flat)
        if m:
            inv.invoice_number = m.group(1)
            inv.add_issue("发票号码由兜底规则识别，建议人工复核")

    inv.invoice_date = parse_date(flat)

    m = RE_CHECK_CODE.search(flat)
    if m:
        inv.check_code = m.group(1)

    # 价税合计（小写）
    for rx in (RE_TOTAL_XIAOXIE, RE_TOTAL_YEN, RE_TOTAL_LOOSE):
        m = rx.search(flat)
        if m:
            inv.total = to_decimal(m.group(1))
            break

    # 价税合计（大写）
    m = RE_TOTAL_WORDS.search(flat)
    if m:
        inv.total_in_words = m.group(1)
        words_value = chinese_amount_to_decimal(m.group(1))
        if words_value is not None and inv.total is not None and words_value != inv.total:
            inv.add_issue(f"大小写金额不一致：大写 {words_value}，小写 {inv.total}")

    # 合计行 → 金额、税额
    m = RE_SUBTOTAL.search(flat)
    if m:
        inv.amount = to_decimal(m.group(1))
        inv.tax = to_decimal(m.group(2))
    else:
        m = RE_SUBTOTAL_SINGLE.search(flat)
        if m:
            inv.amount = to_decimal(m.group(1))

    # 如果只有价税合计和税额，反推金额
    if inv.total is not None and inv.tax is not None and inv.amount is None:
        inv.amount = (inv.total - inv.tax).quantize(Decimal("0.01"))
    if inv.total is None and inv.amount is not None and inv.tax is not None:
        inv.total = (inv.amount + inv.tax).quantize(Decimal("0.01"))

    # 购销双方
    parties = extract_parties(flat)
    inv.buyer_name = parties["buyer_name"]
    inv.buyer_tax_id = parties["buyer_tax_id"]
    inv.seller_name = parties["seller_name"]
    inv.seller_tax_id = parties["seller_tax_id"]

    # 票种
    inv.invoice_type = parse_invoice_type(flat) or classify_invoice_number(
        inv.invoice_code, inv.invoice_number
    )

    # 项目名称
    seen: list[str] = []
    for m in RE_ITEM.finditer(nl):
        name = m.group(2).strip("：: 　")
        # 品名后面紧跟的计量单位会被一起吃进来（如"客运服务费次"），从尾部剥离
        while name and name[-1] in _UNIT_CHARS:
            name = name[:-1]
        item = f"*{m.group(1)}*{name}".strip()
        if name and item not in seen:
            seen.append(item)
        if len(seen) >= 12:
            break
    inv.items = seen

    return inv
