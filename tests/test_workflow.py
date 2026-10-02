"""add_source, check_evidence and export_comparison through the service.

Each test calls the service exactly as the platform does, on a project
created from the real template, so binding, argument parsing, file safety
and locking are all exercised. Linux only (project file APIs).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import unittest
from unittest import mock

from desk import service
from desk.project_fs import project_lock
from desk.vocabulary import DIMENSIONS
from tests.pdf_fixtures import make_pdf, paper_pdf
from tests.support import POSIX, SKIP_REASON, Platform, tool_envelope


QUOTE_DATA = "We train on the 50,000 CIFAR-10 training images"
QUOTE_RESULT = "Our model reaches 91.2% top-1 accuracy at 14 ms per image."


def good_evidence(source="S1"):
  return {
    "schema": 1,
    "source": source,
    "findings": {
      "data": {
        "status": "reported",
        "value": "CIFAR-10: 50k training and 10k test images.",
        "evidence": [{"page": 2, "quote": QUOTE_DATA}],
      },
      "results": {
        "status": "reported",
        "value": "91.2% top-1 accuracy at 14 ms per image.",
        "evidence": [{"page": 3, "quote": QUOTE_RESULT}],
      },
      "limitations": {"status": "not_reported", "checked": "All 3 pages."},
    },
  }


@unittest.skipUnless(POSIX, SKIP_REASON)
class WorkflowTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)
    self.project_id = self.platform.add_project(name="Detectors", starter=True)
    self.root = self.platform.project_root(self.project_id)
    self.chat = self.platform.add_chat(project_id=self.project_id)
    (self.root / "inbox" / "paper.pdf").write_bytes(paper_pdf())

  def call(self, name, arguments=None, *, expect=200, access="write"):
    reply = service.handle(
      tool_envelope(name, self.chat, arguments=arguments, access=access), self.platform.env(),
    )
    self.assertEqual(reply["status"], expect, reply)
    return reply["body"]

  def add(self, file="inbox/paper.pdf", **kwargs):
    return self.call("add_source", {"file": file}, **kwargs)

  def write_evidence(self, data, source="S1"):
    (self.root / "evidence").mkdir(exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (self.root / "evidence" / f"{source}.json").write_text(text, encoding="utf-8")

  def read_csv(self):
    raw = (self.root / "exports" / "comparison.csv").read_bytes()
    self.assertTrue(raw.startswith("﻿".encode()))
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))

  # add_source

  def test_add_source_publishes_page_text_and_a_verifiable_record(self):
    reply = self.add()
    self.assertEqual(reply["source_id"], "S1")
    self.assertTrue(reply["created"])
    self.assertEqual((reply["pages"], reply["title"]), (3, "Fast Detectors for Edge Devices"))
    folder = self.root / "sources" / "S1"
    self.assertEqual(sorted(os.listdir(folder)), ["pages.json", "source.json"])
    record = json.loads((folder / "source.json").read_text())
    pages_raw = (folder / "pages.json").read_bytes()
    self.assertEqual(record["sha256"], hashlib.sha256(paper_pdf()).hexdigest())
    self.assertEqual(record["pages_sha256"], hashlib.sha256(pages_raw).hexdigest())
    self.assertEqual((record["file"], record["page_count"]), ("inbox/paper.pdf", 3))
    self.assertEqual(record["extractor"]["name"], "pypdf")
    pages = json.loads(pages_raw)["pages"]
    self.assertEqual([page["page"] for page in pages], [1, 2, 3])
    self.assertIn(QUOTE_DATA, pages[1]["text"])
    # The owner's upload is untouched and nothing is left in staging.
    self.assertEqual((self.root / "inbox" / "paper.pdf").read_bytes(), paper_pdf())
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])

  def test_the_same_pdf_is_registered_once(self):
    self.add()
    (self.root / "inbox" / "copy.pdf").write_bytes(paper_pdf())
    again = self.add("inbox/copy.pdf")
    self.assertEqual((again["source_id"], again["created"]), ("S1", False))
    (self.root / "inbox" / "other.pdf").write_bytes(make_pdf([["A different paper body"]]))
    self.assertEqual(self.add("inbox/other.pdf")["source_id"], "S2")

  def test_add_source_reads_only_pdfs_inside_inbox(self):
    for bad in ("desk.json", "inbox", "inbox/notes.txt", "sources/S1/pages.json",
                "../other/inbox/paper.pdf", "inbox/.hidden.pdf", "/etc/passwd"):
      with self.subTest(file=bad):
        error = self.add(bad, expect=400)
        self.assertIn(error["error"], {"invalid_arguments", "unsafe_path"})
    self.assertEqual(self.add("inbox/missing.pdf", expect=404)["error"], "not_found")
    outside = self.platform.data_root / "outside.pdf"
    outside.write_bytes(paper_pdf())
    os.symlink(outside, self.root / "inbox" / "linked.pdf")
    self.assertEqual(self.add("inbox/linked.pdf", expect=400)["error"], "unsafe_path")
    self.assertFalse((self.root / "sources" / "S1").exists())

  def test_unusable_pdfs_are_refused_and_leave_nothing_behind(self):
    (self.root / "inbox" / "scan.pdf").write_bytes(make_pdf([None, None]))
    self.assertEqual(self.add("inbox/scan.pdf", expect=400)["error"], "no_text")
    (self.root / "inbox" / "fake.pdf").write_bytes(b"not a pdf")
    self.assertEqual(self.add("inbox/fake.pdf", expect=400)["error"], "not_a_pdf")
    self.assertFalse((self.root / "sources").exists())

  def test_read_only_callers_cannot_add_or_export(self):
    self.assertEqual(self.add(access="read", expect=403)["error"], "read_only")
    self.assertEqual(self.call("export_comparison", access="read", expect=403)["error"], "read_only")
    self.call("check_evidence", access="read")

  # check_evidence

  def test_check_reports_verified_and_failed_quotations(self):
    self.add()
    data = good_evidence()
    data["findings"]["task"] = {
      "status": "reported",
      "value": "Real-time detection.",
      "evidence": [{"page": 2, "quote": "We study real-time object detection."}],
    }
    data["findings"]["runtime"] = {
      "status": "reported",
      "value": "Runs on a phone.",
      "evidence": [{"page": 3, "quote": "Our model runs on a mobile phone."}],
    }
    self.write_evidence(data)
    report = self.call("check_evidence")
    self.assertFalse(report["ok"])
    self.assertEqual(report["summary"], {
      "sources": 1, "invalid_evidence_files": 0, "quotes": 4, "verified": 2, "failed": 2,
    })
    self.assertEqual(report["sources"][0]["reported"], 4)
    self.assertEqual(report["sources"][0]["not_reported"], 1)
    self.assertEqual(report["sources"][0]["not_assessed"], 1)
    problems = {(p["dimension"], p["result"]): p for p in report["problems"]}
    self.assertEqual(problems[("task", "wrong_page")]["found_on"], [1])
    self.assertIn(("runtime", "not_found"), problems)

  def test_all_verified_evidence_is_ok_and_check_writes_nothing(self):
    self.add()
    self.write_evidence(good_evidence())
    before = sorted(str(path) for path in self.root.rglob("*"))
    report = self.call("check_evidence")
    self.assertTrue(report["ok"])
    self.assertEqual(report["summary"]["verified"], 2)
    self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

  def test_edited_page_text_is_not_trusted(self):
    self.add()
    self.write_evidence(good_evidence())
    pages = self.root / "sources" / "S1" / "pages.json"
    pages.write_text(pages.read_text().replace("91.2%", "99.9%"))
    report = self.call("check_evidence")
    self.assertFalse(report["ok"])
    self.assertEqual(report["sources"][0]["source_text"], "source_modified")
    self.assertEqual(report["summary"]["verified"], 0)

  def test_invalid_and_orphan_evidence_files_are_reported(self):
    self.add()
    self.write_evidence("{not json")
    self.write_evidence(good_evidence("S4"), source="S4")
    report = self.call("check_evidence")
    self.assertEqual(report["summary"]["invalid_evidence_files"], 1)
    texts = " ".join(p.get("problem", "") for p in report["problems"])
    self.assertIn("evidence/S1.json: The file is not valid JSON.", texts)
    self.assertIn("no registered source S4", texts)

  def test_check_with_no_sources_says_what_to_do(self):
    report = self.call("check_evidence")
    self.assertFalse(report["ok"])
    self.assertIn("add_source", report["note"])

  # export_comparison

  def test_export_writes_one_row_per_source_with_checks(self):
    self.add()
    (self.root / "inbox" / "other.pdf").write_bytes(make_pdf([["Second paper with other content"]]))
    self.add("inbox/other.pdf")
    data = good_evidence()
    data["findings"]["results"]["evidence"][0]["page"] = 2
    self.write_evidence(data)
    reply = self.call("export_comparison")
    self.assertEqual(reply["sources"], ["S1", "S2"])
    self.assertEqual((reply["quotes_verified"], reply["quotes_failed"]), (1, 1))
    rows = self.read_csv()
    self.assertEqual([row["Source"] for row in rows], ["S1", "S2"])
    first = rows[0]
    self.assertEqual(first["Title"], "Fast Detectors for Edge Devices")
    self.assertEqual(first["Dataset and data setting: status"], "Reported")
    self.assertEqual(first["Dataset and data setting: evidence"], f'p.2: "{QUOTE_DATA}"')
    self.assertEqual(first["Dataset and data setting: check"], "Verified (1 of 1 quotations)")
    self.assertEqual(
      first["Metrics and reported results: check"],
      "Failed (1 of 1 quotations): p.2 found on a different page (found on p.3)",
    )
    self.assertEqual(first["Limitations and gaps: status"], "Not reported")
    self.assertEqual(first["Limitations and gaps: finding"], "Searched: All 3 pages.")
    self.assertEqual(first["Research task or problem: status"], "Not assessed")
    for _dimension, label in DIMENSIONS:
      self.assertEqual(rows[1][f"{label}: status"], "Not assessed")
    self.assertEqual(len(rows[0]), 3 + 4 * len(DIMENSIONS))

  def test_export_is_deterministic_and_neutralizes_formulas(self):
    self.add()
    data = good_evidence()
    data["findings"]["data"]["value"] = "=HYPERLINK(\"http://evil\")"
    self.write_evidence(data)
    first = self.call("export_comparison")
    raw = (self.root / "exports" / "comparison.csv").read_bytes()
    second = self.call("export_comparison")
    self.assertEqual(first["revision"], second["revision"])
    self.assertEqual(raw, (self.root / "exports" / "comparison.csv").read_bytes())
    self.assertEqual(self.read_csv()[0]["Dataset and data setting: finding"], "'=HYPERLINK(\"http://evil\")")

  def test_export_follows_desk_json_scope(self):
    self.add()
    (self.root / "inbox" / "other.pdf").write_bytes(make_pdf([["Second paper with other content"]]))
    self.add("inbox/other.pdf")
    desk = {"schema": 1, "research_question": "Which is faster?", "compare_sources": ["S2"]}
    (self.root / "desk.json").write_text(json.dumps(desk))
    self.assertEqual(self.call("export_comparison")["sources"], ["S2"])
    desk["compare_sources"] = ["S2", "S7"]
    (self.root / "desk.json").write_text(json.dumps(desk))
    self.assertEqual(self.call("export_comparison", expect=409)["error"], "unknown_source")
    desk["compare_sources"] = "S1"
    (self.root / "desk.json").write_text(json.dumps(desk))
    self.assertEqual(self.call("export_comparison", expect=409)["error"], "invalid_desk_json")

  def test_export_refuses_without_sources_or_with_invalid_evidence(self):
    self.assertEqual(self.call("export_comparison", expect=409)["error"], "no_sources")
    self.add()
    self.write_evidence({"schema": 1, "source": "S1", "findings": {"data": {"status": "maybe"}}})
    self.assertEqual(self.call("export_comparison", expect=409)["error"], "invalid_evidence")
    self.assertFalse((self.root / "exports").exists())

  def test_export_never_writes_through_a_symlinked_exports_folder(self):
    self.add()
    outside = self.platform.data_root / "outside"
    outside.mkdir()
    os.symlink(outside, self.root / "exports")
    self.assertEqual(self.call("export_comparison", expect=400)["error"], "unsafe_path")
    self.assertEqual(os.listdir(outside), [])

  def test_writers_hold_the_project_lock(self):
    self.add()
    short_wait = lambda storage, project_id: project_lock(storage, project_id, timeout=0.2)
    with project_lock(self.platform.app_storage, self.project_id), \
         mock.patch("desk.export.project_lock", short_wait):
      self.assertEqual(self.call("export_comparison", expect=503)["error"], "busy")
    self.assertFalse((self.root / "exports").exists())


if __name__ == "__main__":
  unittest.main()
