"""Page text from an uploaded PDF, using pypdf.

pypdf is imported at module level, and ``desk.service`` imports this module
at module level on purpose: Möbius smoke-runs the service entry when it
builds the app's Python environment, so a missing or broken dependency fails
the Apply instead of the first tool call. Nothing else imports it eagerly,
because the comparison view builder runs without that environment.

Text is stored exactly as extracted; quotation matching normalizes it later
(see ``evidence``), so the stored page text stays the reference. PDFs are
untrusted input: size, page count and per-page text are bounded, encrypted
files are refused, and any parser failure becomes one safe refusal.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pypdf
from pypdf.errors import PyPdfError

from desk.errors import DeskError


MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 1000
MAX_PAGE_CHARS = 200_000
MAX_TOTAL_CHARS = 10_000_000
TITLE_MAX_CHARS = 300

EXTRACTOR = {"name": "pypdf", "version": pypdf.__version__}

# pypdf logs recoverable syntax problems; they belong in the service log
# (stderr), never in a tool response.
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass(frozen=True)
class PdfText:
  # Index 0 is page 1 of the file.
  pages: tuple[str, ...]
  title: str | None


def _title(reader: pypdf.PdfReader) -> str | None:
  try:
    metadata = reader.metadata
    title = metadata.title if metadata is not None else None
  except (PyPdfError, ValueError, TypeError, KeyError, AttributeError):
    return None
  if not isinstance(title, str):
    return None
  title = " ".join(title.split())
  return title[:TITLE_MAX_CHARS] or None


def extract_pages(data: bytes) -> PdfText:
  """Extract the text of every page, or refuse with a ``DeskError``."""
  if len(data) > MAX_PDF_BYTES:
    raise DeskError("too_large", "The PDF is larger than Evidence Desk reads (50 MB).")
  if not data.startswith(b"%PDF-"):
    raise DeskError("not_a_pdf", "The file is not a PDF.")
  try:
    reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
      raise DeskError(
        "encrypted_pdf",
        "The PDF is encrypted. Upload an unencrypted copy.",
      )
    count = len(reader.pages)
    if count == 0:
      raise DeskError("no_text", "The PDF has no pages.")
    if count > MAX_PAGES:
      raise DeskError("too_large", f"The PDF has more than {MAX_PAGES} pages.")
    pages = []
    total = 0
    for page in reader.pages:
      text = (page.extract_text() or "")[:MAX_PAGE_CHARS]
      total += len(text)
      if total > MAX_TOTAL_CHARS:
        raise DeskError("too_large", "The PDF contains more text than Evidence Desk reads.")
      pages.append(text)
    title = _title(reader)
  except DeskError:
    raise
  except (PyPdfError, ValueError, TypeError, KeyError, IndexError, AttributeError,
          AssertionError, RecursionError, ZeroDivisionError, OverflowError) as exc:
    raise DeskError(
      "unreadable_pdf", "The PDF could not be read. It may be damaged.",
    ) from exc
  if not any(text.strip() for text in pages):
    raise DeskError(
      "no_text",
      "No text could be extracted. The PDF may be scanned images; OCR is not supported.",
    )
  return PdfText(tuple(pages), title)
