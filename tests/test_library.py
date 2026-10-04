"""The reference library and the search, lookup and save tools.

Record building and duplicate rules run everywhere. The tools are exercised
through the service, as the platform calls them, on projects created from
the real template (Linux only, like all project file access). No test
reaches the network: ``arxiv.transport`` is always a ``FakeTransport``.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import unittest
import urllib.error
from unittest import mock

from desk import arxiv, library, service
from desk.errors import DeskError
from tests.catalog import ATOM, FIXTURE, FIXTURE_SHA256, SEARCH_FEED, FakeTransport, entry_xml, feed, no_network
from tests.support import POSIX, SKIP_REASON, Platform, tool_envelope


class _FakeProject:
  def __init__(self, raw=None, error=None):
    self.raw, self.error = raw, error

  def read_bytes(self, path, *, max_bytes):
    if self.error:
      raise self.error
    return self.raw


class RecordTest(unittest.TestCase):

  def test_a_record_keeps_metadata_and_provenance_from_the_response(self):
    _total, (entry,) = arxiv.parse_feed(FIXTURE)
    response = arxiv.Response("id_list=1706.03762&max_results=1", FIXTURE, "2026-10-04T17:10:37Z")
    record = library._record("L1", entry, response)
    self.assertEqual(record["id"], "L1")
    self.assertEqual((record["arxiv_id"], record["version"]), ("1706.03762", "v7"))
    self.assertEqual(record["title"], "Attention Is All You Need")
    self.assertEqual((len(record["authors"]), record["author_count"]), (8, 8))
    self.assertEqual(record["categories"], ["cs.CL", "cs.LG"])
    self.assertEqual(record["abs_url"], "https://arxiv.org/abs/1706.03762")
    self.assertEqual(record["arxiv_doi"], "10.48550/arXiv.1706.03762")
    self.assertTrue(record["abstract"].startswith("The dominant sequence transduction models"))
    self.assertEqual(record["provenance"], {
      "endpoint": "https://export.arxiv.org/api/query",
      "request": "id_list=1706.03762&max_results=1",
      "retrieved_at": "2026-10-04T17:10:37Z",
      "response_sha256": FIXTURE_SHA256,
    })
    self.assertIn("not evidence", record["use"])
    self.assertTrue(record["full_text"].startswith("Not retrieved."))

  def test_duplicates_are_found_by_arxiv_id_or_publisher_doi(self):
    _total, (entry,) = arxiv.parse_feed(FIXTURE)
    self.assertEqual(library._in_library([{"id": "L3", "arxiv_id": "1706.03762", "version": "v1"}], entry), "L3")
    self.assertIsNone(library._in_library([{"id": "L1", "arxiv_id": "1705.03122"}], entry))
    with_doi = arxiv.parse_feed(feed(entry_xml("2301.12345v1", "X", extra="<arxiv:doi>10.1000/ABC</arxiv:doi>")))[1][0]
    self.assertEqual(library._in_library([{"id": "L2", "arxiv_id": "9999.00001", "publisher_doi": "10.1000/abc"}], with_doi), "L2")

  def test_same_titles_are_only_possible_duplicates(self):
    _total, (entry,) = arxiv.parse_feed(FIXTURE)
    references = [{"id": "L1", "arxiv_id": "9999.00001", "title": "attention is all you need!"}]
    self.assertEqual(library._possible_duplicates(references, entry), ["L1"])
    self.assertIsNone(library._in_library(references, entry))

  def test_an_invalid_library_is_refused_not_overwritten(self):
    for raw in (b"{broken", b"[]", b'{"schema": 2, "references": []}', b'{"schema": 1, "references": [{"id": "X1", "arxiv_id": "a"}]}',
                b'{"schema": 1, "references": [{"id": "L1", "arxiv_id": "a"}, {"id": "L1", "arxiv_id": "b"}]}'):
      with self.subTest(raw=raw):
        with self.assertRaises(DeskError) as caught:
          library.read_library(_FakeProject(raw))
        self.assertEqual(caught.exception.code, "library_invalid")
    self.assertEqual(library.read_library(_FakeProject(error=DeskError("not_found", "x", status=404))), ([], None))


def _save_in_child(data_root, storage, chat, arxiv_id, results):
  """Child-process body for the concurrent save test (must be module level)."""
  answer = (200, ATOM, feed(entry_xml(f"{arxiv_id}v1", f"Paper {arxiv_id}")), {})
  with mock.patch.object(arxiv, "transport", FakeTransport(default=answer)), \
       mock.patch.object(arxiv, "MIN_INTERVAL_SECONDS", 0.05):
    env = {"APP_ID": "7", "APP_STORAGE_DIR": str(storage)}
    reply = service.handle(tool_envelope("save_reference", chat, arguments={"identifier": arxiv_id}), env)
  results.put(reply["body"].get("reference_id"))


@unittest.skipUnless(POSIX, SKIP_REASON)
class LibraryToolsTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)
    self.project_id = self.platform.add_project(name="Detectors", starter=True)
    self.root = self.platform.project_root(self.project_id)
    self.chat = self.platform.add_chat(project_id=self.project_id)
    self.transport = FakeTransport({
      "id_list=1706.03762": (200, ATOM, FIXTURE, {}),
      "id_list=1706.03762v7": (200, ATOM, FIXTURE, {}),
      "search_query=all:attention AND all:transformer": (200, ATOM, SEARCH_FEED, {}),
    })
    for patcher in (
      mock.patch.object(arxiv, "transport", self.transport),
      mock.patch.object(arxiv, "MIN_INTERVAL_SECONDS", 0.0),
    ):
      patcher.start()
      self.addCleanup(patcher.stop)

  def call(self, name, arguments=None, *, chat=None, expect=200, access="write"):
    reply = service.handle(
      tool_envelope(name, chat or self.chat, arguments=arguments or {}, access=access), self.platform.env(),
    )
    self.assertEqual(reply["status"], expect, reply)
    return reply["body"]

  def library_bytes(self, root=None):
    return ((root or self.root) / "library" / "references.json").read_bytes()

  def test_search_lists_results_and_marks_saved_papers(self):
    body = self.call("search_literature", {"query": "attention transformer"}, access="read")
    self.assertEqual(body["total_results"], 2)
    self.assertEqual([result["arxiv_id"] for result in body["results"]], ["1705.03122", "1706.03762"])
    self.assertEqual([result["in_library"] for result in body["results"]], [None, None])
    self.assertIn("not evidence", body["note"])
    self.assertFalse((self.root / "library").exists())
    self.call("save_reference", {"identifier": "1706.03762"})
    body = self.call("search_literature", {"query": "attention transformer"})
    self.assertEqual([result["in_library"] for result in body["results"]], [None, "L1"])

  def test_lookup_is_read_only(self):
    body = self.call("lookup_reference", {"identifier": "arXiv:1706.03762"}, access="read")
    self.assertEqual((body["result"]["arxiv_id"], body["result"]["title"]), ("1706.03762", "Attention Is All You Need"))
    self.assertIsNone(body["result"]["in_library"])
    self.assertFalse((self.root / "library").exists())

  def test_save_writes_one_record_built_from_the_response(self):
    body = self.call("save_reference", {"identifier": "1706.03762"})
    self.assertEqual((body["reference_id"], body["created"]), ("L1", True))
    data = json.loads(self.library_bytes())
    self.assertEqual(data["schema"], 1)
    (record,) = data["references"]
    self.assertEqual(record, body["reference"])
    self.assertEqual(record["title"], "Attention Is All You Need")
    self.assertEqual(record["provenance"]["response_sha256"], hashlib.sha256(FIXTURE).hexdigest())
    self.assertEqual(os.listdir(self.root / "library"), ["references.json"])

  def test_duplicates_are_not_saved_twice(self):
    self.call("save_reference", {"identifier": "1706.03762"})
    before = self.library_bytes()
    for identifier in ("1706.03762", "1706.03762v7", "10.48550/arXiv.1706.03762", "https://arxiv.org/abs/1706.03762"):
      with self.subTest(identifier=identifier):
        body = self.call("save_reference", {"identifier": identifier})
        self.assertEqual((body["reference_id"], body["created"]), ("L1", False))
        self.assertEqual(self.library_bytes(), before)

  def test_caller_metadata_is_refused_and_read_only_callers_cannot_save(self):
    error = self.call("save_reference", {"identifier": "1706.03762", "title": "Made up"}, expect=400)
    self.assertEqual(error["error"], "unknown_argument")
    self.assertEqual(self.call("save_reference", {"identifier": "1706.03762"}, access="read", expect=403)["error"], "read_only")
    self.assertFalse((self.root / "library").exists())

  def test_each_project_keeps_its_own_library(self):
    other_id = self.platform.add_project(name="Other", starter=True)
    other_root = self.platform.project_root(other_id)
    other_chat = self.platform.add_chat(project_id=other_id)
    self.call("save_reference", {"identifier": "1706.03762"})
    self.assertFalse((other_root / "library").exists())
    body = self.call("search_literature", {"query": "attention transformer"}, chat=other_chat)
    self.assertEqual([result["in_library"] for result in body["results"]], [None, None])
    before = self.library_bytes()
    self.call("save_reference", {"identifier": "1706.03762"}, chat=other_chat)
    self.assertEqual(self.library_bytes(), before)
    self.assertEqual(json.loads(self.library_bytes(other_root))["references"][0]["id"], "L1")

  def test_symlinked_or_invalid_libraries_are_refused(self):
    outside = self.platform.data_root / "outside"
    outside.mkdir()
    os.symlink(outside, self.root / "library")
    self.assertEqual(self.call("save_reference", {"identifier": "1706.03762"}, expect=400)["error"], "unsafe_path")
    self.assertEqual(os.listdir(outside), [])
    os.unlink(self.root / "library")
    (self.root / "library").mkdir()
    (self.root / "library" / "references.json").write_text("{broken")
    calls_before = len(self.transport.calls)
    self.assertEqual(self.call("save_reference", {"identifier": "1706.03762"}, expect=409)["error"], "library_invalid")
    self.assertEqual(len(self.transport.calls), calls_before, "no arXiv request for an unusable library")
    self.assertEqual(self.library_bytes(), b"{broken")
    body = self.call("search_literature", {"query": "attention transformer"})
    self.assertIn("in_library is unknown", body["library_note"])
    self.assertEqual({result["in_library"] for result in body["results"]}, {None})

  def test_network_and_catalog_failures_write_nothing(self):
    def unreachable(url, headers, timeout):
      raise urllib.error.URLError("dns failure for export.arxiv.org via 10.0.0.1")
    with mock.patch.object(arxiv, "transport", unreachable):
      error = self.call("save_reference", {"identifier": "1706.03762"}, expect=503)
    self.assertEqual(error, {"error": "network_unavailable", "detail": "arXiv could not be reached from Evidence Desk; try again later."})
    with mock.patch.object(arxiv, "transport", FakeTransport(default=(200, ATOM, feed(total=0), {}))):
      self.assertEqual(self.call("save_reference", {"identifier": "2301.12345"}, expect=404)["error"], "not_in_catalog")
    with mock.patch.object(arxiv, "transport", FakeTransport(default=(200, ATOM, FIXTURE, {}))):
      # A specific version that arXiv did not return is not saved as another version.
      self.assertEqual(self.call("save_reference", {"identifier": "1706.03762v3"}, expect=404)["error"], "not_in_catalog")
    self.assertFalse((self.root / "library").exists())

  def test_invalid_identifiers_never_reach_the_network(self):
    with mock.patch.object(arxiv, "transport", no_network):
      for identifier, code in (("https://evil.example/abs/1706.03762", "invalid_identifier"),
                               ("10.1145/3292500.3330701", "unsupported_doi")):
        with self.subTest(identifier=identifier):
          self.assertEqual(self.call("save_reference", {"identifier": identifier}, expect=400)["error"], code)
      self.assertEqual(self.call("search_literature", {"query": "AND OR"}, expect=400)["error"], "invalid_query")

  def test_the_library_never_becomes_evidence(self):
    from tests.pdf_fixtures import paper_pdf
    (self.root / "inbox" / "paper.pdf").write_bytes(paper_pdf())
    self.call("add_source", {"file": "inbox/paper.pdf"})
    before_check = self.call("check_evidence")
    self.call("export_comparison")
    before_csv = (self.root / "exports" / "comparison.csv").read_bytes()
    before_view = (self.root / "exports" / "comparison.html").read_bytes()
    self.call("save_reference", {"identifier": "1706.03762"})
    self.assertEqual(self.call("check_evidence"), before_check)
    self.call("export_comparison")
    self.assertEqual((self.root / "exports" / "comparison.csv").read_bytes(), before_csv)
    self.assertEqual((self.root / "exports" / "comparison.html").read_bytes(), before_view)
    self.assertEqual(sorted(os.listdir(self.root / "sources")), [".staging", "S1"])

  def test_project_status_reports_the_library(self):
    self.assertEqual(self.call("project_status")["library"], {"state": "empty", "references": 0})
    self.call("save_reference", {"identifier": "1706.03762"})
    status = self.call("project_status")
    self.assertEqual(status["library"], {"state": "present", "references": 1})
    self.assertEqual(status["areas"]["library"]["files"], ["references.json"])

  def test_concurrent_saves_get_unique_ids(self):
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    workers = [
      context.Process(target=_save_in_child, args=(
        self.platform.data_root, self.platform.app_storage, self.chat, f"2301.0000{index}", results,
      ))
      for index in range(1, 4)
    ]
    for worker in workers:
      worker.start()
    for worker in workers:
      worker.join(60)
      self.assertEqual(worker.exitcode, 0)
    self.assertEqual(sorted(results.get(timeout=5) for _ in workers), ["L1", "L2", "L3"])
    saved = json.loads(self.library_bytes())["references"]
    self.assertEqual(sorted(reference["arxiv_id"] for reference in saved), ["2301.00001", "2301.00002", "2301.00003"])


if __name__ == "__main__":
  unittest.main()
