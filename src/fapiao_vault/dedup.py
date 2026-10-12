"""查重引擎：识别同一张发票被重复收集、甚至被重复报销。

报销场景里最危险的财务问题是"同一张发票报了两次"——轻则退单，重则算违规。
本模块做两层判定：
1. 同一批内重复（一次扫描里发现同一张票的多个副本）
2. 跨期重复（与历史台账比对，票号相同但月份不同 → 高风险）
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .models import DuplicateGroup, Invoice


def _number_key(inv: Invoice) -> str:
    if inv.invoice_code:
        return f"{inv.invoice_code}-{inv.invoice_number}"
    return inv.invoice_number


def _content_key(inv: Invoice) -> str:
    """无发票号码时的内容键：开票日期 + 价税合计 + 销售方。"""
    return "|".join(
        [
            inv.invoice_date or "?",
            str(inv.total) if inv.total is not None else "?",
            inv.seller_name or "?",
        ]
    )


def group_duplicates(invoices: list[Invoice]) -> list[DuplicateGroup]:
    """在一批发票内部找出重复项。

    三级判定，按可靠性递减，且互不重复计数：
    1. 发票号码相同 —— 最可靠
    2. 文件内容指纹相同 —— 同一文件被重复收集
    3. 无号码时用「开票日期+价税合计+销售方」—— 疑似重复

    注意：完全没有解析出内容的文件（既无号码也无金额）不参与第 3 级判定，
    否则一堆"解析失败"的文件会被误报成互相重复。
    """
    groups: list[DuplicateGroup] = []
    covered: set[int] = set()

    # ---- 第 1 级：发票号码
    by_number: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        if inv.invoice_number:
            by_number[_number_key(inv)].append(inv)

    for key, items in by_number.items():
        if len(items) < 2:
            continue
        months = {i.month for i in items if i.month != "unknown"}
        if len(months) > 1:
            reason = (
                f"同一票号出现在 {len(months)} 个不同月份"
                f"（{', '.join(sorted(months))}），存在重复报销风险"
            )
            severity = "high"
        else:
            reason = "同一张发票出现多次（重复收集或重复下载）"
            severity = "medium"
        groups.append(DuplicateGroup(uid=key, invoices=items, reason=reason, severity=severity))
        covered.update(id(i) for i in items)

    # ---- 第 2 级：文件内容指纹
    by_hash: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        if inv.file_sha256 and id(inv) not in covered:
            by_hash[inv.file_sha256].append(inv)

    for h, items in by_hash.items():
        if len(items) < 2:
            continue
        groups.append(
            DuplicateGroup(
                uid=f"FILE:{h[:12]}",
                invoices=items,
                reason="文件内容完全一致（同一文件被重复收集）",
                severity="medium",
            )
        )
        covered.update(id(i) for i in items)

    # ---- 第 3 级：内容键兜底（必须有金额，否则视为解析失败不参与）
    by_content: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        if inv.invoice_number or inv.total is None or id(inv) in covered:
            continue
        by_content[_content_key(inv)].append(inv)

    for key, items in by_content.items():
        if len(items) < 2:
            continue
        groups.append(
            DuplicateGroup(
                uid=f"CONTENT:{key}",
                invoices=items,
                reason="无发票号码，按「开票日期+价税合计+销售方」判定疑似重复",
                severity="medium",
            )
        )
        covered.update(id(i) for i in items)

    groups.sort(key=lambda g: (g.severity != "high", g.uid))
    return groups


def load_history(path: Path) -> dict[str, str]:
    """读取历史台账（此前扫描导出过的 json），返回 {uid: 归属月份}。

    兼容三种结构，第一种是本工具 write_json 实际产出的格式：
    1. 顶层 dict，记录在 "rows" 键下（每条含 uid / month）
    2. 顶层 dict，记录在 "invoices" 键下（旧版约定）
    3. 纯列表

    历史版本曾只匹配第 2/3 种结构，导致工具读不回自己写出的台账，
    跨期查重静默失效——现在三种都支持，并有回归测试锁定（见
    tests/test_archive_report.py::test_history_written_by_tool_can_be_loaded）。
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        records = data.get("rows") or data.get("invoices") or []
    elif isinstance(data, list):
        records = data
    else:
        return {}
    out: dict[str, str] = {}
    for rec in records:
        if isinstance(rec, dict) and rec.get("uid"):
            out[str(rec["uid"])] = str(rec.get("month") or "unknown")
    return out


def cross_period_duplicates(
    invoices: list[Invoice], history: dict[str, str]
) -> list[tuple[Invoice, str]]:
    """找出与历史台账重号、且无法确认属于同一月份的发票（重复报销高风险）。

    判定规则：
    - 台账里没有该票号 → 不算（正常新增）
    - 票号在台账中、且月份相同 → 不算（同一报销期内重扫，属正常操作）
    - 票号在台账中、但月份不同 → 命中（同一张票出现在两个报销期）
    - 月份信息缺失（任一侧为 unknown）→ 命中（无法自证同期，交人工确认）

    返回 [(发票, 台账中的月份), ...]。
    """
    out: list[tuple[Invoice, str]] = []
    for inv in invoices:
        history_month = history.get(inv.uid)
        if history_month is None:
            continue
        if inv.month == history_month:
            continue
        out.append((inv, history_month))
    return out
