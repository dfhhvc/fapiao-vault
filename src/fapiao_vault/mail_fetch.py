"""邮件抓取：从 IMAP 邮箱下载发票附件（仅标准库，零第三方依赖）。

很多报销人的发票入口是邮箱——开票方/平台把电子发票 PDF 发到邮箱，
手工逐封下载既繁琐又容易漏。本模块实现「邮箱 → 附件 → 本地目录」，
下载完成后接 `fapiao scan` 即可完成整理、查重与报表。

与项目核心承诺的关系（必须说清楚）：
- `fapiao scan` 永不联网，发票文件与解析结果全部留在本机；
- `fapiao fetch` 是【可选】功能，只连接用户自己指定的 IMAP 服务器
  拉取附件，授权码仅在进程内存中使用，不落盘、不上传任何发票数据。

安全设计：
- 授权码不进 argv（避免 shell 历史泄漏）：环境变量或交互式输入；
- 只读连接（readonly=True）：不改变邮件的已读状态；
- 只保存 .pdf / .ofd / .xml 附件（发票格式），其他附件一概不碰；
- 重复下载保护：同名同大小视为已下载直接跳过，同名不同大小自动加序号。

常见邮箱 IMAP 设置（端口均为 993/SSL）：
- QQ 邮箱   imap.qq.com   （设置-账户：开启 IMAP，使用「授权码」登录）
- 163 邮箱  imap.163.com  （设置-POP3/IMAP：开启服务，使用「授权密码」）
- 126 邮箱  imap.126.com  （同 163）
- Gmail     imap.gmail.com（需两步验证 + 应用专用密码）
- Outlook   outlook.office365.com
"""

from __future__ import annotations

import imaplib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from pathlib import Path

# 只保存发票类附件；图片/扫描件发票需要 OCR，本工具明确不做
ATTACH_EXTS = (".pdf", ".ofd", ".xml")

# 邮件附件没有文件名时，按 content-type 兜底生成扩展名
_CT_EXTS = {
    "application/pdf": ".pdf",
    "application/ofd": ".ofd",
    "application/xml": ".xml",
    "text/xml": ".xml",
}

# IMAP 检索的月份缩写必须是英文；不能直接用 %b（受系统 locale 影响）
_IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_NAME_CHARS = re.compile(r'[/\\<>:"|?*]')


class MailFetchError(Exception):
    """邮件抓取失败（连接/登录/协议错误）。message 面向最终用户，可直接展示。"""


@dataclass
class FetchResult:
    """一次抓取的统计结果。"""

    matched: int = 0  # SEARCH 命中的邮件数
    processed: int = 0  # 实际取回解析的邮件数（受 limit 限制）
    saved: list[str] = field(default_factory=list)  # 新下载的文件路径
    skipped: int = 0  # 已存在（同名同大小）而跳过的附件数
    non_invoice: int = 0  # 没有发票附件的邮件数
    errors: list[str] = field(default_factory=list)  # 单封邮件级错误（不中断整批）


def imap_since_date(days: int, today: date | None = None) -> str:
    """把「最近 N 天」换算成 IMAP SEARCH 使用的日期（DD-Mon-YYYY，英文月份）。"""
    d = (today or date.today()) - timedelta(days=max(0, days))
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _decode_mime_words(value: str) -> str:
    """解码 RFC 2047 编码词（如 =?utf-8?B?...?=），用于中文附件名/主题。"""
    if not value:
        return ""
    try:
        chunks = decode_header(value)
    except Exception:  # 编码损坏的邮件头：保底返回原文
        return value
    out: list[str] = []
    for data, enc in chunks:
        if isinstance(data, bytes):
            out.append(data.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(data)
    return "".join(out)


def _sanitize_filename(name: str) -> str:
    """把附件名清洗成本地文件系统安全的名字。"""
    name = _CTRL_CHARS.sub("", name)
    name = _UNSAFE_NAME_CHARS.sub("_", name).strip(" .")
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem[:140]}{dot}{ext}" if dot else name[:150]
    return name or "未命名附件"


