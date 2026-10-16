"""命令行入口。

用法：
    fapiao scan  <目录>           扫描并生成报表
    fapiao scan  <目录> --archive 扫描 + 归档重命名
    fapiao fetch --host ...       从 IMAP 邮箱抓取发票附件（可选联网功能）
    fapiao verify <统一社会信用代码>  校验税号
    fapiao version
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from . import __version__
from .archive import archive_invoices
from .mail_fetch import MailFetchError, fetch_invoices
from .pipeline import scan_directory
from .report import build_summary, write_csv, write_html, write_json, write_xlsx
from .validate import validate_tax_id

# ---------------------------------------------------------------- 终端着色

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:
    return _c(t, "1")


def green(t: str) -> str:
    return _c(t, "32")


def yellow(t: str) -> str:
    return _c(t, "33")


def red(t: str) -> str:
    return _c(t, "31")


def dim(t: str) -> str:
    return _c(t, "90")


def _rule(width: int = 62) -> str:
    return dim("─" * width)


# ---------------------------------------------------------------- scan


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.directory).expanduser().resolve()
    if not root.exists():
        print(red(f"目录不存在：{root}"))
        return 2
    if not root.is_dir():
        print(red(f"不是目录：{root}"))
        return 2

    history = Path(args.history).expanduser().resolve() if args.history else None
    if history is None:
        default_history = root / "_报表" / "台账.json"
        if default_history.exists() and not args.no_auto_history:
            history = default_history

    print(bold("FapiaoVault 发票扫描"))
    print(dim(f"  目录：{root}"))
    print(dim(f"  递归：{'是' if args.recursive else '否'}"))
    if history:
        print(dim(f"  历史台账：{history}"))
    print(_rule())

    result = scan_directory(
        root,
        recursive=args.recursive,
        history_path=history,
        expected_names=args.company or [],
        expected_tax_ids=args.tax_id or [],
    )

    if result.scanned_count == 0:
        print(yellow("没有找到可解析的文件（支持 .pdf / .ofd / .xml）。"))
        print(dim("提示：图片/扫描件发票需要 OCR，本工具不做，请改用原始电子发票文件。"))
        return 0

    summary = build_summary(result)
    t = summary["totals"]

    print(f"  扫描文件        {t['scanned']} 个")
    print(f"  识别成功        {green(str(t['ok']))} 张")
    if t["failed"]:
        print(f"  未识别          {yellow(str(t['failed']))} 个")
    print(f"  价税合计        {bold('¥' + t['total'])}")
    print(f"  税额合计        ¥{t['tax']}")
    if t["duplicate_groups"]:
        risk = red(f"，其中高风险 {t['high_risk']} 组") if t["high_risk"] else ""
        print(f"  重复发票        {yellow(str(t['duplicate_groups']))} 组{risk}")
    else:
        print(f"  重复发票        {green('无')}")

    # ---- 重复明细
    if result.duplicates:
        print()
        print(bold("重复发票明细"))
        for g in result.duplicates[:20]:
            tag = red("[高风险]") if g.severity == "high" else yellow("[提示]")
            print(f"  {tag} {g.uid}  {g.reason}")
            for inv in g.invoices:
                print(dim(f"        - {Path(inv.source_file).name}"))

    # ---- 与历史台账重号（跨期）
    if result.cross_period:
        print()
        print(bold("与历史台账重号（跨期，疑似重复报销）"))
        for inv, history_month in result.cross_period[:20]:
            print(
                f"  {red('!')} {inv.invoice_number or inv.uid}"
                f"  台账月份 {history_month} / 本次 {inv.month}"
            )
            print(dim(f"        - {Path(inv.source_file).name}"))

    # ---- 问题清单
    problem_invoices = [i for i in result.invoices if i.issues]
    if problem_invoices:
        print()
        print(bold(f"需人工核对（{len(problem_invoices)}）"))
        for inv in problem_invoices[:20]:
            print(f"  {yellow('!')} {Path(inv.source_file).name}")
            for issue in inv.issues:
                print(dim(f"        {issue}"))
        if len(problem_invoices) > 20:
            print(dim(f"        ...另有 {len(problem_invoices) - 20} 条，详见报表"))

    # ---- 输出报表
    out_dir = Path(args.out).expanduser().resolve() if args.out else root / "_报表"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = write_json(summary, out_dir / "台账.json")
    csv_path = write_csv(summary, out_dir / "发票明细.csv")
    html_path = write_html(summary, out_dir / "发票报告.html")
    xlsx_path = write_xlsx(summary, out_dir / "发票汇总.xlsx")

    print()
    print(bold("已生成报表"))
    print(f"  HTML 报告   {html_path}")
    print(f"  Excel 汇总  {xlsx_path if xlsx_path else dim('（未安装 openpyxl，已跳过）')}")
    print(f"  CSV 明细    {csv_path}")
    print(f"  JSON 台账   {json_path}")

    # ---- 归档
    if args.archive:
        archive_dir = (
            Path(args.archive_dir).expanduser().resolve() if args.archive_dir else root / "_归档"
        )
        done, errors = archive_invoices(
            result.ok_invoices, archive_dir, style=args.style, group_by_month=True
        )
        print()
        print(bold(f"已归档 {done} 个文件 → {archive_dir}"))
        for e in errors[:10]:
            print(red(f"  {e}"))

    if args.open_report and sys.platform.startswith("win"):
        try:
            os.startfile(str(html_path))  # type: ignore[attr-defined]
        except OSError:
            pass

    return 0


# ---------------------------------------------------------------- fetch


def cmd_fetch(args: argparse.Namespace) -> int:
    dest = Path(args.dir).expanduser().resolve()
    if args.password_env:
        password = os.environ.get(args.password_env, "")
        if not password:
            print(red(f"环境变量 {args.password_env} 未设置或为空"))
            return 2
    else:
        password = getpass.getpass("邮箱授权码（输入不回显）：")

    print(bold("FapiaoVault 邮件抓取（IMAP）"))
    print(
        dim(f"  服务器  {args.host}:{args.port}{'（SSL）' if args.ssl else '（无 SSL，不建议）'}")
    )
    print(dim(f"  账户    {args.user}"))
    scope = f"  范围    {args.folder} 最近 {args.since} 天"
    if args.limit:
        scope += f"，最多 {args.limit} 封"
    print(dim(scope))
    print(dim(f"  保存到  {dest}"))
    print(_rule())

    def on_progress(done: int, total: int) -> None:
        print(f"\r  已处理 {done}/{total} 封", end="", flush=True)

    try:
        result = fetch_invoices(
            args.host,
            args.user,
            password,
            dest,
            port=args.port,
            use_ssl=args.ssl,
            folder=args.folder,
            since_days=args.since,
            limit=args.limit,
            on_progress=on_progress,
        )
    except MailFetchError as exc:
        print()
        print(red(f"抓取失败：{exc}"))
        return 1

    print()  # 结束进度行
    print()
    print(f"  命中邮件        {result.matched} 封（处理 {result.processed} 封）")
    print(f"  新下载附件      {green(str(len(result.saved)))} 个")
    print(f"  已存在跳过      {result.skipped} 个")
    print(f"  无发票附件      {result.non_invoice} 封")
    for path in result.saved[:10]:
        print(dim(f"        - {Path(path).name}"))
    if len(result.saved) > 10:
        print(dim(f"        ...另有 {len(result.saved) - 10} 个"))
    for err in result.errors[:10]:
        print(yellow(f"  ! {err}"))

    print()
    print(bold("下一步"))
    print(f"  整理查重  {green(f'fapiao scan {dest}')}")

    if result.errors:
        print(yellow(f"\n注意：{len(result.errors)} 封邮件处理失败，可重跑一次补抓。"))
        return 1
    return 0


# ---------------------------------------------------------------- verify


def cmd_verify(args: argparse.Namespace) -> int:
    code = args.code.strip()
    ok, msg = validate_tax_id(code)
    print(bold("税号校验"))
    print(dim(f"  输入：{code}"))
    print(f"  结果：{green('通过') if ok else red('不通过')}  {msg}")
    if len(code) == 18:
        from .validate import uscc_check_digit

        expected = uscc_check_digit(code[:17].upper())
        if expected and expected != code[17].upper():
            print(yellow(f"  提示：前 17 位 {code[:17].upper()} 对应的正确校验位应为 {expected}"))
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fapiao",
        description="FapiaoVault —— 纯本地电子发票整理、查重与报销汇总（不联网、不上传）",
    )
    parser.add_argument("--version", action="version", version=f"fapiao-vault {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="扫描目录中的发票并生成报表")
    p_scan.add_argument("directory", help="发票所在目录")
    p_scan.add_argument("--out", help="报表输出目录（默认 <目录>/_报表）")
    p_scan.add_argument(
        "--no-recursive", dest="recursive", action="store_false", help="不递归子目录"
    )
    p_scan.add_argument("--history", help="历史台账 JSON，用于检测跨期重复报销")
    p_scan.add_argument("--no-auto-history", action="store_true", help="不自动读取 _报表/台账.json")
    p_scan.add_argument(
        "--company", action="append", help="公司抬头（可重复指定），用于抬头合规校验"
    )
    p_scan.add_argument(
        "--tax-id", action="append", help="公司税号（可重复指定），用于抬头合规校验"
    )
    p_scan.add_argument("--archive", action="store_true", help="同时按月份归档并重命名")
    p_scan.add_argument("--archive-dir", help="归档目录（默认 <目录>/_归档）")
    p_scan.add_argument(
        "--style",
        default="date_seller_total",
        choices=["date_seller_total", "date_total_seller", "number_first"],
        help="归档文件名风格",
    )
    p_scan.add_argument("--open-report", action="store_true", help="完成后自动打开 HTML 报告")
    p_scan.set_defaults(func=cmd_scan)

    p_verify = sub.add_parser("verify", help="校验统一社会信用代码 / 纳税人识别号")
    p_verify.add_argument("code", help="18 位统一社会信用代码或 15 位旧税号")
    p_verify.set_defaults(func=cmd_verify)

    p_fetch = sub.add_parser("fetch", help="从 IMAP 邮箱抓取发票附件到本地（可选联网功能）")
    p_fetch.add_argument("--host", required=True, help="IMAP 服务器，如 imap.qq.com / imap.163.com")
    p_fetch.add_argument("--user", required=True, help="邮箱账户")
    p_fetch.add_argument("--dir", required=True, help="附件保存目录")
    p_fetch.add_argument("--port", type=int, default=993, help="服务器端口（默认 993）")
    p_fetch.add_argument(
        "--no-ssl", dest="ssl", action="store_false", help="不使用 SSL（默认全程 SSL）"
    )
    p_fetch.add_argument("--folder", default="INBOX", help="邮箱文件夹（默认 INBOX）")
    p_fetch.add_argument("--since", type=int, default=90, help="只抓最近 N 天的邮件（默认 90）")
    p_fetch.add_argument("--limit", type=int, default=0, help="最多处理 N 封邮件（0 = 不限）")
    p_fetch.add_argument(
        "--password-env",
        help="存放授权码的环境变量名（不指定则交互式输入，避免授权码进入 shell 历史）",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_version = sub.add_parser("version", help="显示版本")
    p_version.set_defaults(func=lambda a: (print(f"fapiao-vault {__version__}"), 0)[1])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
