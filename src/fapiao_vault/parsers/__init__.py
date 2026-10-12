"""解析器调度：按扩展名选择解析器，并统一做校验与置信度评估。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import Invoice
from ..validate import check_arithmetic, validate_invoice_number, validate_invoice_tax_id
from .ofd_parser import parse_ofd
from .pdf_parser import parse_pdf
from .xml_parser import parse_xml

# 支持的扩展名 → 解析器
SUPPORTED_EXTS = {
    ".pdf": parse_pdf,
    ".ofd": parse_ofd,
    ".xml": parse_xml,
    # 图片发票需要 OCR，本工具刻意不做（离线优先、确定性优先）；
    # 这类文件会被识别出来并提示用户单独处理。
    ".jpg": None,
    ".jpeg": None,
    ".png": None,
    ".bmp": None,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def enrich(inv: Invoice) -> Invoice:
    """对解析结果做统一校验，并给出置信度。"""
    if inv.invoice_number or inv.invoice_code:
        ok, msg = validate_invoice_number(inv.invoice_code, inv.invoice_number)
        if not ok:
            inv.add_issue(f"发票号码异常：{msg}")

    for issue in validate_invoice_tax_id(inv):
        inv.add_issue(issue)
    for issue in check_arithmetic(inv):
        inv.add_issue(issue)

    if not inv.invoice_number and inv.total is None and not inv.issues:
        inv.add_issue("未解析到任何发票字段，可能不是发票文件（或为纯图片/扫描件）")

    inv.recompute_confidence()
    return inv


def parse_file(path: Path | str) -> Invoice:
    """解析单个文件，返回已校验的 Invoice。"""
    p = Path(path)
    ext = p.suffix.lower()

    if ext in IMAGE_EXTS:
        inv = Invoice(source_file=str(p), file_format=ext.lstrip("."))
        inv.parse_method = "skipped-image"
        inv.add_issue(
            "图片发票不做 OCR（本工具只处理自带文本层的电子发票），请改用原始 PDF/OFD/XML"
        )
        inv.recompute_confidence()
        return inv

    parser = SUPPORTED_EXTS.get(ext)
    if parser is None:
        inv = Invoice(source_file=str(p), file_format=ext.lstrip(".") or "unknown")
        inv.parse_method = "unsupported"
        inv.add_issue(f"不支持的文件类型：{ext or '无扩展名'}")
        inv.recompute_confidence()
        return inv

    inv = parser(p)
    inv.file_sha256 = file_sha256(p)
    return enrich(inv)