def iter_invoice_attachments(msg: Message) -> list[tuple[str, bytes]]:
    """从一封邮件中提取发票类附件，返回 (文件名, 内容) 列表。

    email.walk() 会递归进入 multipart 与内嵌 message/rfc822，
    因此「转发邮件里再套一封发票邮件」也能被提取到。
    """
    out: list[tuple[str, bytes]] = []
    fallback_index = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        raw_name = part.get_filename()
        if raw_name:
            filename = _sanitize_filename(_decode_mime_words(raw_name))
        else:
            ext = _CT_EXTS.get(part.get_content_type())
            if not ext:
                continue
            fallback_index += 1
            filename = f"附件{fallback_index}{ext}"
        ext = Path(filename).suffix.lower()
        if ext not in ATTACH_EXTS:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # 个别损坏的编码段不拖垮整封邮件
            payload = None
        if payload:
            out.append((filename, payload))
    return out


def _unique_path(target: Path) -> Path:
    """同名文件已存在时生成不冲突的路径（发票_2.pdf、发票_3.pdf ...）。"""
    for i in range(2, 10000):
        cand = target.with_name(f"{target.stem}_{i}{target.suffix}")
        if not cand.exists():
            return cand
    return target.with_name(f"{target.stem}_{uuid.uuid4().hex[:8]}{target.suffix}")


def save_attachments(attaches: list[tuple[str, bytes]], dest_dir: Path) -> tuple[list[str], int]:
    """把附件写入目录，返回 (新保存的路径列表, 跳过数)。

    重复下载保护：同名同大小视为已下载，跳过；同名不同大小加序号保存，
    避免两张不同的票因为邮件附件同名而互相覆盖。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped = 0
    for filename, payload in attaches:
        target = dest_dir / filename
        if target.exists() and target.stat().st_size == len(payload):
            skipped += 1
            continue
        if target.exists():
            target = _unique_path(target)
        target.write_bytes(payload)
        saved.append(str(target))
    return saved, skipped


def fetch_invoices(
    host: str,
    user: str,
    password: str,
    dest_dir: Path,
    *,
    port: int = 993,
    use_ssl: bool = True,
    folder: str = "INBOX",
    since_days: int = 90,
    limit: int = 0,
    timeout: int = 30,
    on_progress: Callable[[int, int], None] | None = None,
) -> FetchResult:
    """连接 IMAP 邮箱，把最近 N 天邮件中的发票附件下载到 dest_dir。

    只读连接、只下载发票类附件；单封邮件失败记录进 result.errors，
    不中断整批。连接/登录失败抛 MailFetchError。
    """
    result = FetchResult()
    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailFetchError(
            f"无法连接 {host}:{port}（{exc}）。请检查服务器地址与端口，或确认网络可达。"
        ) from exc

    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise MailFetchError(
                f"登录失败（{exc}）。多数国内邮箱需要「授权码」而非网页登录密码："
                "QQ 邮箱在 设置-账户 开启 IMAP 并生成授权码；"
                "163/126 在 设置-POP3/IMAP 开启服务并获取授权密码。"
            ) from exc

        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            raise MailFetchError(f"无法打开邮箱文件夹「{folder}」，请确认名称（通常是 INBOX）。")

        status, data = conn.search(None, "SINCE", imap_since_date(since_days))
        if status != "OK" or not data or not data[0]:
            raise MailFetchError("邮件检索失败（服务器返回异常）。")
        nums = data[0].split()
        result.matched = len(nums)
        if limit > 0:
            nums = nums[-limit:]  # 只取最近的 limit 封

        for num in nums:
            result.processed += 1
            if on_progress:
                on_progress(result.processed, len(nums))
            try:
                status, msg_data = conn.fetch(num, "(RFC822)")
            except (imaplib.IMAP4.error, OSError) as exc:
                result.errors.append(f"邮件 {num!r} 拉取失败：{exc}")
                continue
            raw = None
            for item in msg_data or ():
                if isinstance(item, tuple) and len(item) >= 2:
                    raw = item[1]
                    break
            if raw is None:
                result.errors.append(f"邮件 {num!r} 内容为空，已跳过")
                continue
            try:
                msg = message_from_bytes(raw)
            except Exception as exc:  # 极端损坏的邮件
                result.errors.append(f"邮件 {num!r} 解析失败：{exc}")
                continue

            attaches = iter_invoice_attachments(msg)
            if not attaches:
                result.non_invoice += 1
                continue
            saved, skipped = save_attachments(attaches, dest_dir)
            result.saved.extend(saved)
            result.skipped += skipped
    except MailFetchError:
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailFetchError(f"邮箱会话中断（{exc}）。") from exc
    finally:
        # 不用 `with conn:`：其 __exit__ 调用的 logout 若失败会顶掉业务异常。
        # shutdown() 只关 socket，登录失败/已登出的状态下也安全。
        try:
            conn.shutdown()
        except Exception:
            pass

    return result
