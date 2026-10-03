"""CSV layout, formula neutralization and the cell size limit.

These build the CSV from evidence parsed by the real parser and checked by
the real quote checker, without project files, so they run on every
platform. The end-to-end export through the service is in test_workflow.
"""

from __future__ import annotations

import csv
import io
import json
import unittest
from unittest import mock

from desk import export
from desk.errors import DeskError
from desk.evidence import QuoteCheck, SOURCE_UNAVAILABLE, SourceReport, check_quote, page_variants, parse_evidence
from desk.export import MAX_CELL_CHARS, build_csv, header_row
from desk.vocabulary import DIMENSIONS
from tests.pdf_fixtures import PAPER_PAGES


PAGES = tuple("\n".join(lines) + "\n" for lines in PAPER_PAGES)
QUOTE_DATA = "We train on the 50,000 CIFAR-10 training images"
QUOTE_RESULT = "Our model reaches 91.2% top-1 accuracy"
REPORTED = {"status": "reported", "value": "CIFAR-10.", "evidence": [{"page": 2, "quote": QUOTE_DATA}]}
FORMULA_PAYLOADS = ("=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", " =1+1", "\t=1+1")

# The column layout before qualification columns existed, in order.
ORIGINAL_HEADER = ["Source", "Title", "File"] + [
  f"{label}: {part}" for _dimension, label in DIMENSIONS
  for part in ("status", "finding", "evidence", "check")
]


def report_for(findings, *, source_available=True):
  raw = json.dumps({"schema": 1, "source": "S1", "findings": findings}).encode()
  parsed, problems = parse_evidence(raw, "S1")
  assert parsed is not None, problems
  report = SourceReport("S1", registered=True, evidence="valid", title="T", file="inbox/p.pdf", findings=parsed)
  variants = tuple(page_variants(text) for text in PAGES)
  if source_available:
    check = lambda quote: check_quote(quote, variants, PAGES)
  else:
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


def rows(*reports):
  text = build_csv(list(reports)).decode("utf-8-sig")
  return list(csv.reader(io.StringIO(text)))


class LayoutTest(unittest.TestCase):

  def test_original_columns_keep_their_positions(self):
    header = header_row()
    self.assertEqual(header[:27], ORIGINAL_HEADER)
    self.assertEqual(header[27:], [
      f"{label}: {part}" for _dimension, label in DIMENSIONS
      for part in ("note", "checked absences", "contradictions")
    ])
    self.assertEqual(rows(report_for({}))[0], header)

  def test_values_follow_their_columns(self):
    report = report_for({"data": {**REPORTED, "note": "Only one split."}})
    header, row = rows(report)
    cells = dict(zip(header, row))
    self.assertEqual(len(row), len(header))
    self.assertEqual(row[header.index("Dataset and data setting: status")], "Reported")
    self.assertEqual(cells["Dataset and data setting: note"], "Only one split.")
    self.assertEqual(cells["Research task or problem: status"], "Not assessed")


class FormulaTest(unittest.TestCase):

  def assertNeutralized(self, cell):
    self.assertTrue(cell.startswith("'"), cell)
    self.assertNotIn(cell[1:2], ("", " ", "\t"))

  def test_contradiction_cells_are_neutralized(self):
    for payload in FORMULA_PAYLOADS:
      with self.subTest(payload=payload):
        report = report_for({"results": {
          "status": "reported", "value": "Two accuracies.",
          "evidence": [{"page": 3, "quote": QUOTE_RESULT}],
          "contradictions": [{"description": payload, "evidence": [
            {"page": 3, "quote": QUOTE_RESULT}, {"page": 2, "quote": QUOTE_DATA},
          ]}],
        }})
        header, row = rows(report)
        self.assertNeutralized(dict(zip(header, row))["Metrics and reported results: contradictions"])

  def test_notes_on_not_reported_and_not_assessed_are_neutralized(self):
    for payload in FORMULA_PAYLOADS:
      with self.subTest(payload=payload):
        report = report_for({
          "runtime": {"status": "not_reported", "checked": "All pages.", "note": payload},
          "task": {"status": "not_assessed", "note": payload},
        })
        cells = dict(zip(*rows(report)))
        self.assertEqual(cells["Runtime, deployment, or real-time constraints: status"], "Not reported")
        self.assertNeutralized(cells["Runtime, deployment, or real-time constraints: note"])
        self.assertEqual(cells["Research task or problem: status"], "Not assessed")
        self.assertNeutralized(cells["Research task or problem: note"])


class UnavailableSourceTest(unittest.TestCase):

  def test_contradiction_quotes_fail_when_the_source_text_is_unavailable(self):
    report = report_for({"results": {
      "status": "reported", "value": "Two accuracies.",
      "evidence": [{"page": 3, "quote": QUOTE_RESULT}],
      "contradictions": [{"description": "Two figures.", "evidence": [
        {"page": 3, "quote": QUOTE_RESULT}, {"page": 2, "quote": QUOTE_DATA},
      ]}],
    }}, source_available=False)
    cells = dict(zip(*rows(report)))
    self.assertEqual(
      cells["Metrics and reported results: check"],
      "Failed (3 of 3 quotations): p.3 source text unavailable; "
      "p.3 source text unavailable (contradiction); p.2 source text unavailable (contradiction)",
    )
    self.assertIn("[check failed: p.3 source text unavailable]", cells["Metrics and reported results: contradictions"])
    self.assertNotIn("[verified]", cells["Metrics and reported results: contradictions"])


class CellLimitTest(unittest.TestCase):

  def test_documented_limit_is_below_the_spreadsheet_cell_maximum(self):
    self.assertLess(MAX_CELL_CHARS + 1, 32_767)

  def test_a_cell_at_the_limit_is_written_and_one_over_is_refused(self):
    note = "n" * 100
    with mock.patch.object(export, "MAX_CELL_CHARS", len(note)):
      rows(report_for({"data": {**REPORTED, "note": note}}))
      with self.assertRaises(DeskError) as caught:
        rows(report_for({"data": {**REPORTED, "note": note + "n"}}))
    error = caught.exception
    self.assertEqual((error.code, error.status), ("cell_too_large", 409))
    self.assertIn('S1 "Dataset and data setting: note"', error.message)
    self.assertIn("101 characters", error.message)

  def test_the_formula_prefix_counts_toward_the_limit(self):
    note = "=" + "n" * 99
    with mock.patch.object(export, "MAX_CELL_CHARS", len(note)):
      with self.assertRaises(DeskError):
        rows(report_for({"data": {**REPORTED, "note": note}}))


if __name__ == "__main__":
  unittest.main()
