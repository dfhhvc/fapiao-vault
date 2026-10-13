"""邮件抓取测试：全部在本地构造 MIME 消息，不联网、不碰真实邮箱。"""

import tempfile
import unittest
from datetime import date
from email import message_from_bytes
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from fapiao_vault.mail_fetch import (
    _decode_mime_words,
    _sanitize_filename,
    imap_since_date,
    iter_invoice_attachments,
    save_attachments,
)


def _make_pdf(content: bytes = b"%PDF-1.4 fake", name: str = "电子发票.pdf") -> MIMEApplication:
    pdf = MIMEApplication(content, _subtype="pdf")
    pdf.add_header("Content-Disposition", "attachment", filename=Header(name, "utf-8").encode())
    return pdf


def _roundtrip(msg):
    """序列化再解析，模拟真实邮件经过 SMTP 传输后的形态。"""
    return message_from_bytes(msg.as_bytes())


class TestIterInvoiceAttachments(unittest.TestCase):
    def test_extract_pdf_with_rfc2047_name(self):
        msg = MIMEMultipart()
        msg["Subject"] = Header("9月发票", "utf-8")
        msg.attach(MIMEText("请查收", "plain", "utf-8"))
        msg.attach(_make_pdf(b"%PDF-1.4 body", name="电子发票.pdf"))
        parsed = _roundtrip(msg)

        attaches = iter_invoice_attachments(parsed)
        self.assertEqual(len(attaches), 1)
        filename, payload = attaches[0]
        # RFC 2047 编码的中文附件名必须被正确解码
        self.assertEqual(filename, "电子发票.pdf")
        self.assertEqual(payload, b"%PDF-1.4 body")
        # 主题解码（用于日志展示）
        self.assertEqual(_decode_mime_words(str(parsed["Subject"])), "9月发票")

    def test_only_invoice_extensions_kept(self):
        msg = MIMEMultipart()
        msg.attach(_make_pdf(name="发票.PDF"))  # 大写扩展名也要认
        ofd = MIMEApplication(b"PK-ofd", _subtype="ofd")
        ofd.add_header("Content-Disposition", "attachment", filename="发票.ofd")
        msg.attach(ofd)
        xml = MIMEApplication(b"<Invoice/>", _subtype="xml")
        xml.add_header("Content-Disposition", "attachment", filename="发票.xml")
        msg.attach(xml)
        jpg = MIMEApplication(b"\xff\xd8jpg", _subtype="jpeg")
        jpg.add_header("Content-Disposition", "attachment", filename="照片.jpg")
        msg.attach(jpg)
        docx = MIMEApplication(b"docx-bytes", _subtype="octet-stream")
        docx.add_header("Content-Disposition", "attachment", filename="合同.docx")
        msg.attach(docx)

        names = [f for f, _ in iter_invoice_attachments(msg)]
        self.assertEqual(names, ["发票.PDF", "发票.ofd", "发票.xml"])

    def test_pdf_without_filename_uses_content_type(self):
        msg = MIMEMultipart()
        msg.attach(MIMEApplication(b"%PDF-1.4", _subtype="pdf"))  # 不设文件名
        attaches = iter_invoice_attachments(msg)
        self.assertEqual([f for f, _ in attaches], ["附件1.pdf"])

    def test_nested_forwarded_message(self):
        # 转发邮件里套一封发票邮件（message/rfc822），内层附件也要能提取
        inner = MIMEMultipart()
        inner["Subject"] = "内层发票"
        inner.attach(_make_pdf(b"%PDF-inner"))
        outer = MIMEMultipart()
        outer.attach(MIMEMessage(inner))
        attaches = iter_invoice_attachments(_roundtrip(outer))
        self.assertEqual([(f, p) for f, p in attaches], [("电子发票.pdf", b"%PDF-inner")])


class TestHelpers(unittest.TestCase):
    def test_sanitize_filename(self):
        # Windows 非法字符与路径分隔符必须被清洗
        self.assertEqual(_sanitize_filename("发票/抬头.pdf"), "发票_抬头.pdf")
        self.assertEqual(_sanitize_filename('x:y*z?"p<d>f|.pdf'), "x_y_z__p_d_f_.pdf")
        self.assertEqual(_sanitize_filename("普通发票.pdf"), "普通发票.pdf")
        self.assertEqual(_sanitize_filename("   "), "未命名附件")
        # 超长文件名截断但保留扩展名
        cleaned = _sanitize_filename("名" * 200 + ".pdf")
        self.assertLessEqual(len(cleaned), 145)
        self.assertTrue(cleaned.endswith(".pdf"))

    def test_imap_since_date_english_months(self):
        # IMAP 协议要求英文月份缩写，且不能受系统 locale 影响
        self.assertEqual(imap_since_date(0, date(2026, 1, 5)), "05-Jan-2026")
        self.assertEqual(imap_since_date(90, date(2026, 1, 5)), "07-Oct-2025")
        self.assertEqual(imap_since_date(1, date(2026, 3, 1)), "28-Feb-2026")


class TestSaveAttachments(unittest.TestCase):
    def test_dedupe_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            saved, skipped = save_attachments([("发票.pdf", b"AAA")], dest)
            self.assertEqual([Path(p).name for p in saved], ["发票.pdf"])
            self.assertEqual(skipped, 0)

            # 同名同大小：视为已下载，跳过
            saved, skipped = save_attachments([("发票.pdf", b"AAA")], dest)
            self.assertEqual(saved, [])
            self.assertEqual(skipped, 1)

            # 同名不同大小：是另一张票，加序号保存，不得覆盖
            saved, skipped = save_attachments([("发票.pdf", b"BBBB")], dest)
            self.assertEqual([Path(p).name for p in saved], ["发票_2.pdf"])
            self.assertEqual((dest / "发票.pdf").read_bytes(), b"AAA")
            self.assertEqual((dest / "发票_2.pdf").read_bytes(), b"BBBB")
            self.assertEqual(skipped, 0)


if __name__ == "__main__":
    unittest.main()
