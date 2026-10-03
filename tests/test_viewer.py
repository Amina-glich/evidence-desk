"""The citation-linked evidence view, checked by parsing the generated HTML.

Runs on every platform: reports are built in memory by the real parser and
quote checker (tests.reports).
"""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser

from desk.viewer import render_html
from desk.vocabulary import DIMENSIONS
from tests.reports import QUOTE_DATA, QUOTE_RESULT, REPORTED, report_for


class Page(HTMLParser):
  """Collects ids, internal links, tags and the text inside each element id."""

  def __init__(self, html: str):
    super().__init__(convert_charrefs=True)
    self.ids: list[str] = []
    self.links: list[tuple[str, str]] = []
    self.tags: set[str] = set()
    self.attributes: list[tuple[str, str]] = []
    self.text_by_id: dict[str, str] = {}
    self._open: list[tuple[str, str | None]] = []
    self._link: str | None = None
    self._link_text: list[str] = []
    self.feed(html)

  def handle_starttag(self, tag, attrs):
    attrs = dict(attrs)
    self.tags.add(tag)
    self.attributes += [(name, value or "") for name, value in attrs.items()]
    element_id = attrs.get("id")
    if element_id:
      self.ids.append(element_id)
      self.text_by_id[element_id] = ""
    if tag not in ("meta", "br", "img", "input", "link"):
      self._open.append((tag, element_id))
    if tag == "a" and (attrs.get("href") or "").startswith("#"):
      self._link, self._link_text = attrs["href"][1:], []

  def handle_endtag(self, tag):
    if tag == "a" and self._link is not None:
      self.links.append((self._link, "".join(self._link_text)))
      self._link = None
    while self._open:
      open_tag, _element_id = self._open.pop()
      if open_tag == tag:
        break

  def handle_data(self, data):
    if self._link is not None:
      self._link_text.append(data)
    for _tag, element_id in self._open:
      if element_id:
        self.text_by_id[element_id] += data


def two_sources():
  first = report_for({
    "data": {**REPORTED, "value": "CIFAR-10, 50k train / 10k test.", "note": "Single split only."},
    "results": {
      "status": "reported",
      "value": "Accuracy is given as two different values.",
      "evidence": [{"page": 3, "quote": QUOTE_RESULT}],
      "absences": [{"item": "Variance across seeds", "checked": "Results section, p.3."}],
      "contradictions": [{
        "description": "The figures disagree.",
        "evidence": [{"page": 3, "quote": QUOTE_RESULT}, {"page": 2, "quote": "a sentence the paper never says"}],
      }],
    },
    "runtime": {"status": "not_reported", "checked": "All 3 pages."},
  }, title="Fast Detectors for Edge Devices")
  second = report_for({"task": {"status": "not_assessed", "note": "Only the abstract was read."}},
                      source_id="S2", title="Second paper")
  return [first, second]


class LinkIntegrityTest(unittest.TestCase):

  def setUp(self):
    self.html = render_html(two_sources(), research_question="Which detectors run in real time?")
    self.page = Page(self.html)

  def test_ids_are_unique_and_every_internal_link_resolves(self):
    self.assertEqual(len(self.page.ids), len(set(self.page.ids)))
    targets = set(self.page.ids)
    missing = [target for target, _text in self.page.links if target not in targets]
    self.assertEqual(missing, [])

  def test_every_citation_links_to_its_exact_quotation_and_back(self):
    citations = [(target, text) for target, text in self.page.links if target.startswith("q-")]
    # Evidence: data, results; contradiction: two sides.
    self.assertEqual(len(citations), 4)
    for target, text in citations:
      entry = self.page.text_by_id[target]
      source_id, page = text.strip("[]").split(",")[0].split(" p.")
      self.assertTrue(target.startswith(f"q-{source_id}-"))
      self.assertIn(f"Page {page}", entry)
    data_link = next(target for target, text in citations if "S1 p.2, verified" in text)
    self.assertIn(QUOTE_DATA, self.page.text_by_id[data_link])
    self.assertIn("verified", self.page.text_by_id[data_link])
    back = [target for target, text in self.page.links if text == "Back to the finding"]
    self.assertEqual(len(back), 4)
    self.assertIn("f-data-S1", back)

  def test_overview_links_every_dimension_and_source_to_its_finding(self):
    for dimension, _label in DIMENSIONS:
      for source_id in ("S1", "S2"):
        self.assertIn(f"f-{dimension}-{source_id}", [target for target, _text in self.page.links])


