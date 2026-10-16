"""生成合成发票测试样例（PDF / OFD / XML）。

重要说明：
- 这里的发票数据全部是**虚构的测试数据**（票号、税号、公司名均为编造），
  仅用于验证解析器，不对应任何真实票据。
- 生成的 PDF 使用 PDF 规范内置的中文 CID 字体（STSong-Light）并自带 ToUnicode 映射，
  不依赖任何第三方库，也不需要联网。

用法：
    python tools/make_fixtures.py [输出目录]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fapiao_vault.validate import uscc_check_digit

BUYER_NAME = "测试用科技有限公司"
SELLER_NAME = "示例服务有限公司"


def make_uscc(prefix: str) -> str:
    """由 17 位前缀生成一个校验位合法的统一社会信用代码（测试数据）。"""
    prefix = prefix.upper()
    digit = uscc_check_digit(prefix)
    if digit is None:
        raise ValueError(f"前缀非法：{prefix}")
    return prefix + digit


BUYER_TAX = make_uscc("91330100MA2TETX11")
SELLER_TAX = make_uscc("91110108MA0DEM09Y")

# ---------------------------------------------------------------- PDF 构建


def _to_unicode_cmap(chars: set[str]) -> bytes:
    """构造 ToUnicode CMap：把每个 2 字节码位映射回同一个 Unicode 码点。"""
    items = sorted(chars, key=ord)
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CMapName /FapiaoVaultTest def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for ch in chunk:
            code = f"{ord(ch):04X}"
            lines.append(f"<{code}> <{code}>")
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return ("\n".join(lines) + "\n").encode("latin-1")


def build_pdf(lines: list[str]) -> bytes:
    """用最小 PDF 语法生成一份可提取文本的 PDF（内置 CID 中文字体）。"""
    content_parts = ["BT", "/F1 11 Tf", "15 TL", "40 800 Td"]
    for line in lines:
        hex_text = line.encode("utf-16-be").hex().upper()
        content_parts.append(f"<{hex_text}> Tj")
        content_parts.append("T*")
    content_parts.append("ET")
    content = ("\n".join(content_parts) + "\n").encode("latin-1")

    chars = {c for line in lines for c in line if not c.isspace()}
    cmap = _to_unicode_cmap(chars)

    objs: dict[int, bytes] = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objs[4] = (
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
    )
    objs[5] = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        b"/Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] /ToUnicode 8 0 R >>"
    )
    objs[6] = (
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        b"/FontDescriptor 7 0 R /DW 1000 >>"
    )
    objs[7] = (
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
        b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -254 "
        b"/CapHeight 880 /StemV 93 >>"
    )
    objs[8] = b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream"

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"

    xref_pos = len(out)
    size = len(objs) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in sorted(objs):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- 票面文本


def invoice_lines(
    number: str,
    date_cn: str,
    amount: str,
    tax: str,
    total: str,
    total_words: str,
    seller: str = SELLER_NAME,
    seller_tax: str = SELLER_TAX,
    buyer: str = BUYER_NAME,
    buyer_tax: str = BUYER_TAX,
) -> list[str]:
    """生成一张数电票（电子发票·普通发票）的票面文本行。"""
    return [
        "电子发票（普通发票）",
        f"发票号码：{number}",
        f"开票日期：{date_cn}",
        "购",
        "买",
        "方",
        "信",
        "息",
        f"名称：{buyer}",
        f"统一社会信用代码/纳税人识别号：{buyer_tax}",
        "销",
        "售",
        "方",
        "信",
        "息",
        f"名称：{seller}",
        f"统一社会信用代码/纳税人识别号：{seller_tax}",
        "项目名称 规格型号 单位 数量 单价 金额 税率/征收率 税额",
        f"*运输服务*客运服务费 次 1 {amount} {amount} 3% {tax}",
        f"合 计 ¥{amount} ¥{tax}",
        f"价税合计（大写）{total_words} （小写）¥{total}",
    ]


XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice>
  <InvoiceNumber>{number}</InvoiceNumber>
  <IssueDate>{date}</IssueDate>
  <InvoiceType>电子发票（普通发票）</InvoiceType>
  <BuyerName>{buyer}</BuyerName>
  <BuyerTaxID>{buyer_tax}</BuyerTaxID>
  <SellerName>{seller}</SellerName>
  <SellerTaxID>{seller_tax}</SellerTaxID>
  <TotalAmount>{amount}</TotalAmount>
  <TotalTax>{tax}</TotalTax>
  <TotalAmountWithTax>{total}</TotalAmountWithTax>
  <TotalAmountWithTaxInWords>{words}</TotalAmountWithTaxInWords>
  <CheckCode>{check}</CheckCode>
  <ItemName>*运输服务*客运服务费</ItemName>
</Invoice>
"""


