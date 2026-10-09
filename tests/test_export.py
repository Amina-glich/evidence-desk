"""CSV layout, formula neutralization and the cell size limit.

These build the CSV from evidence parsed by the real parser and checked by
the real quote checker, without project files, so they run on every
platform. The end-to-end export through the service is in test_workflow.
"""

from __future__ import annotations

import csv
import io
import unittest
from unittest import mock

from desk import export
from desk.errors import DeskError
from desk.export import MAX_CELL_CHARS, build_csv, header_row
from desk.vocabulary import DIMENSIONS
from tests.reports import QUOTE_DATA, QUOTE_RESULT, REPORTED, report_for


FORMULA_PAYLOADS = ("=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", " =1+1", "\t=1+1")

# The column layout before qualification columns existed, in order.
ORIGINAL_HEADER = ["Source", "Title", "File"] + [
  f"{label}: {part}" for _dimension, label in DIMENSIONS
  for part in ("status", "finding", "evidence", "check")
]


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


class NarrowCsvTest(unittest.TestCase):
  """exports/comparison-by-dimension.csv: the same cells, one row per dimension."""

  def narrow(self, *reports):
    text = export.build_narrow_csv(list(reports)).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))

  def full_report(self):
    return report_for({
      "data": {**REPORTED, "note": "Only one split."},
      "results": {
        "status": "reported", "value": "91.2% top-1.", "evidence": [{"page": 3, "quote": QUOTE_RESULT}],
        "absences": [{"item": "latency", "checked": "pages 1-4"}],
      },
      "runtime": {"status": "not_reported", "checked": "Read pages 1-4 for a latency table."},
      "task": {
        **REPORTED,
        "contradictions": [{
          "description": "Two accuracies",
          "evidence": [{"page": 2, "quote": QUOTE_DATA}, {"page": 2, "quote": QUOTE_RESULT}],
        }],
      },
    })

  def test_it_has_eleven_columns_and_one_row_per_dimension(self):
    table = self.narrow(self.full_report(), report_for({}, source_id="S2"))
    self.assertEqual(table[0], export.NARROW_HEADER)
    self.assertEqual(len(export.NARROW_HEADER), 11)
    self.assertTrue(all(len(row) == 11 for row in table))
    self.assertEqual(len(table) - 1, 2 * len(DIMENSIONS))
    self.assertEqual([row[3] for row in table[1:len(DIMENSIONS) + 1]], [label for _id, label in DIMENSIONS])

  def test_every_cell_of_the_full_csv_appears_unchanged(self):
    report = self.full_report()
    header, wide = rows(report)
    cells = dict(zip(header, wide))
    for row in self.narrow(report)[1:]:
      label = row[3]
      for column, part in zip(("Status", "Finding", "Evidence", "Check", "Note", "Checked absences", "Contradictions"), row[4:]):
        wide_name = f"{label}: {column.lower()}"
        self.assertEqual(part, cells[wide_name], wide_name)
    flat = {cell for row in self.narrow(report)[1:] for cell in row}
    for cell in wide:
      self.assertTrue(cell == "" or cell in flat, cell)

  def test_formulas_are_neutralized_and_the_cell_limit_applies(self):
    report = report_for({"data": {**REPORTED, "note": "=HYPERLINK(\"http://x\")"}})
    self.assertIn("'=HYPERLINK(\"http://x\")", {cell for row in self.narrow(report) for cell in row})
    note = "n" * 100
    with mock.patch.object(export, "MAX_CELL_CHARS", len(note)):
      export.build_narrow_csv([report_for({"data": {**REPORTED, "note": note}})])
      with self.assertRaises(DeskError) as raised:
        export.build_narrow_csv([report_for({"data": {**REPORTED, "note": note + "n"}})])
    self.assertEqual(raised.exception.code, "cell_too_large")
