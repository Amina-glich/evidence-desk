"""Read-only arXiv metadata discovery: search and lookup.

Only one host is ever contacted: ``ENDPOINT`` (the official arXiv API over
HTTPS). Requests are built from fixed parameter names and validated values;
no caller-supplied URL is ever fetched, redirects are refused and proxy
settings are ignored, so a request cannot be steered elsewhere.

arXiv's API terms ask for no more than one request every three seconds on a
single connection, across all of an operator's machines. ``fetch`` therefore
holds an app-wide lock (``project_fs.app_lock``, outside every project) for
the whole request and keeps the next permitted time in it, including any
``Retry-After`` that arXiv sends. The next slot is reserved before a request
is sent, so a failed or interrupted attempt still counts. Stored state that
is malformed, not finite or implausibly far ahead (for example after the
clock went back) is replaced by one minimum interval from now: it can
neither skip the spacing nor block requests for longer than the largest
pause honoured. The state is cooperative between Evidence Desk processes;
it is not protected from other writers of app storage.

The Atom response is parsed with the standard library. A response with a
DOCTYPE in its first 4 KiB, or with an ENTITY declaration anywhere, is
refused; the bundled Expat must be at least 2.4.1, which limits entity
expansion; and ElementTree never loads external DTDs or entities. A DOCTYPE
after the first 4 KiB is not refused by itself, but it cannot declare
entities. Text is taken from known elements only. URLs in results are
rebuilt from the validated arXiv id, never copied from the response.

Metadata found here is discovery information, never paper evidence.
Failures become a ``DeskError`` with a fixed, safe message: no exception
text, host details or paths reach the caller.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import pyexpat
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from desk.errors import DeskError
from desk.project_fs import app_lock


ENDPOINT = "https://export.arxiv.org/api/query"
USER_AGENT = "EvidenceDesk (Mobius app)"
TIMEOUT_SECONDS = 20.0
MIN_INTERVAL_SECONDS = 3.0
# A request waits at most this long for its turn before refusing.
LOCK_TIMEOUT_SECONDS = 30.0
MAX_WAIT_SECONDS = 15.0
MAX_RETRY_AFTER_SECONDS = 300.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 10
MAX_QUERY_CHARS = 300
MAX_QUERY_TERMS = 12
MAX_IDENTIFIER_CHARS = 100
MAX_TITLE_CHARS = 1000
MAX_ABSTRACT_CHARS = 10000
MAX_AUTHORS = 50
MAX_TEXT_CHARS = 2000
EXPAT_MINIMUM = (2, 4, 1)

_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
       "opensearch": "http://a9.com/-/spec/opensearch/1.1/"}
_NEW_ID = re.compile(r"^(\d{4}\.\d{4,5})(v[1-9]\d{0,2})?$")
_OLD_ID = re.compile(r"^([a-z]+(?:-[a-z]+)*(?:\.[A-Z]{2})?/\d{7})(v[1-9]\d{0,2})?$")
_ABS_PREFIXES = ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv.org/abs/")
_ARXIV_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)?10\.48550/arxiv\.(.+)$", re.IGNORECASE)
_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)?10\.\d{4,9}/\S+$", re.IGNORECASE)
_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
_OPERATORS = {"and", "or", "andnot", "not"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CATEGORY = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,40}$")
# Failures of the connection or of the HTTP exchange, including a body cut
# short while it is read (http.client.IncompleteRead).
_NETWORK_ERRORS = (OSError, http.client.HTTPException)
_STATE_MAX_BYTES = 256


@dataclass(frozen=True)
class ArxivId:
  base: str
  version: str | None = None

  @property
  def text(self) -> str:
    return self.base + (self.version or "")


@dataclass(frozen=True)
class Entry:
  """One arXiv record, as the API described it."""
  arxiv_id: ArxivId
  title: str
  authors: tuple[str, ...]
  author_count: int
  abstract: str
  abstract_truncated: bool
  primary_category: str | None
  categories: tuple[str, ...]
  published: str | None
  updated: str | None
  comment: str | None
  journal_ref: str | None
  publisher_doi: str | None

  @property
  def abs_url(self) -> str:
    """Canonical abstract page: always the latest version."""
    return f"https://arxiv.org/abs/{self.arxiv_id.base}"

  @property
  def version_url(self) -> str:
    return f"https://arxiv.org/abs/{self.arxiv_id.text}"

  @property
  def pdf_url(self) -> str:
    return f"https://arxiv.org/pdf/{self.arxiv_id.text}"

  @property
  def arxiv_doi(self) -> str:
    return f"10.48550/arXiv.{self.arxiv_id.base}"


@dataclass(frozen=True)
class Response:
  """One successful API exchange, for provenance."""
  request: str
  body: bytes
  retrieved_at: str


# Identifiers and queries.

def parse_identifier(value: str) -> ArxivId:
  """An arXiv id from an id, ``arXiv:`` id, abstract-page address or arXiv DOI.

  Addresses are only read as text, never fetched. Publisher DOIs are not
  supported in this version.
  """
  text = value.strip() if isinstance(value, str) else ""
  if not text or len(text) > MAX_IDENTIFIER_CHARS or any(ord(char) < 32 for char in text):
    raise DeskError("invalid_identifier", f"Give an arXiv id of at most {MAX_IDENTIFIER_CHARS} characters, such as 1706.03762.")
  doi = _ARXIV_DOI.match(text)
  if doi:
    text = doi.group(1)
  elif _DOI.match(text):
    raise DeskError(
      "unsupported_doi",
      "Only arXiv identifiers and arXiv DOIs (10.48550/arXiv.…) are supported; publisher DOIs are not yet.",
    )
  else:
    lowered = text.lower()
    for prefix in _ABS_PREFIXES:
      if lowered.startswith(prefix):
        text = text[len(prefix):]
        break
    else:
      if "://" in text or text.lower().startswith("www."):
        raise DeskError("invalid_identifier", "Only arXiv identifiers are accepted, not web addresses of other sites.")
    if text.lower().startswith("arxiv:"):
      text = text[len("arxiv:"):]
  for pattern in (_NEW_ID, _OLD_ID):
    match = pattern.match(text)
    if match:
      return ArxivId(match.group(1), match.group(2))
  raise DeskError("invalid_identifier", "This is not a valid arXiv id (for example 1706.03762 or hep-th/9901001).")


def search_terms(query: str) -> tuple[str, ...]:
  """The words of a free-text query, without arXiv query operators."""
  text = query.strip() if isinstance(query, str) else ""
  if not 3 <= len(text) <= MAX_QUERY_CHARS:
    raise DeskError("invalid_query", f"The search query must be 3-{MAX_QUERY_CHARS} characters.")
  terms = [term for term in _TERM.findall(text) if term.lower() not in _OPERATORS]
  if not terms:
    raise DeskError("invalid_query", "The search query has no searchable words.")
  if len(terms) > MAX_QUERY_TERMS:
    raise DeskError("invalid_query", f"Use at most {MAX_QUERY_TERMS} search words.")
  return tuple(terms)


def search_params(query: str) -> dict[str, str]:
  """Every word must appear somewhere in the record (``all:`` field)."""
  terms = search_terms(query)
  return {
    "search_query": " AND ".join(f"all:{term}" for term in terms),
    "start": "0",
    "max_results": str(MAX_RESULTS),
    "sortBy": "relevance",
    "sortOrder": "descending",
  }


def lookup_params(arxiv_id: ArxivId) -> dict[str, str]:
  return {"id_list": arxiv_id.text, "max_results": "1"}


# Transport.

class _NoRedirect(urllib.request.HTTPRedirectHandler):
  """Redirects could lead to another host; treat them as failures."""

  def redirect_request(self, req, fp, code, msg, headers, newurl):
    raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str, bytes, dict[str, str]]:
  """GET ``url``; returns (status, content type, body, selected headers)."""
  opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect(),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
  )
  request = urllib.request.Request(url, headers=headers, method="GET")
  try:
    with opener.open(request, timeout=timeout) as response:
      body = response.read(MAX_RESPONSE_BYTES + 1)
      return response.status, response.headers.get("Content-Type", ""), body, {
        "retry-after": response.headers.get("Retry-After", ""),
      }
  except urllib.error.HTTPError as exc:
    retry = exc.headers.get("Retry-After", "") if exc.headers is not None else ""
    return exc.code, "", b"", {"retry-after": retry}


# Tests replace this; the service uses the real transport.
transport = urllib_transport


def _retry_after(value: str) -> float | None:
  return min(float(value), MAX_RETRY_AFTER_SECONDS) if value.strip().isdigit() else None


def _reject_constant(value: str):
  raise ValueError(f"invalid JSON constant: {value}")


def _read_next_allowed(fd: int, now: float) -> float:
  """The stored next permitted time, or ``now`` plus one interval if it is unusable.

  An empty file (a new lock) means no earlier request. Anything malformed,
  not a finite non-negative number, or further ahead than the longest pause
  ``fetch`` itself could have stored, is replaced by the safe fallback.
  """
  fallback = now + MIN_INTERVAL_SECONDS
  try:
    if os.fstat(fd).st_size > _STATE_MAX_BYTES:
      return fallback
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, _STATE_MAX_BYTES + 1)
    if not raw:
      return 0.0
    data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
  except (OSError, ValueError, RecursionError):
    return fallback
  value = data.get("next_allowed") if isinstance(data, dict) else None
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return fallback
  value = float(value)
  if not math.isfinite(value) or value < 0 or value > now + MAX_RETRY_AFTER_SECONDS + MIN_INTERVAL_SECONDS:
    return fallback
  return value


def _write_next_allowed(fd: int, when: float) -> None:
  data = json.dumps({"next_allowed": when}).encode("utf-8")
  os.ftruncate(fd, 0)
  os.lseek(fd, 0, os.SEEK_SET)
  os.write(fd, data)


def fetch(params: dict[str, str], app_storage_dir: Path, *, clock=time.time, sleep=time.sleep) -> Response:
  """One paced GET of the arXiv API, or a safe ``DeskError``."""
  query = urllib.parse.urlencode(params)
  url = f"{ENDPOINT}?{query}"
  with app_lock(app_storage_dir, "arxiv", timeout=LOCK_TIMEOUT_SECONDS) as fd:
    now = clock()
    wait = _read_next_allowed(fd, now) - now
    if wait > MAX_WAIT_SECONDS:
      raise DeskError(
        "catalog_unavailable",
        f"arXiv asked for a pause; try again in about {int(wait) + 1} seconds.",
        status=503,
      )
    if wait > 0:
      sleep(wait)
    # Reserve the next slot before sending: an attempt that fails, or a
    # process stopped mid-request, still keeps the spacing.
    _write_next_allowed(fd, clock() + MIN_INTERVAL_SECONDS)
    try:
      status, content_type, body, headers = transport(url, {"User-Agent": USER_AGENT, "Accept": "application/atom+xml"}, TIMEOUT_SECONDS)
    except BaseException as exc:
      _write_next_allowed(fd, clock() + MIN_INTERVAL_SECONDS)
      if isinstance(exc, _NETWORK_ERRORS):
        raise DeskError(
          "network_unavailable", "arXiv could not be reached from Evidence Desk; try again later.", status=503,
        ) from exc
      raise
    pause = _retry_after(headers.get("retry-after", "")) if status in (429, 503) else None
    _write_next_allowed(fd, clock() + max(MIN_INTERVAL_SECONDS, pause or 0.0))
  if status == 400:
    raise DeskError("invalid_query", "arXiv did not accept this search.", status=400)
  if status in (429, 503):
    raise DeskError("catalog_unavailable", "arXiv is limiting requests; try again later.", status=503)
  if status != 200:
    raise DeskError("catalog_unavailable", f"arXiv answered with status {status}; try again later.", status=502)
  if content_type.split(";")[0].strip().lower() not in ("application/atom+xml", "application/xml", "text/xml"):
    raise DeskError("catalog_invalid_response", "arXiv returned an unexpected kind of response.", status=502)
  if len(body) > MAX_RESPONSE_BYTES:
    raise DeskError("catalog_invalid_response", "arXiv returned more data than Evidence Desk reads.", status=502)
  return Response(query, body, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock())))


# Parsing.

def _clean(text: str | None, limit: int) -> str | None:
  if text is None:
    return None
  value = " ".join(text.split())
  return value[:limit] if value else None


def parse_feed(body: bytes) -> tuple[int, tuple[Entry, ...]]:
  """(total results, entries) of an arXiv Atom response."""
  if pyexpat.version_info < EXPAT_MINIMUM:
    raise DeskError("unsafe_parser", "This Python's XML parser is too old to read arXiv safely.", status=503)
  if len(body) > MAX_RESPONSE_BYTES or b"<!doctype" in body[:4096].lower() or b"<!entity" in body.lower():
    raise DeskError("catalog_invalid_response", "arXiv returned a response Evidence Desk does not read.", status=502)
  try:
    root = ET.fromstring(body)
  except ET.ParseError as exc:
    raise DeskError("catalog_invalid_response", "arXiv returned a response that is not valid XML.", status=502) from exc
  if root.tag != f"{{{_NS['atom']}}}feed":
    raise DeskError("catalog_invalid_response", "arXiv returned an unexpected document.", status=502)
  total_text = root.findtext("opensearch:totalResults", default="0", namespaces=_NS).strip()
  total = int(total_text) if total_text.isdigit() else 0
  entries = []
  for element in root.findall("atom:entry", _NS):
    entry_id = (element.findtext("atom:id", default="", namespaces=_NS) or "").strip()
    if entry_id.startswith(("http://arxiv.org/api/errors", "https://arxiv.org/api/errors")):
      raise DeskError("invalid_identifier", "arXiv did not recognize this identifier.", status=404)
    for prefix in ("http://arxiv.org/abs/", "https://arxiv.org/abs/"):
      if entry_id.startswith(prefix):
        entry_id = entry_id[len(prefix):]
        break
    else:
      continue
    try:
      arxiv_id = parse_identifier(entry_id)
    except DeskError:
      continue
    title = _clean(element.findtext("atom:title", namespaces=_NS), MAX_TITLE_CHARS)
    if not title:
      continue
    raw_abstract = " ".join((element.findtext("atom:summary", default="", namespaces=_NS) or "").split())
    names = [
      name for name in (
        _clean(author.findtext("atom:name", namespaces=_NS), 200) for author in element.findall("atom:author", _NS)
      ) if name
    ]
    categories = tuple(dict.fromkeys(
      term for term in (category.get("term", "") for category in element.findall("atom:category", _NS))
      if _CATEGORY.match(term)
    ))
    primary = element.find("arxiv:primary_category", _NS)
    primary_term = primary.get("term", "") if primary is not None else ""
    dates = {}
    for name in ("published", "updated"):
      value = (element.findtext(f"atom:{name}", default="", namespaces=_NS) or "").strip()
      dates[name] = value if _DATE.match(value) else None
    doi = _clean(element.findtext("arxiv:doi", namespaces=_NS), 200)
    entries.append(Entry(
      arxiv_id=arxiv_id,
      title=title,
      authors=tuple(names[:MAX_AUTHORS]),
      author_count=len(names),
      abstract=raw_abstract[:MAX_ABSTRACT_CHARS],
      abstract_truncated=len(raw_abstract) > MAX_ABSTRACT_CHARS,
      primary_category=primary_term if _CATEGORY.match(primary_term) else None,
      categories=categories,
      published=dates["published"],
      updated=dates["updated"],
      comment=_clean(element.findtext("arxiv:comment", namespaces=_NS), MAX_TEXT_CHARS),
      journal_ref=_clean(element.findtext("arxiv:journal_ref", namespaces=_NS), MAX_TEXT_CHARS),
      publisher_doi=doi if doi and _DOI.match(doi) else None,
    ))
  return total, tuple(entries)
