"""归档：按月份归类、按规则重命名，生成一份整齐可查的发票库。

只做「复制」不做「移动」，避免误操作导致原始发票丢失。
文件重名时自动追加序号，不覆盖任何已有文件。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .models import Invoice

# Windows / macOS / Linux 通用的非法文件名字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_MULTI_US = re.compile(r"_+")


def sanitize(name: str, max_len: int = 60) -> str:
    """把任意字符串变成安全的文件名片段。"""
    s = (name or "").strip()
    s = _ILLEGAL.sub("", s)
    s = s.replace(" ", "")
    s = _MULTI_US.sub("_", s)
    s = s.strip("._")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "未知"


def build_filename(inv: Invoice, style: str = "date_seller_total") -> str:
    """生成归档文件名（不含扩展名）。"""
    date = inv.invoice_date or "0000-00-00"
    seller = sanitize(inv.seller_name, 30)
    total = str(inv.total) if inv.total is not None else "0.00"
    number = inv.invoice_number or ""
    kind = sanitize(inv.invoice_type.replace("电子发票（", "").replace("）", ""), 12)

    if style == "date_seller_total":
        parts = [date, seller, f"{total}元"]
    elif style == "date_total_seller":
        parts = [date, f"{total}元", seller]
    elif style == "number_first":
        parts = [number or date, seller, f"{total}元"]
    else:
        parts = [date, seller, f"{total}元"]

    if number:
        parts.append(number)
    if kind:
        parts.append(kind)
    return "_".join(p for p in parts if p)


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """在目录下找一个不冲突的文件名。"""
    candidate = directory / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    for i in range(2, 10000):
        candidate = directory / f"{stem}_{i}{ext}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为 {stem} 找到可用文件名")


def archive_invoices(
    invoices: list[Invoice],
    out_dir: Path | str,
    style: str = "date_seller_total",
    group_by_month: bool = True,
) -> tuple[int, list[str]]:
    """把发票复制归档到 out_dir。

    返回 (成功数量, 失败信息列表)。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    errors: list[str] = []

    for inv in invoices:
        if not inv.source_file:
            continue
        src = Path(inv.source_file)
        if not src.exists():
            errors.append(f"源文件不存在：{src}")
            continue

        target_dir = out_dir / inv.month if group_by_month else out_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        stem = build_filename(inv, style=style)
        ext = src.suffix.lower()
        try:
            dest = unique_path(target_dir, stem, ext)
            shutil.copy2(src, dest)
            done += 1
        except OSError as exc:
            errors.append(f"归档 {src.name} 失败：{exc}")

    return done, errors
