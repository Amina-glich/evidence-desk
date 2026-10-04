"""Checked source reports built in memory, for export and view tests.

Evidence is parsed by the real parser and every quotation is checked by the
real quote checker (``evidence.check_report_quotes``, as ``check_project``
does) against the fixture paper's page text, or against ``pages`` when
given, without project files, so tests using this run on every platform.
"""

from __future__ import annotations

import json

from desk.evidence import (
  QuoteCheck, SOURCE_UNAVAILABLE, SourceReport, check_quote, check_report_quotes, page_variants,
  parse_evidence_file,
)
from tests.pdf_fixtures import PAPER_PAGES


PAGES = tuple("\n".join(lines) + "\n" for lines in PAPER_PAGES)
QUOTE_DATA = "We train on the 50,000 CIFAR-10 training images"
QUOTE_RESULT = "Our model reaches 91.2% top-1 accuracy"
REPORTED = {"status": "reported", "value": "CIFAR-10.", "evidence": [{"page": 2, "quote": QUOTE_DATA}]}


def report_for(findings, *, source_id="S1", title="T", source_available=True, measurements=None, pages=None):
  data = {"schema": 1, "source": source_id, "findings": findings}
  if measurements is not None:
    data["measurements"] = measurements
  parsed, problems = parse_evidence_file(json.dumps(data).encode(), source_id)
  assert parsed is not None, problems
  report = SourceReport(
    source_id, registered=True, evidence="valid", title=title, file="inbox/p.pdf",
    findings=parsed.findings, measurements=parsed.measurements,
  )
  page_text = PAGES if pages is None else tuple(pages)
  variants = tuple(page_variants(text) for text in page_text)
  if source_available:
    check = lambda quote: check_quote(quote, variants, page_text)
  else:
    report.source_text = "source_modified"
    check = lambda quote: QuoteCheck(quote.page, quote.quote, SOURCE_UNAVAILABLE)
  check_report_quotes(report, check)
  return report
