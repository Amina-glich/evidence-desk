"""Evidence file validation and deterministic quotation matching."""

from __future__ import annotations

import json
import unittest

from desk.evidence import (
  NOT_FOUND, PAGE_OUT_OF_RANGE, TOO_SHORT, VERIFIED, WRONG_PAGE, Absence, Contradiction, FieldEvidence,
  Quote, check_quote, normalize_quote, page_variants, parse_evidence, parse_evidence_file,
)
from tests.pdf_fixtures import PAPER_PAGES


RAW_PAGES = tuple("\n".join(lines) + "\n" for lines in PAPER_PAGES)
PAGES = tuple(page_variants(text) for text in RAW_PAGES)


def check(page, quote, raw_pages=RAW_PAGES):
  return check_quote(Quote(page, quote), tuple(page_variants(text) for text in raw_pages), raw_pages)


def evidence(findings, source="S1", **extra):
  return json.dumps({"schema": 1, "source": source, "findings": findings, **extra}).encode()


REPORTED = {
  "status": "reported",
  "value": "CIFAR-10, 50k train / 10k test.",
  "evidence": [{"page": 2, "quote": "We train on the 50,000 CIFAR-10 training images"}],
}


class QuoteCheckTest(unittest.TestCase):

  def test_exact_quote_on_its_page_verifies(self):
    result = check(2, "We train on the 50,000 CIFAR-10 training images")
    self.assertEqual((result.result, result.match), (VERIFIED, "exact"))

  def test_quotes_spanning_line_breaks_verify_as_normalized(self):
    result = check(2, "training images and evaluate on the 10,000 test images")
    self.assertEqual((result.result, result.match), (VERIFIED, "normalized"))

  def test_line_break_hyphens_match_as_split_words_and_as_real_hyphens(self):
    self.assertEqual(check(2, "The learned representation is").result, VERIFIED)
    self.assertEqual(check(2, "is state-of-the-art for small models").result, VERIFIED)

  def test_typographic_forms_and_ligatures_are_normalized(self):
    pages = ("The eﬃcient model — called “Tiny” — runs­ fast.\n",)
    self.assertEqual(check(1, 'The efficient model - called "Tiny" - runs fast.', pages).result, VERIFIED)
    self.assertEqual(normalize_quote("  a​\tb\n\nc  "), "a b c")

  def test_wrong_page_reports_where_the_quote_is(self):
    result = check(1, "Our model reaches 91.2% top-1 accuracy")
    self.assertEqual((result.result, result.found_on), (WRONG_PAGE, (3,)))

  def test_altered_quotes_fail(self):
    for altered in (
      "Our model reaches 92.1% top-1 accuracy",      # changed number
      "our model reaches 91.2% top-1 accuracy",      # changed case
      "Our model reaches 91.2% accuracy",            # dropped word
      "We train on ... CIFAR-10 training images",    # ellipsis join
    ):
      with self.subTest(quote=altered):
        self.assertEqual(check(3 if "model" in altered else 2, altered).result, NOT_FOUND)

  def test_page_beyond_the_pdf_and_short_quotes(self):
    result = check(9, "Our model reaches 91.2% top-1 accuracy")
    self.assertEqual((result.result, result.found_on), (PAGE_OUT_OF_RANGE, (3,)))
    self.assertEqual(check(2, "CIFAR-10").result, TOO_SHORT)


