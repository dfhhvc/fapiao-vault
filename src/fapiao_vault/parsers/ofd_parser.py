"""OFD 发票解析：仅用 Python 标准库。

OFD（Open Fixed-layout Document，GB/T 33190-2016）本质上是一个 ZIP 包，
里面是 XML。发票的文字都放在 <TextCode> 节点里，因此「解压 + 遍历 XML 文本节点」
就能拿到完整票面文字——不需要任何第三方库，也不需要 OCR。
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..fields import extract_from_text
from ..models import Invoice

# OFD 中承载文字的元素名（不同生成器命名略有差异）
_TEXT_TAGS = ("TextCode", "Text")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _extract_text_from_xml(data: bytes) -> list[str]:
    """从一段 OFD XML 中按文档顺序取出所有文字。"""
    out: list[str] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # XML 破损时退化为正则兜底，保证不因个别文件整批失败
        text = data.decode("utf-8", errors="ignore")
        for m in re.finditer(r"<(?:\w+:)?Text(?:Code)?[^>]*>(.*?)</(?:\w+:)?Text(?:Code)?>", text, re.S):
            val = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if val:
                out.append(val)
        return out

    for el in root.iter():
        if _localname(el.tag) in _TEXT_TAGS and el.text and el.text.strip():
            out.append(el.text.strip())
    return out


def _find_embedded_invoice_xml(zf: zipfile.ZipFile) -> bytes | None:
    """部分 OFD 内嵌了原始发票 XML（含结构化字段），优先使用它。"""
    for name in zf.namelist():
        lower = name.lower()
        if not lower.endswith(".xml"):
            continue
        if "invoice" in lower or "fapiao" in lower:
            try:
                return zf.read(name)
            except KeyError:
                continue
    return None


def parse_ofd(path: Path) -> Invoice:
    try:
        zf = zipfile.ZipFile(str(path))
    except (zipfile.BadZipFile, OSError) as exc:
        inv = Invoice(source_file=str(path), file_format="ofd")
        inv.parse_method = "ofd-xml"
        inv.add_issue(f"OFD 打开失败（不是有效的 ZIP/OFD 包）：{exc}")
        return inv

    with zf:
        # 1) 优先用内嵌的发票 XML
        embedded = _find_embedded_invoice_xml(zf)
        if embedded:
            from .xml_parser import parse_invoice_xml_bytes

            inv = parse_invoice_xml_bytes(embedded, source_file=str(path), file_format="ofd")
            if inv.invoice_number or inv.total is not None:
                inv.parse_method = "ofd-embedded-xml"
                return inv

        # 2) 遍历包内所有 XML，收集文字
        pieces: list[str] = []
        for name in sorted(zf.namelist()):
            if not name.lower().endswith(".xml"):
                continue
            try:
                data = zf.read(name)
            except KeyError:
                continue
            pieces.extend(_extract_text_from_xml(data))

    text = "\n".join(pieces)
    inv = extract_from_text(text, source_file=str(path), file_format="ofd")
    inv.parse_method = "ofd-text"
    if not text.strip():
        inv.add_issue("OFD 内未提取到文字节点，可能是纯图形 OFD")
    return inv
