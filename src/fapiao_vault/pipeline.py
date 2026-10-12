"""扫描流水线：遍历目录 → 解析 → 查重 → 汇总。

这一层是纯函数式的数据流，便于单元测试与在 CLI / 库两种用法间复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .dedup import group_duplicates, cross_period_duplicates, load_history
from .models import DuplicateGroup, Invoice
from .parsers import SUPPORTED_EXTS, IMAGE_EXTS, parse_file
from .validate import check_buyer

# 需要跳过的目录（避免把归档产物又扫一遍，造成重复统计）
DEFAULT_EXCLUDES = {"_归档", "_报表", ".git", "__pycache__", "node_modules", "_archive", "_reports"}


@dataclass
class ScanResult:
    invoices: list[Invoice] = field(default_factory=list)
    duplicates: list[DuplicateGroup] = field(default_factory=list)
    cross_period: list[Invoice] = field(default_factory=list)
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    scanned_count: int = 0

    @property
    def ok_invoices(self) -> list[Invoice]:
        return [i for i in self.invoices if i.invoice_number or i.total is not None]

    @property
    def failed_invoices(self) -> list[Invoice]:
        return [i for i in self.invoices if not (i.invoice_number or i.total is not None)]


def iter_invoice_files(root: Path, recursive: bool = True, excludes: set[str] | None = None):
    """产出候选发票文件路径。"""
    excludes = excludes if excludes is not None else DEFAULT_EXCLUDES
    exts = set(SUPPORTED_EXTS) | IMAGE_EXTS
    if recursive:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(part in excludes for part in p.parts):
                continue
            if p.suffix.lower() in exts:
                yield p
    else:
        for p in sorted(root.iterdir()):
            if p.is_file() and p.suffix.lower() in exts:
                yield p


def scan_directory(
    root: Path | str,
    recursive: bool = True,
    history_path: Path | None = None,
    expected_names: list[str] | None = None,
    expected_tax_ids: list[str] | None = None,
) -> ScanResult:
    """扫描目录，返回完整结果。"""
    root = Path(root)
    result = ScanResult()
    history_uids = load_history(history_path) if history_path else set()

    for path in iter_invoice_files(root, recursive=recursive):
        result.scanned_count += 1
        inv = parse_file(path)

        if expected_names or expected_tax_ids:
            for issue in check_buyer(
                inv.buyer_name,
                inv.buyer_tax_id,
                expected_names or [],
                expected_tax_ids or [],
            ):
                inv.add_issue(issue)
            inv.recompute_confidence()

        if inv.parse_method in ("unsupported", "skipped-image") or not (
            inv.invoice_number or inv.total is not None
        ):
            result.skipped_files.append((str(path), "；".join(inv.issues) or "未识别到发票字段"))

        result.invoices.append(inv)

    result.duplicates = group_duplicates(result.invoices)
    if history_uids:
        result.cross_period = cross_period_duplicates(result.invoices, history_uids)
    return result