class ParseEvidenceTest(unittest.TestCase):

  def assertInvalid(self, raw, fragment, source="S1"):
    findings, problems = parse_evidence(raw, source)
    self.assertIsNone(findings)
    self.assertTrue(any(fragment in problem for problem in problems), problems)

  def test_valid_file_parses_every_status(self):
    findings, problems = parse_evidence(evidence({
      "data": REPORTED,
      "runtime": {"status": "not_reported", "checked": "All 3 pages.", "note": "No latency table."},
      "limitations": {"status": "not_assessed"},
    }), "S1")
    self.assertEqual(problems, [])
    self.assertEqual(findings["data"].evidence, (Quote(2, REPORTED["evidence"][0]["quote"]),))
    self.assertEqual(findings["runtime"].checked, "All 3 pages.")
    self.assertEqual(findings["limitations"].status, "not_assessed")
    self.assertNotIn("task", findings)

  def test_structural_mistakes_invalidate_the_whole_file(self):
    self.assertInvalid(b"{nope", "not valid JSON")
    self.assertInvalid(evidence({}, source="S2"), '"source" must be "S1"')
    self.assertInvalid(evidence({}, extra=1), "Unknown top-level key")
    self.assertInvalid(evidence({"dataset": REPORTED}), "unknown dimension")
    self.assertInvalid(evidence({"data": {"status": "reported_maybe"}}), 'needs "status"')
    self.assertInvalid(evidence({"data": {"status": "reported", "value": "x"}}), "reported needs evidence")
    self.assertInvalid(evidence({"data": {**REPORTED, "evidence": []}}), "needs 1-20 items")
    self.assertInvalid(evidence({"data": {**REPORTED, "evidence": [{"page": "5", "quote": "abcdefghijk"}]}}), ".page")
    self.assertInvalid(evidence({"data": {**REPORTED, "evidence": [{"page": True, "quote": "abcdefghijk"}]}}), ".page")
    self.assertInvalid(evidence({"data": {**REPORTED, "evidence": [{"page": 2}]}}), 'exactly "page" and "quote"')
    self.assertInvalid(evidence({"data": {**REPORTED, "confidence": 0.9}}), "does not take confidence")
    self.assertInvalid(evidence({"runtime": {"status": "not_reported"}}), "not_reported needs checked")
    self.assertInvalid(evidence({"runtime": {"status": "not_reported", "checked": "  "}}), "which part of the source")
    self.assertInvalid(evidence({"task": {"status": "not_assessed", "value": "guess"}}), "does not take value")
    data = json.loads(evidence({}))
    data["schema"] = 2
    self.assertInvalid(json.dumps(data).encode(), '"schema" must be 1')

  def test_notes_are_kept_on_every_status(self):
    findings, problems = parse_evidence(evidence({
      "data": {**REPORTED, "note": "Only the CIFAR-10 setting."},
      "runtime": {"status": "not_reported", "checked": "All 3 pages.", "note": "No latency table."},
      "task": {"status": "not_assessed", "note": "Abstract only so far."},
    }), "S1")
    self.assertEqual(problems, [])
    self.assertEqual(findings["data"].note, "Only the CIFAR-10 setting.")
    self.assertEqual(findings["runtime"].note, "No latency table.")
    self.assertEqual(findings["task"].note, "Abstract only so far.")

  def test_reported_findings_keep_absences_and_contradictions(self):
    findings, problems = parse_evidence(evidence({"results": {
      **REPORTED,
      "absences": [{"item": "Energy use", "checked": "All 3 pages."}],
      "contradictions": [{
        "description": "Accuracy differs between abstract and results.",
        "evidence": [
          {"page": 1, "quote": "We study real-time object detection."},
          {"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"},
        ],
      }],
    }}), "S1")
    self.assertEqual(problems, [])
    result = findings["results"]
    self.assertEqual(result.absences, (Absence("Energy use", "All 3 pages."),))
    self.assertEqual(result.contradictions, (Contradiction(
      "Accuracy differs between abstract and results.",
      (Quote(1, "We study real-time object detection."), Quote(3, "Our model reaches 91.2% top-1 accuracy")),
    ),))

  def test_a_contradiction_cannot_repeat_one_passage(self):
    side = {"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"}
    other = {"page": 1, "quote": "We study real-time object detection."}

    def results(*sides):
      return evidence({"results": {**REPORTED, "contradictions": [
        {"description": "Two accuracies.", "evidence": list(sides)},
      ]}})

    self.assertInvalid(results(side, side), "repeats the same page and quotation")
    self.assertInvalid(results(side, other, side), "repeats the same page and quotation")
    # Only whitespace or typographic forms differ: still the same passage.
    spaced = {"page": 3, "quote": "Our model  reaches 91.2%\ntop-1 accuracy"}
    self.assertInvalid(results(side, spaced), "repeats the same page and quotation")
    # The same words on another page, or another passage, are different sides.
    findings, problems = parse_evidence(results(side, {**side, "page": 4}), "S1")
    self.assertEqual(problems, [])
    findings, problems = parse_evidence(results(side, other), "S1")
    self.assertEqual(problems, [])
    self.assertEqual(len(findings["results"].contradictions[0].evidence), 2)

  def test_qualifications_are_validated_strictly(self):
    side = {"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"}
    one_sided = {"description": "Two accuracies.", "evidence": [side]}
    cases = {
      # A contradiction must quote at least two passages.
      "contradictions[0].evidence: needs 2-20 items": {**REPORTED, "contradictions": [one_sided]},
      'needs exactly "description" and "evidence"': {**REPORTED, "contradictions": [{"evidence": [side, side]}]},
      "description: say what disagrees": {**REPORTED, "contradictions": [{"description": " ", "evidence": [side, side]}]},
      "contradictions: needs 1-10 items": {**REPORTED, "contradictions": []},
      ".contradictions[0].evidence[1].page": {
        **REPORTED, "contradictions": [{"description": "x", "evidence": [side, {"page": 0, "quote": "abcdefghijk"}]}],
      },
      # An absence must say what was searched.
      'needs exactly "item" and "checked"': {**REPORTED, "absences": [{"item": "Energy use"}]},
      "absences[0].checked: say which part": {**REPORTED, "absences": [{"item": "Energy use", "checked": ""}]},
      "absences: needs 1-20 items": {**REPORTED, "absences": "none"},
    }
    for fragment, finding in cases.items():
      with self.subTest(fragment=fragment):
        self.assertInvalid(evidence({"results": finding}), fragment)
    # Qualifications belong to Reported findings only: a whole-dimension
    # absence is not_reported with "checked", and not_assessed means unchecked.
    self.assertInvalid(evidence({"runtime": {
      "status": "not_reported", "checked": "All pages.", "absences": [{"item": "x", "checked": "y"}],
    }}), "not_reported does not take absences")
    self.assertInvalid(evidence({"task": {
      "status": "not_assessed", "contradictions": [one_sided],
    }}), "not_assessed does not take contradictions")