def build_ofd(lines: list[str]) -> bytes:
    """构造一个最小可用的 OFD 包（ZIP + XML，含 <ofd:TextCode> 文字节点）。"""
    import io

    ns = 'xmlns:ofd="http://www.ofdspec.org/2016"'
    text_objects = "\n".join(
        f'      <ofd:TextObject ID="{i}"><ofd:TextCode>{_xml_escape(line)}</ofd:TextCode></ofd:TextObject>'
        for i, line in enumerate(lines, start=1)
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">\n'
        f'  <ofd:Content>\n    <ofd:Layer ID="1">\n{text_objects}\n    </ofd:Layer>\n  </ofd:Content>\n'
        f"</ofd:Page>\n"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ofd:Document {ns}>\n  <ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/></ofd:Pages>\n'
        f"</ofd:Document>\n"
    )
    root = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ofd:OFD {ns} Version="1.0">\n  <ofd:DocBody><ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody>\n'
        f"</ofd:OFD>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OFD.xml", root)
        zf.writestr("Doc_0/Document.xml", document)
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content)
    return buf.getvalue()


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- 主流程


def main(target: str | None = None, quiet: bool = False) -> int:
    out = Path(target) if target else Path(__file__).resolve().parents[1] / "samples"
    out.mkdir(parents=True, exist_ok=True)

    # 1) 数电票 PDF
    lines_pdf = invoice_lines(
        number="25117000000000000001",
        date_cn="2026年09月01日",
        amount="25.00",
        tax="0.75",
        total="25.75",
        total_words="贰拾伍元柒角伍分",
    )
    (out / "01_数电票_出租车_42.50元.pdf").write_bytes(build_pdf(lines_pdf))

    # 2) 数电票 OFD
    lines_ofd = invoice_lines(
        number="25117000000000000002",
        date_cn="2026年09月03日",
        amount="120.00",
        tax="3.60",
        total="123.60",
        total_words="壹佰贰拾叁元陆角整",
        seller="示例酒店管理有限公司",
    )
    (out / "02_数电票_住宿_123.60元.ofd").write_bytes(build_ofd(lines_ofd))

    # 3) 数电票 XML
    xml_doc = XML_TEMPLATE.format(
        number="25117000000000000003",
        date="2026-09-05",
        buyer=BUYER_NAME,
        buyer_tax=BUYER_TAX,
        seller="示例网络科技有限公司",
        seller_tax=SELLER_TAX,
        amount="300.00",
        tax="18.00",
        total="318.00",
        words="叁佰壹拾捌元整",
        check="01234567890123456789",
    )
    (out / "03_数电票_服务费_318.00元.xml").write_text(xml_doc, encoding="utf-8")

    # 4) 重复副本：票号与 01 完全相同（模拟同一张票被重复下载）
    (out / "04_重复下载_同一张票.pdf").write_bytes(build_pdf(lines_pdf))

    # 5) 抬头错误：购买方不是本公司
    lines_wrong = invoice_lines(
        number="25117000000000000005",
        date_cn="2026年09月08日",
        amount="50.00",
        tax="1.50",
        total="51.50",
        total_words="伍拾壹元伍角整",
        buyer="别的公司有限公司",
        buyer_tax=make_uscc("91310101MA1WRNG08"),
    )
    (out / "05_抬头写错的发票.pdf").write_bytes(build_pdf(lines_wrong))

    # 6) 扫描件（图片）：本工具不做 OCR，应被识别并提示
    (out / "06_扫描件_图片发票.jpg").write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
    )

    # 7) 非发票的 XML
    (out / "07_不是发票.xml").write_text(
        '<?xml version="1.0"?><config><name>dummy</name></config>', encoding="utf-8"
    )

    if not quiet:
        print(f"已生成 {len(list(out.iterdir()))} 个测试文件到：{out}")
        for p in sorted(out.iterdir()):
            print(f"  {p.name}  ({p.stat().st_size} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