class ContentTest(unittest.TestCase):

  def setUp(self):
    self.reports = two_sources()
    self.page = Page(render_html(self.reports, research_question="Which detectors run in real time?"))

  def test_statuses_stay_distinct(self):
    self.assertIn("Reported", self.page.text_by_id["f-data-S1"])
    self.assertIn("Not reported", self.page.text_by_id["f-runtime-S1"])
    self.assertIn("Searched: All 3 pages.", self.page.text_by_id["f-runtime-S1"])
    self.assertIn("Not assessed", self.page.text_by_id["f-task-S2"])
    self.assertIn("Not checked yet.", self.page.text_by_id["f-task-S2"])
    self.assertNotIn("Searched", self.page.text_by_id["f-task-S2"])

  def test_notes_absences_and_contradictions_are_shown_with_their_checks(self):
    self.assertIn("Single split only.", self.page.text_by_id["f-data-S1"])
    self.assertIn("Only the abstract was read.", self.page.text_by_id["f-task-S2"])
    results = self.page.text_by_id["f-results-S1"]
    self.assertIn("Variance across seeds", results)
    self.assertIn("searched: Results section, p.3.", results)
    self.assertIn("Contradiction in the source (not resolved): The figures disagree.", results)
    self.assertIn(QUOTE_RESULT, results)
    self.assertIn("a sentence the paper never says", results)
    self.assertIn("[S1 p.3, verified]", results)
    self.assertIn("[S1 p.2, check failed]", results)

  def test_overview_flags_contradictions_absences_notes_and_failed_checks(self):
    html = render_html(self.reports, research_question=None)
    overview = html[html.index('<table class="overview">'):html.index("</table>")]
    results_row = overview[overview.index('href="#d-results"'):]
    results_row = results_row[:results_row.index("</tr>")]
    self.assertIn("1 failed check", results_row)
    self.assertIn("1 contradiction", results_row)
    self.assertIn("1 checked absence", results_row)
    data_row = overview[overview.index('href="#d-data"'):]
    self.assertIn("note", data_row[:data_row.index("</tr>")])
    self.assertIn("Research question: not set in desk.json.", html)

  def test_failed_and_unavailable_checks_are_never_shown_as_verified(self):
    report = report_for({"data": REPORTED}, source_available=False)
    html = render_html([report], research_question=None)
    page = Page(html)
    self.assertIn("[S1 p.2, check failed]", page.text_by_id["f-data-S1"])
    self.assertNotIn("[S1 p.2, verified]", page.text_by_id["f-data-S1"])
    self.assertIn("check failed: source text unavailable", page.text_by_id["q-S1-1"])
    self.assertIn("0 of 1 quotations verified", html)
    self.assertIn("source text unavailable (source_modified)", html)

  def test_status_labels_stay_on_one_line(self):
    # Narrow phone cells otherwise break "Not assessed" across two lines.
    html = render_html(self.reports, research_question=None)
    rule = re.search(r"\n\.status \{([^}]*)\}", html)
    self.assertIsNotNone(rule)
    self.assertIn("white-space: nowrap;", rule.group(1))

  def test_sources_appear_in_the_given_order(self):
    html = render_html(list(reversed(self.reports)), research_question=None)
    self.assertLess(html.index('id="f-data-S2"'), html.index('id="f-data-S1"'))


class SafetyTest(unittest.TestCase):

  HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)>" onmouseover="x'

  def test_all_recorded_text_is_escaped(self):
    hostile = self.HOSTILE
    report = report_for({
      "data": {**REPORTED, "value": hostile, "note": hostile},
      "results": {
        "status": "reported", "value": "v", "evidence": [{"page": 3, "quote": hostile}],
        "absences": [{"item": hostile, "checked": hostile}],
        "contradictions": [{"description": hostile, "evidence": [
          {"page": 3, "quote": QUOTE_RESULT}, {"page": 2, "quote": hostile},
        ]}],
      },
      "runtime": {"status": "not_reported", "checked": hostile},
    }, title=hostile)
    html = render_html([report], research_question=hostile)
    page = Page(html)
    self.assertNotIn("script", page.tags)
    self.assertNotIn("img", page.tags)
    self.assertFalse([name for name, _value in page.attributes if name.startswith("on")])
    self.assertIn("&lt;script&gt;", html)

  def test_page_is_self_contained_and_draws_no_chart(self):
    html = render_html(two_sources(), research_question=None)
    page = Page(html)
    self.assertFalse({"script", "svg", "canvas", "iframe", "img", "link", "object"} & page.tags)
    self.assertNotIn("http://", html)
    self.assertNotIn("https://", html)
    self.assertFalse([value for name, value in page.attributes if name in ("src", "srcset")])
    self.assertIn("This is an evidence matrix, not a chart.", html)
    self.assertIn("does not plot values across papers", html)

  def test_output_is_deterministic(self):
    self.assertEqual(
      render_html(two_sources(), research_question="Q"),
      render_html(two_sources(), research_question="Q"),
    )


if __name__ == "__main__":
  unittest.main()