MEASUREMENT = {
  "dimension": "results",
  "metric_kind": "quality",
  "value_text": "91.2%",
  "unit": "%",
  "evidence": [{"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"}],
  "fields": {
    "task": {"label": "Image classification", "as_written": "Our model"},
    "dataset": {"label": "CIFAR-10", "as_written": "CIFAR-10", "page": 2,
                "quote": "We train on the 50,000 CIFAR-10 training images"},
    "split": {"unknown": "The test split is not named next to this value."},
    "metric": {"label": "Top-1 accuracy", "as_written": "top-1 accuracy"},
    "metric_definition": {"unknown": "Not stated."},
    "variant": {"unknown": "Not stated."},
  },
}


class MeasurementSchemaTest(unittest.TestCase):

  def assertInvalid(self, measurements, fragment):
    parsed, problems = parse_evidence_file(evidence({}, measurements=measurements), "S1")
    self.assertIsNone(parsed)
    self.assertTrue(any(fragment in problem for problem in problems), problems)

  def test_files_without_measurements_parse_as_before(self):
    raw = evidence({"data": REPORTED})
    parsed, problems = parse_evidence_file(raw, "S1")
    self.assertEqual(problems, [])
    self.assertEqual(parsed.measurements, ())
    self.assertEqual(parse_evidence(raw, "S1")[0], parsed.findings)
    self.assertEqual(parse_evidence_file(evidence({}, measurements=[]), "S1")[0].measurements, ())

  def test_a_valid_measurement_keeps_every_field_and_quote(self):
    parsed, problems = parse_evidence_file(evidence({}, measurements=[{**MEASUREMENT, "setting": "One run."}]), "S1")
    self.assertEqual(problems, [])
    (measurement,) = parsed.measurements
    self.assertEqual((measurement.value_text, measurement.unit, measurement.setting), ("91.2%", "%", "One run."))
    self.assertEqual(measurement.evidence, (Quote(3, "Our model reaches 91.2% top-1 accuracy"),))
    self.assertEqual(measurement.fields["task"], FieldEvidence(label="Image classification", as_written="Our model"))
    self.assertEqual(measurement.fields["dataset"].quote, Quote(2, "We train on the 50,000 CIFAR-10 training images"))
    self.assertEqual(measurement.fields["split"].unknown, "The test split is not named next to this value.")
    self.assertNotIn("hardware", measurement.fields)

  def test_every_compatibility_field_is_required(self):
    for name in ("task", "dataset", "split", "metric", "metric_definition", "variant"):
      with self.subTest(field=name):
        fields = {key: value for key, value in MEASUREMENT["fields"].items() if key != name}
        self.assertInvalid([{**MEASUREMENT, "fields": fields}], f'fields.{name}: required; use {{"unknown": reason}}')

  def test_structural_mistakes_invalidate_the_file(self):
    fields = MEASUREMENT["fields"]
    cases = {
      '"measurements" must be a list': "all",
      "needs exactly dimension, evidence, fields, metric_kind, unit, value_text": [{**MEASUREMENT, "confidence": 1}],
      "dimension: use one of": [{**MEASUREMENT, "dimension": "accuracy"}],
      "metric_kind: use one of": [{**MEASUREMENT, "metric_kind": "score"}],
      "value_text: the number exactly as the source writes it": [{**MEASUREMENT, "value_text": ""}],
      "unit: needs a unit": [{**MEASUREMENT, "unit": " "}],
      "evidence: needs 1-20 items": [{**MEASUREMENT, "evidence": []}],
      "fields.precision: unknown field": [{**MEASUREMENT, "fields": {**fields, "precision": {"unknown": "x"}}}],
      'needs "label" and "as_written"': [{**MEASUREMENT, "fields": {**fields, "task": {"label": "x"}}}],
      "unknown: say why the source does not state it": [{**MEASUREMENT, "fields": {**fields, "task": {"unknown": ""}}}],
      '"label" and "as_written" must be text': [{**MEASUREMENT, "fields": {**fields, "task": {"label": "", "as_written": "x"}}}],
      "fields.dataset[0].page": [{**MEASUREMENT, "fields": {**fields, "dataset": {**fields["dataset"], "page": 0}}}],
      "fields: must be an object": [{**MEASUREMENT, "fields": []}],
    }
    for fragment, measurements in cases.items():
      with self.subTest(fragment=fragment):
        self.assertInvalid(measurements, fragment)


