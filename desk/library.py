"""The project's reference library and the arXiv discovery tools.

``library/references.json`` holds bibliographic records the owner chose to
save. It is written only by the service (``project_fs.SERVICE_OWNED_ROOTS``)
and kept apart from ``inbox/`` PDFs, ``sources/`` page text, ``evidence/`` and
``exports/``. A record is discovery information, never evidence: findings
still need the paper's PDF registered with ``add_source`` and quotations
verified against its pages, and nothing here reads or changes those files.

``save_reference`` takes only an identifier. The record is built from the
response arXiv returns to the service, never from caller-supplied metadata,
and keeps provenance: the request, when it was made, and the SHA-256 of the
exact response. The network request happens before the project lock is
taken; the library is then read, checked for duplicates and replaced
atomically under ``project_lock`` with a revision check. A library that is
not valid is refused, never overwritten. No PDF is downloaded.
"""

from __future__ import annotations

import hashlib
import json
import re

from desk import arxiv
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.project_fs import Project, project_lock


LIBRARY_PATH = "library/references.json"
LIBRARY_SCHEMA = 1
LIBRARY_MAX_BYTES = 4 * 1024 * 1024
MAX_REFERENCES = 500
SEARCH_ABSTRACT_CHARS = 1500
_REFERENCE_ID = re.compile(r"^L([1-9][0-9]{0,5})$")

DISCOVERY_NOTE = (
  "Bibliographic metadata for discovery, not evidence. Findings need the paper's PDF "
  "uploaded to inbox/, registered with add_source, and page-verified quotations."
)
FULL_TEXT_NOTE = (
  "Not retrieved. The arXiv API does not report the paper's license; the owner downloads "
  "the PDF only where its terms allow and uploads it to inbox/."
)
METADATA_LICENSE = "arXiv descriptive metadata, CC0 1.0"


def _title_key(title: str) -> str:
  return " ".join(re.sub(r"[^0-9a-z]+", " ", title.casefold()).split())


