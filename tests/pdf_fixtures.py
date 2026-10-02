"""Small, real PDFs built in memory for offline tests.

Each page is a list of text lines drawn with the standard Helvetica font, one
``T*`` line break apart, so pypdf extracts them as newline-separated lines.
A page given as ``None`` has no content stream (like a scanned image page).
"""

from __future__ import annotations

import io


def _escape(text: str) -> str:
  return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str] | None], *, title: str | None = None) -> bytes:
  objects: list[bytes] = []

  def add(body: bytes) -> int:
    objects.append(body)
    return len(objects)

  catalog = add(b"")  # filled in below
  pages_id = add(b"")
  font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
  kids = []
  for lines in pages:
    content_ref = b""
    if lines is not None:
      ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
      for line in lines:
        ops.append(f"({_escape(line)}) Tj T*")
      ops.append("ET")
      stream = "\n".join(ops).encode("latin-1")
      content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
      content_ref = b" /Contents %d 0 R" % content
    kids.append(add(
      b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
      b"/Resources << /Font << /F1 %d 0 R >> >>%s >>" % (pages_id, font, content_ref)
    ))
  objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
  objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
    b" ".join(b"%d 0 R" % kid for kid in kids), len(kids),
  )
  info = None
  if title is not None:
    info = add(b"<< /Title (%s) >>" % _escape(title).encode("latin-1"))

  out = io.BytesIO()
  out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
  offsets = []
  for number, body in enumerate(objects, start=1):
    offsets.append(out.tell())
    out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
  xref = out.tell()
  out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
  for offset in offsets:
    out.write(b"%010d 00000 n \n" % offset)
  trailer = b"<< /Size %d /Root %d 0 R" % (len(objects) + 1, catalog)
  if info is not None:
    trailer += b" /Info %d 0 R" % info
  out.write(b"trailer\n%s >>\nstartxref\n%d\n%%%%EOF\n" % (trailer, xref))
  return out.getvalue()


# A three-page paper used across the source, evidence and export tests.
PAPER_PAGES = [
  ["Fast Detectors for Edge Devices", "Abstract. We study real-time object detection."],
  [
    "2 Experimental setup",
    "We train on the 50,000 CIFAR-10 training images and",
    "evaluate on the 10,000 test images with five random seeds.",
    "The learned repre-",
    "sentation is state-of-the-",
    "art for small models.",
  ],
  ["3 Results", "Our model reaches 91.2% top-1 accuracy at 14 ms per image."],
]


def paper_pdf() -> bytes:
  return make_pdf(PAPER_PAGES, title="Fast Detectors for Edge Devices")