if __name__ == "__main__":
  unittest.main()


class DisplayTitleTest(unittest.TestCase):
  """The label shown for a source: a verified first-page title, else the file name."""

  PAGE_ONE = "Fast Detectors for\nEdge Devices\nA. Author\nAbstract. We study detectors.\n"

  def title(self, metadata, page=PAGE_ONE, path="inbox/fast-detectors.pdf"):
    from desk.evidence import display_title
    return display_title(metadata, page, path)

  def test_a_metadata_title_found_on_the_first_page_is_used(self):
    self.assertEqual(self.title("Fast Detectors for Edge Devices"), "Fast Detectors for Edge Devices")
    self.assertEqual(self.title("  fast   detectors for edge devices "), "fast detectors for edge devices")

  def test_a_title_the_first_page_does_not_show_is_not_trusted(self):
    for wrong in ("Microsoft Word - draft_v7.docx", "Some Other Paper Entirely", "untitled"):
      self.assertEqual(self.title(wrong), "fast-detectors.pdf", wrong)

  def test_a_title_only_deep_in_the_page_is_not_trusted(self):
    page = "Header text. " * 400 + "Fast Detectors for Edge Devices"
    self.assertEqual(self.title("Fast Detectors for Edge Devices", page), "fast-detectors.pdf")

  def test_short_or_generic_titles_are_not_trusted_even_if_present(self):
    self.assertEqual(self.title("Abstract", "Abstract\nText"), "fast-detectors.pdf")

  def test_without_metadata_or_page_text_the_file_name_is_used(self):
    self.assertEqual(self.title(None), "fast-detectors.pdf")
    self.assertEqual(self.title("Fast Detectors for Edge Devices", None), "fast-detectors.pdf")
    self.assertEqual(self.title(None, None, "inbox/a/b.pdf"), "b.pdf")

  def test_nothing_known_gives_no_title(self):
    self.assertIsNone(self.title(None, None, None))
    self.assertIsNone(self.title(None, None, ""))
