"""arXiv client: parsing, identifiers, queries, the fixed endpoint, errors and pacing.

Offline: responses come from tests/fixtures/arxiv (recorded once) and
synthetic feeds; ``arxiv.transport`` is always replaced.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import unittest
from pathlib import Path
from unittest import mock

from desk import arxiv
from desk.errors import DeskError
from tests.catalog import ATOM, FIXTURE, FIXTURE_SHA256, SEARCH_FEED, FakeTransport, entry_xml, feed, no_network, temporary_state_lock
from tests.support import POSIX, SKIP_REASON, Platform


class FixtureTest(unittest.TestCase):

  def test_the_recorded_fixture_is_unchanged(self):
    self.assertEqual(hashlib.sha256(FIXTURE).hexdigest(), FIXTURE_SHA256)

  def test_every_field_is_read_from_the_recorded_response(self):
    total, (entry,) = arxiv.parse_feed(FIXTURE)
    self.assertEqual(total, 1)
    self.assertEqual((entry.arxiv_id.base, entry.arxiv_id.version), ("1706.03762", "v7"))
    self.assertEqual(entry.title, "Attention Is All You Need")
    self.assertEqual(entry.author_count, 8)
    self.assertEqual(entry.authors[0], "Ashish Vaswani")
    self.assertEqual(entry.authors[-1], "Illia Polosukhin")
    self.assertEqual(entry.primary_category, "cs.CL")
    self.assertEqual(entry.categories, ("cs.CL", "cs.LG"))
    self.assertEqual((entry.published, entry.updated), ("2017-06-12T17:57:34Z", "2023-08-02T00:41:18Z"))
    self.assertEqual(entry.comment, "15 pages, 5 figures")
    self.assertIsNone(entry.journal_ref)
    self.assertIsNone(entry.publisher_doi)
    self.assertTrue(entry.abstract.startswith("The dominant sequence transduction models"))
    self.assertIn("28.4 BLEU", entry.abstract)
    self.assertFalse(entry.abstract_truncated)

  def test_urls_are_rebuilt_from_the_id_never_copied_from_the_feed(self):
    hostile = FIXTURE.replace(b"https://arxiv.org/pdf/1706.03762v7", b"https://evil.example/pdf")
    _total, (entry,) = arxiv.parse_feed(hostile)
    self.assertEqual(entry.abs_url, "https://arxiv.org/abs/1706.03762")
    self.assertEqual(entry.version_url, "https://arxiv.org/abs/1706.03762v7")
    self.assertEqual(entry.pdf_url, "https://arxiv.org/pdf/1706.03762v7")
    self.assertEqual(entry.arxiv_doi, "10.48550/arXiv.1706.03762")


class HostileFeedTest(unittest.TestCase):

  def assertRefused(self, code, body):
    with self.assertRaises(DeskError) as caught:
      arxiv.parse_feed(body)
    self.assertEqual(caught.exception.code, code)

  def test_doctype_entities_and_bad_xml_are_refused(self):
    bomb = (
      b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
      b'<feed xmlns="http://www.w3.org/2005/Atom"><title>&lol2;</title></feed>'
    )
    self.assertRefused("catalog_invalid_response", bomb)
    self.assertRefused("catalog_invalid_response", FIXTURE.replace(b"<feed", b"<!ENTITY x 'y'><feed", 1))
    self.assertRefused("catalog_invalid_response", b"<feed><entry>")
    self.assertRefused("catalog_invalid_response", b'<html xmlns="http://www.w3.org/1999/xhtml"/>')
    self.assertRefused("catalog_invalid_response", b"x" * (arxiv.MAX_RESPONSE_BYTES + 1))

  def test_an_arxiv_error_entry_means_an_unknown_identifier(self):
    error = feed(entry_xml("x", "Error", entry_id="http://arxiv.org/api/errors#incorrect_id_format_for_17"))
    self.assertRefused("invalid_identifier", error)

  def test_entries_that_are_not_arxiv_records_are_skipped(self):
    body = feed(
      entry_xml("1706.03762v7", "Elsewhere", entry_id="https://evil.example/abs/1706.03762v7"),
      entry_xml("not-an-id", "Bad id"),
      entry_xml("2301.12345v1", ""),
      entry_xml("2301.12345v2", "Kept"),
    )
    _total, entries = arxiv.parse_feed(body)
    self.assertEqual([entry.title for entry in entries], ["Kept"])

  def test_long_fields_are_bounded(self):
    body = feed(entry_xml(
      "2301.12345v1", "T" * 5000, authors=tuple(f"Author {index}" for index in range(80)),
      summary="word " * 5000,
    ))
    _total, (entry,) = arxiv.parse_feed(body)
    self.assertEqual(len(entry.title), arxiv.MAX_TITLE_CHARS)
    self.assertEqual((len(entry.authors), entry.author_count), (arxiv.MAX_AUTHORS, 80))
    self.assertTrue(entry.abstract_truncated)
    self.assertLessEqual(len(entry.abstract), arxiv.MAX_ABSTRACT_CHARS)

  def test_an_empty_result_is_not_an_error(self):
    self.assertEqual(arxiv.parse_feed(feed(total=0)), (0, ()))

  def test_an_old_xml_parser_is_refused(self):
    with mock.patch.object(arxiv.pyexpat, "version_info", (2, 4, 0)):
      self.assertRefused("unsafe_parser", FIXTURE)


class IdentifierTest(unittest.TestCase):

  def test_accepted_forms(self):
    cases = {
      "1706.03762": ("1706.03762", None),
      " 1706.03762v7 ": ("1706.03762", "v7"),
      "arXiv:1706.03762": ("1706.03762", None),
      "ARXIV:2301.12345v2": ("2301.12345", "v2"),
      "https://arxiv.org/abs/1706.03762v2": ("1706.03762", "v2"),
      "arxiv.org/abs/1706.03762": ("1706.03762", None),
      "10.48550/arXiv.1706.03762": ("1706.03762", None),
      "https://doi.org/10.48550/arXiv.1706.03762": ("1706.03762", None),
      "hep-th/9901001": ("hep-th/9901001", None),
      "math.GT/0309136v2": ("math.GT/0309136", "v2"),
    }
    for text, (base, version) in cases.items():
      with self.subTest(text=text):
        parsed = arxiv.parse_identifier(text)
        self.assertEqual((parsed.base, parsed.version), (base, version))

  def test_refused_forms(self):
    for text in ("", "   ", "1706", "attention", "1706.03762v0", "../1706.03762", "1706.03762/../x",
                 "https://evil.example/abs/1706.03762", "http://localhost/1706.03762", "file:///etc/passwd",
                 "www.arxiv.org.evil/1706.03762", "1706.03\n762", "1706.03762\x00", "x" * 101, 5, None):
      with self.subTest(text=text):
        with self.assertRaises(DeskError) as caught:
          arxiv.parse_identifier(text)
        self.assertEqual(caught.exception.code, "invalid_identifier")

  def test_publisher_dois_are_not_supported_yet(self):
    for text in ("10.1145/3292500.3330701", "https://doi.org/10.1038/nature14539", "doi:10.1109/5.771073"):
      with self.subTest(text=text):
        with self.assertRaises(DeskError) as caught:
          arxiv.parse_identifier(text)
        self.assertEqual(caught.exception.code, "unsupported_doi")


class QueryTest(unittest.TestCase):

  def test_every_word_must_match_and_operators_are_dropped(self):
    params = arxiv.search_params("attention AND transformers OR (2017) title:speed")
    self.assertEqual(params["search_query"], "all:attention AND all:transformers AND all:2017 AND all:title AND all:speed")
    self.assertEqual((params["max_results"], params["start"], params["sortBy"]), ("10", "0", "relevance"))

  def test_query_bounds(self):
    for query in ("ab", "x" * 301, "AND OR NOT", "!!! ???", " ".join(["word"] * 13), None):
      with self.subTest(query=query):
        with self.assertRaises(DeskError) as caught:
          arxiv.search_params(query)
        self.assertEqual(caught.exception.code, "invalid_query")


class FetchTest(unittest.TestCase):

  def setUp(self):
    patcher = mock.patch.object(arxiv, "transport", no_network)
    patcher.start()
    self.addCleanup(patcher.stop)
    lock = temporary_state_lock()
    fake_lock = lock.__enter__()
    self.addCleanup(lock.__exit__, None, None, None)
    patcher = mock.patch.object(arxiv, "app_lock", fake_lock)
    patcher.start()
    self.addCleanup(patcher.stop)
    with fake_lock(None, "arxiv", timeout=0) as fd:
      self.state_fd = fd
    self.now = [1000.0]
    self.sleeps: list[float] = []

  def fetch(self, transport, params=None):
    with mock.patch.object(arxiv, "transport", transport):
      return arxiv.fetch(
        params or arxiv.lookup_params(arxiv.parse_identifier("1706.03762")), Path("unused"),
        clock=lambda: self.now[0], sleep=self.sleeps.append,
      )

  def set_state(self, raw: bytes):
    os.ftruncate(self.state_fd, 0)
    os.lseek(self.state_fd, 0, os.SEEK_SET)
    os.write(self.state_fd, raw)

  def stored_next_allowed(self) -> float:
    os.lseek(self.state_fd, 0, os.SEEK_SET)
    return json.loads(os.read(self.state_fd, 1024))["next_allowed"]

  def assertRefused(self, code, transport, status=None):
    with self.assertRaises(DeskError) as caught:
      self.fetch(transport)
    self.assertEqual(caught.exception.code, code)
    if status:
      self.assertEqual(caught.exception.status, status)
    return caught.exception

  def test_requests_go_only_to_the_fixed_https_endpoint(self):
    transport = FakeTransport(default=(200, ATOM, FIXTURE, {}))
    response = self.fetch(transport)
    ((url, headers, timeout),) = transport.calls
    parts = urllib.parse.urlsplit(url)
    self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "export.arxiv.org", "/api/query"))
    self.assertEqual(dict(urllib.parse.parse_qsl(parts.query)), {"id_list": "1706.03762", "max_results": "1"})
    self.assertEqual(headers["User-Agent"], arxiv.USER_AGENT)
    self.assertEqual(timeout, arxiv.TIMEOUT_SECONDS)
    self.assertEqual((response.body, response.request), (FIXTURE, "id_list=1706.03762&max_results=1"))

  def test_status_and_content_problems_are_mapped(self):
    self.assertRefused("invalid_query", FakeTransport(default=(400, "", b"", {})), 400)
    self.assertRefused("catalog_unavailable", FakeTransport(default=(500, "", b"", {})), 502)
    self.assertRefused("catalog_unavailable", FakeTransport(default=(302, "", b"", {})), 502)
    self.assertRefused("catalog_invalid_response", FakeTransport(default=(200, "text/html", b"<html/>", {})))
    big = b"x" * (arxiv.MAX_RESPONSE_BYTES + 1)
    self.assertRefused("catalog_invalid_response", FakeTransport(default=(200, ATOM, big, {})))

  def test_network_failures_give_one_safe_message(self):
    secret = "/data/apps/7/secret-path proxy.internal:3128"
    for error in (urllib.error.URLError(secret), TimeoutError(secret), ssl.SSLError(secret), ConnectionResetError(secret)):
      with self.subTest(error=type(error).__name__):
        def failing(url, headers, timeout, error=error):
          raise error
        exc = self.assertRefused("network_unavailable", failing, 503)
        self.assertEqual(exc.message, "arXiv could not be reached from Evidence Desk; try again later.")
        self.assertNotIn("secret", str(exc.to_body()))

  def test_http_protocol_failures_give_the_same_safe_message(self):
    secret = "/data/apps/7/secret-path proxy.internal:3128"
    errors = (
      http.client.IncompleteRead(secret.encode(), 10), http.client.BadStatusLine(secret),
      http.client.LineTooLong(secret), http.client.RemoteDisconnected(secret), http.client.HTTPException(secret),
    )
    for error in errors:
      with self.subTest(error=type(error).__name__):
        def failing(url, headers, timeout, error=error):
          raise error
        exc = self.assertRefused("network_unavailable", failing, 503)
        self.assertEqual(exc.message, "arXiv could not be reached from Evidence Desk; try again later.")
        self.assertNotIn("secret", str(exc.to_body()))

  def test_a_body_cut_short_while_reading_is_a_network_failure(self):
    class Response:
      status = 200
      headers = {"Content-Type": ATOM}
      def __enter__(self):
        return self
      def __exit__(self, *exc):
        return False
      def read(self, amount):
        raise http.client.IncompleteRead(b"<feed", amount)

    class Opener:
      def open(self, request, timeout):
        return Response()

    with mock.patch.object(arxiv.urllib.request, "build_opener", lambda *handlers: Opener()):
      self.assertRefused("network_unavailable", arxiv.urllib_transport, 503)

  def test_a_failed_attempt_still_spaces_the_next_request(self):
    def failing(url, headers, timeout):
      self.now[0] += 1.0
      raise http.client.IncompleteRead(b"", 10)
    self.assertRefused("network_unavailable", failing)
    self.assertEqual(self.stored_next_allowed(), 1004.0)
    self.fetch(FakeTransport(default=(200, ATOM, FIXTURE, {})))
    self.assertEqual(self.sleeps, [3.0])

  def test_an_unexpected_failure_is_not_disguised_but_keeps_the_spacing(self):
    def failing(url, headers, timeout):
      raise ValueError("unexpected")
    with self.assertRaises(ValueError):
      self.fetch(failing)
    self.fetch(FakeTransport(default=(200, ATOM, FIXTURE, {})))
    self.assertEqual(self.sleeps, [3.0])

  def test_the_next_slot_is_reserved_before_the_request_is_sent(self):
    seen = []
    def recording(url, headers, timeout):
      seen.append(self.stored_next_allowed())
      return 200, ATOM, FIXTURE, {}
    self.fetch(recording)
    self.assertEqual(seen, [1003.0])

  def test_invalid_pacing_state_neither_skips_nor_blocks_the_spacing(self):
    far_future = 1000.0 + arxiv.MAX_RETRY_AFTER_SECONDS + arxiv.MIN_INTERVAL_SECONDS + 1.0
    states = {
      "NaN": b'{"next_allowed": NaN}',
      "Infinity": b'{"next_allowed": Infinity}',
      "-Infinity": b'{"next_allowed": -Infinity}',
      "overflow": b'{"next_allowed": 1e999}',
      "negative": b'{"next_allowed": -5}',
      "boolean": b'{"next_allowed": true}',
      "string": b'{"next_allowed": "999999"}',
      "missing": b'{}',
      "list": b"[1e12]",
      "not JSON": b"next_allowed=0",
      "not UTF-8": b"\xff\xfe",
      "oversized": b'{"next_allowed": 0, "pad": "' + b"x" * 300 + b'"}',
      "far future": json.dumps({"next_allowed": far_future}).encode(),
      "very far future": b'{"next_allowed": 1e300}',
    }
    for name, raw in states.items():
      with self.subTest(state=name):
        self.sleeps.clear()
        self.set_state(raw)
        transport = FakeTransport(default=(200, ATOM, FIXTURE, {}))
        self.fetch(transport)
        self.assertEqual(self.sleeps, [arxiv.MIN_INTERVAL_SECONDS])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.stored_next_allowed(), 1003.0)

  def test_a_clock_that_went_back_does_not_block_requests(self):
    transport = FakeTransport(default=(200, ATOM, FIXTURE, {}))
    self.fetch(transport)
    self.now[0] -= 3600.0
    self.fetch(transport)
    self.assertEqual(self.sleeps, [arxiv.MIN_INTERVAL_SECONDS])
    self.assertEqual(len(transport.calls), 2)

  def test_the_longest_honoured_pause_is_still_kept(self):
    self.set_state(json.dumps({"next_allowed": 1000.0 + arxiv.MAX_RETRY_AFTER_SECONDS}).encode())
    exc = self.assertRefused("catalog_unavailable", no_network, 503)
    self.assertIn(f"about {int(arxiv.MAX_RETRY_AFTER_SECONDS) + 1} seconds", exc.message)

  def test_requests_are_spaced_by_three_seconds(self):
    transport = FakeTransport(default=(200, ATOM, FIXTURE, {}))
    self.fetch(transport)
    self.now[0] += 1.0
    self.fetch(transport)
    self.assertEqual(self.sleeps, [2.0])
    self.now[0] += 10.0
    self.fetch(transport)
    self.assertEqual(self.sleeps, [2.0])
    self.assertEqual(len(transport.calls), 3)

  def test_retry_after_is_respected_without_hammering(self):
    self.assertRefused("catalog_unavailable", FakeTransport(default=(429, "", b"", {"retry-after": "120"})), 503)
    calls = FakeTransport(default=(200, ATOM, FIXTURE, {}))
    self.now[0] += 5.0
    exc = self.assertRefused("catalog_unavailable", calls)
    self.assertIn("try again in about", exc.message)
    self.assertEqual(calls.calls, [])
    self.now[0] += 120.0
    self.fetch(calls)
    self.assertEqual(len(calls.calls), 1)


class TransportTest(unittest.TestCase):

  def test_redirects_are_refused(self):
    handler = arxiv._NoRedirect()
    request = urllib.request.Request("https://export.arxiv.org/api/query?id_list=1706.03762")
    with self.assertRaises(urllib.error.HTTPError):
      handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/")

  def test_the_real_transport_ignores_proxies_and_follows_no_redirects(self):
    built = []

    class Opener:
      def open(self, request, timeout):
        raise urllib.error.URLError("stopped before the network")

    def build_opener(*handlers):
      built.extend(handlers)
      return Opener()

    with mock.patch.object(arxiv.urllib.request, "build_opener", build_opener), \
         mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example:3128"}):
      with self.assertRaises(urllib.error.URLError):
        arxiv.urllib_transport(arxiv.ENDPOINT + "?id_list=1706.03762", {}, 1.0)
    proxies = [handler for handler in built if isinstance(handler, urllib.request.ProxyHandler)]
    self.assertEqual([handler.proxies for handler in proxies], [{}])
    self.assertTrue(any(isinstance(handler, arxiv._NoRedirect) for handler in built))


@unittest.skipUnless(POSIX, SKIP_REASON)
class AppLockTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)

  def test_pacing_state_lives_in_app_storage_and_is_shared(self):
    calls = FakeTransport(default=(200, ATOM, FIXTURE, {}))
    sleeps = []
    with mock.patch.object(arxiv, "transport", calls):
      arxiv.fetch(arxiv.search_params("attention"), self.platform.app_storage, clock=lambda: 50.0, sleep=sleeps.append)
      arxiv.fetch(arxiv.search_params("attention"), self.platform.app_storage, clock=lambda: 51.0, sleep=sleeps.append)
    self.assertEqual(sleeps, [2.0])
    self.assertTrue((self.platform.app_storage / "locks" / "app-arxiv.lock").is_file())
    self.assertEqual(list((self.platform.data_root / "projects").iterdir()), [])

  def test_a_symlinked_lock_folder_is_refused(self):
    outside = self.platform.data_root / "outside"
    outside.mkdir()
    os.symlink(outside, self.platform.app_storage / "locks")
    with mock.patch.object(arxiv, "transport", no_network):
      with self.assertRaises(DeskError) as caught:
        arxiv.fetch(arxiv.search_params("attention"), self.platform.app_storage)
    self.assertEqual(caught.exception.code, "unsafe_path")
    self.assertEqual(os.listdir(outside), [])

  def test_a_lock_that_is_not_a_regular_file_is_refused_without_waiting(self):
    lock = self.platform.app_storage / "locks" / "app-arxiv.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for kind, make in (("fifo", os.mkfifo), ("folder", os.mkdir)):
      with self.subTest(kind=kind):
        make(lock)
        outcome = []
        def run():
          try:
            with mock.patch.object(arxiv, "transport", no_network):
              arxiv.fetch(arxiv.search_params("attention"), self.platform.app_storage)
          except DeskError as exc:
            outcome.append(exc.code)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(5.0)
        self.assertFalse(worker.is_alive(), "fetch blocked on a non-regular lock file")
        self.assertEqual(outcome, ["unsafe_path"])
        (os.rmdir if kind == "folder" else os.unlink)(lock)

  def test_a_busy_lock_refuses_instead_of_waiting_forever(self):
    from desk.project_fs import app_lock
    with app_lock(self.platform.app_storage, "arxiv", timeout=1.0):
      with mock.patch.object(arxiv, "LOCK_TIMEOUT_SECONDS", 0.2), mock.patch.object(arxiv, "transport", no_network):
        with self.assertRaises(DeskError) as caught:
          arxiv.fetch(arxiv.search_params("attention"), self.platform.app_storage)
    self.assertEqual(caught.exception.code, "busy")


if __name__ == "__main__":
  unittest.main()
