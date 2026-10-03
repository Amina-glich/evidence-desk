"""Checked source reports built in memory, for export and view tests.

Evidence is parsed by the real parser and every quotation is checked by the
real quote checker against the fixture paper's page text, without project
files, so tests using this run on every platform.
"""

from __future__ import annotations

import json

from desk.evidence import QuoteCheck, SOURCE_UNAVAILABLE, SourceReport, check_quote, page_variants, parse_evidence
from tests.pdf_fixtures import PAPER_PAGES


PAGES = tuple("\n".join(lines) + "\n" for lines in PAPER_PAGES)
QUOTE_DATA = "We train on the 50,000 CIFAR-10 training images"
QUOTE_RESULT = "Our model reaches 91.2% top-1 accuracy"
REPORTED = {"status": "reported", "value": "CIFAR-10.", "evidence": [{"page": 2, "quote": QUOTE_DATA}]}


def report_for(findings, *, source_id="S1", title="T", source_available=True):
  raw = json.dumps({"schema": 1, "source": source_id, "findings": findings}).encode()
  parsed, problems = parse_evidence(raw, source_id)
  assert parsed is not None, problems
  report = SourceReport(
    source_id, registered=True, evidence="valid", title=title, file="inbox/p.pdf", findings=parsed,
  )
  variants = tuple(page_variants(text) for text in PAGES)
  if source_available:
    check = lambda quote: check_quote(quote, variants, PAGES)
  else:
    report.source_text = "source_modified"
    check = lambda quote: QuoteCheck(quote.page, quote.quote, SOURCE_UNAVAILABLE)
  for dimension, finding in parsed.items():
    if finding.evidence:
      report.checks[dimension] = tuple(check(quote) for quote in finding.evidence)
    if finding.contradictions:
      report.contradiction_checks[dimension] = tuple(
        tuple(check(quote) for quote in contradiction.evidence)
        for contradiction in finding.contradictions
      )
  return report
