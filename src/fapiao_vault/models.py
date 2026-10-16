"""数据模型：统一表示一张发票。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

# 置信度：high = 关键字段齐全且通过校验；medium = 缺部分字段；low = 解析失败
CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"


@dataclass
class Invoice:
    """一张发票的结构化表示。

    金额统一用 Decimal，避免浮点误差导致报销金额对不上账。
    """

    source_file: str = ""
    file_format: str = ""  # pdf / ofd / xml
    file_sha256: str = ""  # 文件内容指纹，用于识别"同一张票被下载多次"

    # 票种与号码
    invoice_type: str = ""  # 数电票（电子发票）/ 增值税电子普通发票 / ...
    invoice_code: str = ""  # 发票代码（数电票无此项）
    invoice_number: str = ""  # 发票号码
    invoice_date: str = ""  # 开票日期 YYYY-MM-DD
    check_code: str = ""  # 校验码（部分普票有）

    # 购销双方
    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""

    # 金额
    amount: Decimal | None = None  # 金额（不含税）
    tax: Decimal | None = None  # 税额
    total: Decimal | None = None  # 价税合计
    total_in_words: str = ""  # 价税合计（大写）

    items: list[str] = field(default_factory=list)  # 项目名称

    # 解析元信息
    parse_method: str = ""  # text-layer / ofd-xml / invoice-xml
    confidence: str = CONF_LOW
    issues: list[str] = field(default_factory=list)  # 解析或校验中发现的问题

    # ---------- 派生属性 ----------

    @property
    def uid(self) -> str:
        """发票唯一标识，按可靠性从高到低回退：

        1. 发票代码 + 发票号码（最可靠）
        2. 开票日期 + 价税合计 + 销售方（号码缺失但有内容时）
        3. 文件内容指纹（完全没解析出内容时，至少能识别"同一文件"）
        """
        if self.invoice_number:
            if self.invoice_code:
                return f"{self.invoice_code}-{self.invoice_number}"
            return self.invoice_number
        if self.total is not None:
            return f"CONTENT:{self.invoice_date}|{self.total}|{self.seller_name}"
        return f"FILE:{self.file_sha256}"

    @property
    def has_amounts(self) -> bool:
        return self.total is not None or self.amount is not None

    @property
    def month(self) -> str:
        """归属月份 YYYY-MM，用于跨期重复检测与归档。"""
        return self.invoice_date[:7] if len(self.invoice_date) >= 7 else "unknown"

    def add_issue(self, msg: str) -> None:
        if msg not in self.issues:
            self.issues.append(msg)

    def recompute_confidence(self) -> None:
        """根据字段完整度给出置信度。"""
        if not self.invoice_number and not self.has_amounts:
            self.confidence = CONF_LOW
            return
        required_present = sum(
            1
            for v in (
                self.invoice_number,
                self.invoice_date,
                self.seller_name,
                self.total,
            )
            if v not in ("", None)
        )
        if required_present >= 4 and not self.issues:
            self.confidence = CONF_HIGH
        elif required_present >= 2:
            self.confidence = CONF_MEDIUM
        else:
            self.confidence = CONF_LOW

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("amount", "tax", "total"):
            d[k] = None if d[k] is None else str(d[k])
        d["uid"] = self.uid
        d["month"] = self.month
        d["confidence"] = self.confidence
        return d


@dataclass
class DuplicateGroup:
    """一组被判为重复的发票。"""

    uid: str
    invoices: list[Invoice]
    reason: str
    severity: str  # high = 跨期重复（有重复报销风险）；medium = 同批次重复
