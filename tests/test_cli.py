"""命令行入口测试。"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import make_fixtures

from fapiao_vault.cli import build_parser, cmd_fetch, main

try:
    import pypdf  # noqa: F401

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

# 归档数量与跨期重号断言依赖 PDF 样品被解析：无 pypdf 时跳过（与
# test_write_xlsx_or_skip 对 openpyxl 的处理一致），CI core-only job 守护零依赖承诺。
requires_pypdf = unittest.skipUnless(
    HAS_PYPDF, '需要可选依赖 pypdf（pip install "fapiao-vault[pdf]"）'
)


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_fixtures.main(str(cls.dir), quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_version_flag(self):
        from fapiao_vault import __version__

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_no_args_prints_help(self):
        code, out = run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("scan", out)

    def test_scan_creates_reports(self):
        with tempfile.TemporaryDirectory() as out_dir:
            code, out = run_cli(["scan", str(self.dir), "--out", out_dir, "--no-auto-history"])
            self.assertEqual(code, 0)
            self.assertIn("识别成功", out)
            for name in ("台账.json", "发票明细.csv", "发票报告.html"):
                self.assertTrue((Path(out_dir) / name).exists(), name)

    def test_scan_reports_duplicates(self):
        with tempfile.TemporaryDirectory() as out_dir:
            _, out = run_cli(["scan", str(self.dir), "--out", out_dir, "--no-auto-history"])
            self.assertIn("重复发票", out)

    @requires_pypdf
    def test_scan_with_archive(self):
        with tempfile.TemporaryDirectory() as out_dir, tempfile.TemporaryDirectory() as arch:
            code, out = run_cli(
                [
                    "scan",
                    str(self.dir),
                    "--out",
                    out_dir,
                    "--archive",
                    "--archive-dir",
                    arch,
                    "--no-auto-history",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("已归档 5 个文件", out)
            self.assertTrue(list(Path(arch).rglob("*.pdf")))

    @requires_pypdf
    def test_scan_with_cross_period_history(self):
        # 历史台账月份不同 → CLI 必须把跨期重号显示出来（历史版本算完不显示，静默失效）
        with tempfile.TemporaryDirectory() as out_dir, tempfile.TemporaryDirectory() as hd:
            hist = Path(hd) / "h.json"
            hist.write_text(
                json.dumps({"rows": [{"uid": "25117000000000000001", "month": "2026-08"}]}),
                encoding="utf-8",
            )
            code, out = run_cli(["scan", str(self.dir), "--out", out_dir, "--history", str(hist)])
            self.assertEqual(code, 0)
            self.assertIn("与历史台账重号", out)
            self.assertIn("2026-08", out)

    def test_scan_nonexistent_dir(self):
        code, out = run_cli(["scan", "/definitely/not/a/dir"])
        self.assertEqual(code, 2)
        self.assertIn("目录不存在", out)

    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run_cli(["scan", d])
            self.assertEqual(code, 0)
            self.assertIn("没有找到", out)

    def test_verify_ok(self):
        code, out = run_cli(["verify", make_fixtures.BUYER_TAX])
        self.assertEqual(code, 0)
        self.assertIn("通过", out)

    def test_verify_bad(self):
        code, out = run_cli(["verify", "91330100MA2XXXXXX3"])
        self.assertEqual(code, 1)
        self.assertIn("不通过", out)

    def test_version_subcommand(self):
        code, out = run_cli(["version"])
        self.assertEqual(code, 0)
        self.assertIn("fapiao-vault", out)


class TestFetchCli(unittest.TestCase):
    """fetch 子命令的本地测试：只测参数接线与失败前置，绝不联网。"""

    def test_fetch_parser_defaults_and_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            ["fetch", "--host", "imap.qq.com", "--user", "a@qq.com", "--dir", "./收件箱"]
        )
        self.assertIs(args.func, cmd_fetch)
        self.assertTrue(args.ssl)
        self.assertEqual(args.port, 993)
        self.assertEqual(args.folder, "INBOX")
        self.assertEqual(args.since, 90)
        self.assertEqual(args.limit, 0)

        args2 = parser.parse_args(
            [
                "fetch",
                "--host",
                "h",
                "--user",
                "u",
                "--dir",
                "d",
                "--no-ssl",
                "--since",
                "30",
                "--limit",
                "5",
                "--folder",
                "Sent",
            ]
        )
        self.assertFalse(args2.ssl)
        self.assertEqual(args2.since, 30)
        self.assertEqual(args2.limit, 5)
        self.assertEqual(args2.folder, "Sent")

    def test_fetch_missing_password_env_fails_before_network(self):
        # 授权码环境变量未设置时，必须在发起任何网络连接之前就失败
        env_name = "FAPIAO_VAULT_TEST_MISSING_PW"
        os.environ.pop(env_name, None)
        with tempfile.TemporaryDirectory() as d:
            code, out = run_cli(
                [
                    "fetch",
                    "--host",
                    "imap.example.invalid",
                    "--user",
                    "a@b.c",
                    "--dir",
                    d,
                    "--password-env",
                    env_name,
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("未设置或为空", out)


if __name__ == "__main__":
    unittest.main()
