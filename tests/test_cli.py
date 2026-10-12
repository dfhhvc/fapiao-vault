"""命令行入口测试。"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fapiao_vault.cli import main
from tools import make_fixtures


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

    def test_scan_with_archive(self):
        with tempfile.TemporaryDirectory() as out_dir, tempfile.TemporaryDirectory() as arch:
            code, out = run_cli(
                [
                    "scan", str(self.dir),
                    "--out", out_dir,
                    "--archive", "--archive-dir", arch,
                    "--no-auto-history",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("已归档 5 个文件", out)
            self.assertTrue(list(Path(arch).rglob("*.pdf")))

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


if __name__ == "__main__":
    unittest.main()
