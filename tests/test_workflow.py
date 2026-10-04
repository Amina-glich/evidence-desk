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
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import build_view
from desk import service
from desk.project_fs import project_lock
from desk.vocabulary import DIMENSIONS
from tests.pdf_fixtures import make_pdf, paper_pdf
from tests.test_measurements import A_76, B_74, PAPER_A, PAPER_B, accuracy
from tests.support import POSIX, REPO_ROOT, SKIP_REASON, Platform, tool_envelope


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
      for column in ("note", "checked absences", "contradictions"):
        self.assertEqual(rows[1][f"{label}: {column}"], "")
    self.assertEqual(len(rows[0]), 3 + 7 * len(DIMENSIONS))

  def test_export_preserves_notes_absences_and_contradictions(self):
    self.add()
    data = good_evidence()
    data["findings"]["results"].update({
      "note": "Single-run numbers; no variance reported.",
      "absences": [
        {"item": "Energy use", "checked": "All 3 pages."},
        {"item": "Results on other datasets", "checked": "Results section, p.3."},
      ],
      "contradictions": [{
        "description": "Two passages that disagree (fixture).",
        "evidence": [
          {"page": 2, "quote": "evaluate on the 10,000 test images"},
          {"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"},
        ],
      }],
    })
    data["findings"]["limitations"]["note"] = "No limitations section."
    data["findings"]["task"] = {"status": "not_assessed", "note": "Only the abstract was read."}
    self.write_evidence(data)
    reply = self.call("export_comparison")
    self.assertEqual((reply["quotes_verified"], reply["quotes_failed"]), (4, 0))
    row = self.read_csv()[0]
    label = "Metrics and reported results"
    # Qualifications sit beside the status; they never change it.
    self.assertEqual(row[f"{label}: status"], "Reported")
    self.assertEqual(row[f"{label}: note"], "Single-run numbers; no variance reported.")
    self.assertEqual(
      row[f"{label}: checked absences"],
      "Energy use (searched: All 3 pages.) | Results on other datasets (searched: Results section, p.3.)",
    )
    self.assertEqual(
      row[f"{label}: contradictions"],
      'Two passages that disagree (fixture).: '
      'p.2: "evaluate on the 10,000 test images" [verified] vs '
      'p.3: "Our model reaches 91.2% top-1 accuracy" [verified]',
    )
    self.assertEqual(row[f"{label}: check"], "Verified (3 of 3 quotations)")
    # Not reported (checked and absent) and Not assessed (unchecked) stay distinct.
    self.assertEqual(row["Limitations and gaps: status"], "Not reported")
    self.assertEqual(row["Limitations and gaps: finding"], "Searched: All 3 pages.")
    self.assertEqual(row["Limitations and gaps: note"], "No limitations section.")
    self.assertEqual(row["Research task or problem: status"], "Not assessed")
    self.assertEqual(row["Research task or problem: finding"], "")
    self.assertEqual(row["Research task or problem: note"], "Only the abstract was read.")

  def test_failed_contradiction_quotes_are_flagged_not_hidden(self):
    self.add()
    data = good_evidence()
    data["findings"]["results"]["contradictions"] = [{
      "description": "Latency differs between sections.",
      "evidence": [
        {"page": 3, "quote": "at 14 ms per image"},
        {"page": 3, "quote": "at 18 ms per image on the phone"},
      ],
    }]
    self.write_evidence(data)
    report = self.call("check_evidence")
    self.assertFalse(report["ok"])
    self.assertEqual(report["sources"][0]["contradictions"], 1)
    (problem,) = report["problems"]
    self.assertEqual(
      (problem["dimension"], problem["part"], problem["result"]),
      ("results", "contradiction", "not_found"),
    )
    reply = self.call("export_comparison")
    self.assertEqual((reply["quotes_verified"], reply["quotes_failed"]), (3, 1))
    row = self.read_csv()[0]
    label = "Metrics and reported results"
    self.assertEqual(
      row[f"{label}: check"],
      "Failed (1 of 3 quotations): p.3 not found in the source text (contradiction)",
    )
    self.assertEqual(
      row[f"{label}: contradictions"],
      'Latency differs between sections.: p.3: "at 14 ms per image" [verified] vs '
      'p.3: "at 18 ms per image on the phone" [check failed: p.3 not found in the source text]',
    )

  def test_qualification_text_cannot_inject_formulas(self):
    self.add()
    data = good_evidence()
    data["findings"]["results"]["note"] = "=cmd|'/c calc'!A1"
    data["findings"]["results"]["absences"] = [{"item": "@SUM(A1)", "checked": "All pages."}]
    self.write_evidence(data)
    self.call("export_comparison")
    row = self.read_csv()[0]
    self.assertEqual(row["Metrics and reported results: note"], "'=cmd|'/c calc'!A1")
    self.assertTrue(row["Metrics and reported results: checked absences"].startswith("'@SUM"))

  def test_contradictions_against_edited_page_text_are_not_trusted(self):
    self.add()
    data = good_evidence()
    data["findings"]["results"]["contradictions"] = [{
      "description": "Two figures.",
      "evidence": [
        {"page": 3, "quote": "Our model reaches 91.2% top-1 accuracy"},
        {"page": 2, "quote": QUOTE_DATA},
      ],
    }]
    self.write_evidence(data)
    pages = self.root / "sources" / "S1" / "pages.json"
    pages.write_text(pages.read_text().replace("CIFAR-10", "CIFAR-100"))
    report = self.call("check_evidence")
    parts = sorted((p.get("part"), p.get("result")) for p in report["problems"] if "part" in p)
    self.assertIn(("contradiction", "source_unavailable"), parts)
    self.assertEqual(report["summary"]["verified"], 0)
    reply = self.call("export_comparison")
    self.assertEqual((reply["quotes_verified"], reply["quotes_failed"]), (0, 4))
    row = self.read_csv()[0]
    self.assertNotIn("[verified]", row["Metrics and reported results: contradictions"])
    self.assertEqual(row["Metrics and reported results: contradictions"].count("source text unavailable"), 2)

  def test_an_oversized_cell_refuses_the_export_and_writes_nothing(self):
    self.add()
    data = good_evidence()
    # Within every evidence-file limit, but two contradictions of 20
    # 1000-character quotations make one CSV cell over the safe limit.
    sides = [{"page": 3, "quote": f"{index:04d}" + "q" * 996} for index in range(20)]
    data["findings"]["results"]["contradictions"] = [
      {"description": "First.", "evidence": sides},
      {"description": "Second.", "evidence": sides},
    ]
    self.write_evidence(data)
    self.assertEqual(self.call("check_evidence")["sources"][0]["evidence"], "valid")
    error = self.call("export_comparison", expect=409)
    self.assertEqual(error["error"], "cell_too_large")
    self.assertIn('S1 "Metrics and reported results: contradictions"', error["detail"])
    self.assertIn("32,000", error["detail"])
    self.assertFalse((self.root / "exports").exists())

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

  # The citation-linked comparison view.

  def output_dir(self):
    path = tempfile.mkdtemp(prefix="evidence-desk-build-")
    self.addCleanup(shutil.rmtree, path, True)
    return path

  def build(self, output):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
      code = build_view.main({"PROJECT_ROOT": str(self.root), "PROJECT_OUTPUT_DIR": output})
    return code, stdout.getvalue(), stderr.getvalue()

  def test_export_also_writes_the_comparison_view(self):
    self.add()
    data = good_evidence()
    data["findings"]["results"]["note"] = "Single run."
    self.write_evidence(data)
    reply = self.call("export_comparison")
    view = (self.root / "exports" / "comparison.html").read_bytes()
    self.assertEqual(reply["view"], "exports/comparison.html")
    self.assertEqual(reply["view_revision"], hashlib.sha256(view).hexdigest())
    text = view.decode("utf-8")
    self.assertIn(QUOTE_DATA, text)
    self.assertIn("Single run.", text)
    self.assertIn("2 of 2 quotations verified", text)
    self.assertIn("This is an evidence matrix.", text)

  def test_creation_builder_matches_the_exported_view(self):
    self.add()
    (self.root / "inbox" / "other.pdf").write_bytes(make_pdf([["Second paper with other content"]]))
    self.add("inbox/other.pdf")
    self.write_evidence(good_evidence())
    desk = {"schema": 1, "research_question": "Which is faster?", "compare_sources": ["S2", "S1"]}
    (self.root / "desk.json").write_text(json.dumps(desk))
    self.call("export_comparison")
    output = self.output_dir()
    code, stdout, stderr = self.build(output)
    self.assertEqual((code, stderr), (0, ""))
    self.assertIn("for 2 source(s)", stdout)
    built = (Path(output) / "index.html").read_bytes()
    self.assertEqual(built, (self.root / "exports" / "comparison.html").read_bytes())
    # desk.json order and question are honored.
    text = built.decode("utf-8")
    self.assertLess(text.index('id="f-data-S2"'), text.index('id="f-data-S1"'))
    self.assertIn("Which is faster?", text)

  def test_creation_builder_writes_nothing_into_the_project(self):
    self.add()
    self.write_evidence(good_evidence())
    before = sorted(str(path) for path in self.root.rglob("*"))
    self.assertEqual(self.build(self.output_dir())[0], 0)
    self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

  def test_build_script_runs_end_to_end(self):
    self.add()
    self.write_evidence(good_evidence())
    output = self.output_dir()
    env = {
      "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
      "PROJECT_ROOT": str(self.root),
      "PROJECT_SOURCE": "desk.json",
      "PROJECT_OUTPUT_DIR": output,
      "PROJECT_ARTIFACT_ID": "comparison",
    }
    completed = subprocess.run(
      ["bash", str(REPO_ROOT / "build.sh")], cwd=self.root, env=env,
      capture_output=True, text=True, timeout=120, check=False,
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    self.assertIn("Built the evidence comparison for 1 source(s).", completed.stdout)
    self.assertIn(QUOTE_DATA, (Path(output) / "index.html").read_text(encoding="utf-8"))

  def test_creation_builder_refuses_like_the_export(self):
    output = self.output_dir()
    code, _stdout, stderr = self.build(output)
    self.assertEqual(code, 1)
    self.assertIn("No sources are registered yet", stderr)
    self.add()
    self.write_evidence({"schema": 1, "source": "S1", "findings": {"data": {"status": "maybe"}}})
    code, _stdout, stderr = self.build(output)
    self.assertEqual(code, 1)
    self.assertIn("Fix the evidence file(s) for S1 first", stderr)
    self.assertEqual(os.listdir(output), [])

  def test_creation_builder_never_follows_symlinks_out_of_the_project(self):
    self.add()
    outside = self.platform.data_root / "outside"
    outside.mkdir()
    (outside / "S1.json").write_text(json.dumps(good_evidence()))
    os.symlink(outside, self.root / "evidence")
    output = self.output_dir()
    code, _stdout, stderr = self.build(output)
    self.assertEqual(code, 1)
    self.assertIn("symlink", stderr)
    self.assertEqual(os.listdir(output), [])


  # Structured measurements.

  def add_measured_papers(self):
    for name, pages in (("a.pdf", PAPER_A), ("b.pdf", PAPER_B)):
      (self.root / "inbox" / name).write_bytes(make_pdf([page.strip().split("\n") for page in pages]))
    first, second = self.add("inbox/a.pdf")["source_id"], self.add("inbox/b.pdf")["source_id"]
    self.assertEqual((first, second), ("S2", "S3"))
    desk = {"schema": 1, "research_question": "Q", "compare_sources": ["S2", "S3"]}
    (self.root / "desk.json").write_text(json.dumps(desk))

  def test_permitted_measurements_are_checked_and_plotted(self):
    self.add()
    self.add_measured_papers()
    self.write_evidence({"schema": 1, "source": "S2", "findings": {}, "measurements": [accuracy("76.3%", A_76)]}, source="S2")
    self.write_evidence({"schema": 1, "source": "S3", "findings": {}, "measurements": [accuracy("74.9%", B_74)]}, source="S3")
    report = self.call("check_evidence")
    self.assertTrue(report["ok"], report["problems"])
    measured = {entry["source"]: entry for entry in report["sources"]}
    self.assertEqual((measured["S2"]["measurements"], measured["S2"]["quotes_verified"]), (1, 2))
    reply = self.call("export_comparison")
    self.assertEqual((reply["quotes_verified"], reply["quotes_failed"]), (4, 0))
    view = (self.root / "exports" / "comparison.html").read_text(encoding="utf-8")
    self.assertIn('id="chart-1"', view)
    self.assertEqual(view.count("Plotted in chart 1"), 2)
    # The CSV layout does not change with measurements.
    self.assertEqual(len(self.read_csv()[0]), 3 + 7 * len(DIMENSIONS))

  def test_measurement_problems_are_reported_and_never_plotted(self):
    self.add()
    self.add_measured_papers()
    wrong_value = accuracy("76.4%", A_76)
    wrong_quote = accuracy("74.9%", "A single model achieves 74.9% top-1 accuracy on every benchmark.")
    self.write_evidence({"schema": 1, "source": "S2", "findings": {}, "measurements": [wrong_value]}, source="S2")
    self.write_evidence({"schema": 1, "source": "S3", "findings": {}, "measurements": [wrong_quote]}, source="S3")
    report = self.call("check_evidence")
    self.assertFalse(report["ok"])
    problems = [problem for problem in report["problems"] if problem.get("part") == "measurement"]
    self.assertIn(("S2", 1, "value_not_in_quote"), [(p["source"], p["measurement"], p.get("problem")) for p in problems])
    self.assertIn(("S3", 1, "not_found"), [(p["source"], p["measurement"], p.get("result")) for p in problems])
    # Measurement problems do not block the export; the view says why.
    self.call("export_comparison")
    view = (self.root / "exports" / "comparison.html").read_text(encoding="utf-8")
    self.assertNotIn('id="chart-1"', view)
    self.assertIn("No values are plotted.", view)
    self.assertIn("&quot;76.4%&quot; does not appear in its quotation", view)
    self.assertIn("the value&#x27;s quotation did not verify", view)

  def test_invalid_measurement_structure_invalidates_the_file_like_any_other_mistake(self):
    self.add()
    self.write_evidence({"schema": 1, "source": "S1", "findings": {}, "measurements": [{"value_text": "1"}]})
    report = self.call("check_evidence")
    self.assertEqual(report["summary"]["invalid_evidence_files"], 1)
    self.assertEqual(self.call("export_comparison", expect=409)["error"], "invalid_evidence")


if __name__ == "__main__":
  unittest.main()