def read_library(project: Project) -> tuple[list[dict], str | None]:
  """(references, revision) of the library; revision None when it does not exist."""
  try:
    raw = project.read_bytes(LIBRARY_PATH, max_bytes=LIBRARY_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      return [], None
    raise
  invalid = DeskError(
    "library_invalid",
    f"{LIBRARY_PATH} is not a valid Evidence Desk library; it was not changed.",
    status=409,
  )
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    raise invalid from None
  references = data.get("references") if isinstance(data, dict) else None
  if (
    not isinstance(data, dict) or data.get("schema") != LIBRARY_SCHEMA or not isinstance(references, list)
    or any(
      not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _REFERENCE_ID.match(item["id"])
      or not isinstance(item.get("arxiv_id"), str)
      for item in references
    )
    or len({item["id"] for item in references}) != len(references)
  ):
    raise invalid
  return references, hashlib.sha256(raw).hexdigest()


def _in_library(references: list[dict], entry: arxiv.Entry) -> str | None:
  for reference in references:
    if reference.get("arxiv_id") == entry.arxiv_id.base:
      return reference["id"]
    doi = reference.get("publisher_doi")
    if entry.publisher_doi and isinstance(doi, str) and doi.lower() == entry.publisher_doi.lower():
      return reference["id"]
  return None


def _possible_duplicates(references: list[dict], entry: arxiv.Entry) -> list[str]:
  key = _title_key(entry.title)
  return [
    reference["id"] for reference in references
    if isinstance(reference.get("title"), str) and _title_key(reference["title"]) == key
  ]


def _summary(entry: arxiv.Entry, *, abstract_chars: int | None = None) -> dict:
  abstract = entry.abstract
  truncated = entry.abstract_truncated
  if abstract_chars is not None and len(abstract) > abstract_chars:
    abstract, truncated = abstract[:abstract_chars - 1] + "…", True
  return {
    "arxiv_id": entry.arxiv_id.base,
    "version": entry.arxiv_id.version,
    "title": entry.title,
    "authors": list(entry.authors),
    "author_count": entry.author_count,
    "published": entry.published,
    "updated": entry.updated,
    "primary_category": entry.primary_category,
    "categories": list(entry.categories),
    "journal_ref": entry.journal_ref,
    "comment": entry.comment,
    "publisher_doi": entry.publisher_doi,
    "arxiv_doi": entry.arxiv_doi,
    "abstract": abstract,
    "abstract_truncated": truncated,
    "abs_url": entry.abs_url,
  }


def _record(reference_id: str, entry: arxiv.Entry, response: arxiv.Response) -> dict:
  return {
    "id": reference_id,
    "catalog": "arxiv",
    **_summary(entry),
    "version_url": entry.version_url,
    "pdf_url": entry.pdf_url,
    "full_text": FULL_TEXT_NOTE,
    "use": DISCOVERY_NOTE,
    "metadata_license": METADATA_LICENSE,
    "provenance": {
      "endpoint": arxiv.ENDPOINT,
      "request": response.request,
      "retrieved_at": response.retrieved_at,
      "response_sha256": hashlib.sha256(response.body).hexdigest(),
    },
  }


def _lookup(binding: ProjectBinding, arxiv_id: arxiv.ArxivId) -> tuple[arxiv.Entry, arxiv.Response]:
  response = arxiv.fetch(arxiv.lookup_params(arxiv_id), binding.app_storage_dir)
  _total, entries = arxiv.parse_feed(response.body)
  for entry in entries:
    if entry.arxiv_id.base == arxiv_id.base and arxiv_id.version in (None, entry.arxiv_id.version):
      return entry, response
  raise DeskError("not_in_catalog", "arXiv has no record with this identifier.", status=404)


def _library_or_none(binding: ProjectBinding) -> tuple[list[dict] | None, str | None]:
  """The library for duplicate hints; None with a note when it cannot be read."""
  try:
    with binding.open() as project:
      references, _revision = read_library(project)
    return references, None
  except DeskError as exc:
    if exc.code in ("library_invalid", "unsafe_path", "too_large"):
      return None, f"The project library could not be read ({exc.message}); in_library is unknown."
    raise


def search_literature(binding: ProjectBinding, arguments: dict) -> dict:
  """The ``search_literature`` tool: read-only arXiv search."""
  params = arxiv.search_params(arguments["query"])
  references, library_note = _library_or_none(binding)
  response = arxiv.fetch(params, binding.app_storage_dir)
  total, entries = arxiv.parse_feed(response.body)
  results = []
  for entry in entries:
    result = _summary(entry, abstract_chars=SEARCH_ABSTRACT_CHARS)
    result["in_library"] = None if references is None else _in_library(references, entry)
    results.append(result)
  reply = {
    "catalog": "arxiv",
    "query": " ".join(arxiv.search_terms(arguments["query"])),
    "retrieved_at": response.retrieved_at,
    "total_results": total,
    "results": results,
    "note": DISCOVERY_NOTE + " Save a paper with save_reference only when the owner chooses it.",
  }
  if library_note:
    reply["library_note"] = library_note
  return reply


def lookup_reference(binding: ProjectBinding, arguments: dict) -> dict:
  """The ``lookup_reference`` tool: one arXiv record, read-only."""
  arxiv_id = arxiv.parse_identifier(arguments["identifier"])
  references, library_note = _library_or_none(binding)
  entry, response = _lookup(binding, arxiv_id)
  result = _summary(entry)
  result["in_library"] = None if references is None else _in_library(references, entry)
  reply = {"catalog": "arxiv", "retrieved_at": response.retrieved_at, "result": result, "note": DISCOVERY_NOTE}
  if library_note:
    reply["library_note"] = library_note
  return reply


def save_reference(binding: ProjectBinding, arguments: dict) -> dict:
  """The ``save_reference`` tool: add one owner-selected arXiv record."""
  arxiv_id = arxiv.parse_identifier(arguments["identifier"])
  # Refuse an unusable library before contacting arXiv.
  with binding.open() as project:
    read_library(project)
  entry, response = _lookup(binding, arxiv_id)
  with binding.open() as project:
    with project_lock(binding.app_storage_dir, binding.project_id):
      references, revision = read_library(project)
      existing = _in_library(references, entry)
      if existing is not None:
        saved = next(reference for reference in references if reference["id"] == existing)
        reply = {
          "reference_id": existing, "created": False,
          "note": f"This paper is already in the library as {existing}; nothing was changed.",
        }
        if saved.get("version") != entry.arxiv_id.version:
          reply["note"] += (
            f" The saved record is version {saved.get('version')}; arXiv now has {entry.arxiv_id.version}."
          )
        return reply
      if len(references) >= MAX_REFERENCES:
        raise DeskError("too_large", f"The library already holds {MAX_REFERENCES} references.", status=409)
      numbers = [int(_REFERENCE_ID.match(reference["id"]).group(1)) for reference in references]
      reference_id = f"L{max(numbers, default=0) + 1}"
      record = _record(reference_id, entry, response)
      possible = _possible_duplicates(references, entry)
      data = (json.dumps(
        {"schema": LIBRARY_SCHEMA, "references": [*references, record]}, ensure_ascii=False, indent=2,
      ) + "\n").encode("utf-8")
      if len(data) > LIBRARY_MAX_BYTES:
        raise DeskError("too_large", "The library would exceed the size Evidence Desk reads.", status=409)
      project.write_atomic(LIBRARY_PATH, data, expected_revision=revision)
  reply = {"reference_id": reference_id, "created": True, "reference": record, "note": DISCOVERY_NOTE}
  if possible:
    reply["possible_duplicates"] = possible
  return reply
