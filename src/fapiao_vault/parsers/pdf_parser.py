"""PDF 发票解析：读取 PDF 文本层。

数电票与增值税电子发票的 PDF 都是"文本型 PDF"，字面即数据，
所以只需把文本层抽出来交给 fields 模块做规则匹配。

注意：本模块只使用 pypdf 做文本抽取，不渲染、不上传、不写回文件。
"""

from __future__ import annotations

from pathlib import Path

from ..fields import extract_from_text
from ..models import Invoice


def _read_text(path: Path) -> tuple[str, str]:
    """返回 (文本, 错误信息)。"""
    try:
        from pypdf import PdfReader  # 延迟导入：未安装 pypdf 时其他格式仍可用
    except ImportError:
        return "", "未安装 pypdf，无法解析 PDF（pip install pypdf）。OFD / XML 格式仍可正常使用"
    try:
        reader = PdfReader(str(path))
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                # 单页抽取失败不应让整张发票作废，补空串继续
                chunks.append("")
        return "\n".join(chunks), ""
    except Exception as exc:
        return "", f"PDF 读取失败：{exc}"


def parse_pdf(path: Path) -> Invoice:
    text, err = _read_text(path)
    inv = extract_from_text(text, source_file=str(path), file_format="pdf")
    inv.parse_method = "pdf-text-layer"
    if err:
        inv.add_issue(err)
    if not text.strip():
        inv.add_issue("PDF 未包含文本层，可能是扫描件/图片发票（本工具不做 OCR）")
    return inv
