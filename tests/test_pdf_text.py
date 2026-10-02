"""Page text extraction from untrusted PDFs."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from pypdf import PdfReader, PdfWriter

from desk import pdf_text
from desk.errors import DeskError
from desk.pdf_text import extract_pages
from tests.pdf_fixtures import make_pdf, paper_pdf


class PdfTextTest(unittest.TestCase):

  def assertRefused(self, code, data):
    with self.assertRaises(DeskError) as caught:
      extract_pages(data)
    self.assertEqual(caught.exception.code, code)
    return caught.exception

  def test_every_page_is_extracted_in_file_order(self):
    text = extract_pages(paper_pdf())
    self.assertEqual(len(text.pages), 3)
    self.assertIn("Fast Detectors for Edge Devices", text.pages[0])
    self.assertIn("50,000 CIFAR-10 training images", text.pages[1])
    self.assertIn("91.2% top-1 accuracy", text.pages[2])
    self.assertEqual(text.title, "Fast Detectors for Edge Devices")

  def test_pages_without_text_are_kept_in_place(self):
    text = extract_pages(make_pdf([["Introduction to the method"], None, ["Results section text"]]))
    self.assertEqual(len(text.pages), 3)
    self.assertEqual(text.pages[1].strip(), "")
    self.assertIsNone(text.title)

  def test_a_pdf_without_any_text_is_refused(self):
    error = self.assertRefused("no_text", make_pdf([None, None]))
    self.assertIn("OCR is not supported", error.message)

  def test_non_pdf_damaged_and_encrypted_files_are_refused(self):
    self.assertRefused("not_a_pdf", b"<html>not a paper</html>")
    self.assertRefused("unreadable_pdf", b"%PDF-1.4\n garbage without objects")
    self.assertRefused("unreadable_pdf", paper_pdf()[:200])
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(paper_pdf())))
    writer.encrypt("secret", algorithm="RC4-128")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    self.assertRefused("encrypted_pdf", encrypted.getvalue())

  def test_size_page_and_text_limits(self):
    with mock.patch.object(pdf_text, "MAX_PDF_BYTES", 100):
      self.assertRefused("too_large", paper_pdf())
    with mock.patch.object(pdf_text, "MAX_PAGES", 2):
      self.assertRefused("too_large", paper_pdf())
    with mock.patch.object(pdf_text, "MAX_TOTAL_CHARS", 50):
      self.assertRefused("too_large", paper_pdf())
    with mock.patch.object(pdf_text, "MAX_PAGE_CHARS", 10):
      self.assertEqual(max(len(page) for page in extract_pages(paper_pdf()).pages), 10)


if __name__ == "__main__":
  unittest.main()
