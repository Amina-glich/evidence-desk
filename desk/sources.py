"""Registered sources: ``sources/S<n>/source.json`` and ``pages.json``.

``add_source`` registers one owner-uploaded PDF from ``inbox/``. Text is
extracted outside the lock, staged privately, and published as the next
``S<n>`` under ``project_lock``, so a source appears complete or not at all.
The same PDF bytes (SHA-256) are registered once.

``source.json`` records the PDF's digest and the SHA-256 of ``pages.json``.
``load_source`` refuses page text whose digest no longer matches, so evidence
is never checked against text that changed after registration. This detects
accidental edits; it is not a defense against a writer that also rewrites
``source.json``.

Reading sources needs no PDF library: the comparison view's Project builder
runs on the platform's own Python, without the app's pypdf environment, so
``pdf_text`` is imported only when a PDF is registered.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.project_fs import SOURCE_ID_RE, Project, project_lock, split_relative


SOURCE_JSON = "source.json"
PAGES_JSON = "pages.json"
SOURCE_JSON_MAX_BYTES = 64 * 1024
PAGES_JSON_MAX_BYTES = 48 * 1024 * 1024
REPLY_PAGE_LIST_MAX = 50


@dataclass(frozen=True)
class Source:
  source_id: str
  record: dict
  # Index 0 is page 1 of the PDF.
  pages: tuple[str, ...]

  @property
  def title(self) -> str | None:
    return self.record.get("title")


def inbox_pdf_path(value: str) -> str:
  """The canonical project path of an inbox PDF named by the agent."""
  parts = split_relative(value)
  if (
    len(parts) < 2
    or parts[0] != "inbox"
    or any(part.startswith(".") for part in parts)
    or not parts[-1].lower().endswith(".pdf")
  ):
    raise DeskError(
      "invalid_arguments",
      "'file' must be a PDF inside inbox/, for example inbox/paper.pdf.",
    )
  return "/".join(parts)


def _sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
  return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_record(project: Project, source_id: str) -> dict:
  try:
    raw = project.read_bytes(f"sources/{source_id}/{SOURCE_JSON}", max_bytes=SOURCE_JSON_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      raise DeskError("source_missing", f"{source_id} has no {SOURCE_JSON}.", status=409) from exc
    raise
  try:
    record = json.loads(raw)
  except (ValueError, RecursionError):
    record = None
  if (
    not isinstance(record, dict)
    or record.get("schema") != SCHEMA_VERSION
    or not isinstance(record.get("sha256"), str)
    or not isinstance(record.get("pages_sha256"), str)
    or not isinstance(record.get("page_count"), int)
    or isinstance(record.get("page_count"), bool)
  ):
    raise DeskError("source_invalid", f"{source_id}/{SOURCE_JSON} is not a valid source record.", status=409)
  return record


def load_source(project: Project, source_id: str) -> Source:
  """A registered source with page text that still matches its record."""
  if not SOURCE_ID_RE.match(source_id):
    raise DeskError("source_missing", f"'{source_id}' is not a source id.", status=409)
  record = _read_record(project, source_id)
  try:
    raw = project.read_bytes(f"sources/{source_id}/{PAGES_JSON}", max_bytes=PAGES_JSON_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      raise DeskError("source_missing", f"{source_id} has no {PAGES_JSON}.", status=409) from exc
    raise
  if _sha256(raw) != record["pages_sha256"]:
    raise DeskError(
      "source_modified",
      f"The page text of {source_id} changed after it was registered; it is not trusted.",
      status=409,
    )
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    data = None
  pages = data.get("pages") if isinstance(data, dict) else None
  if (
    not isinstance(pages, list)
    or len(pages) != record["page_count"]
    or any(
      not isinstance(page, dict) or page.get("page") != index + 1
      or not isinstance(page.get("text"), str)
      for index, page in enumerate(pages)
    )
  ):
    raise DeskError("source_invalid", f"{source_id}/{PAGES_JSON} is not valid page text.", status=409)
  return Source(source_id, record, tuple(page["text"] for page in pages))


def find_by_digest(project: Project, digest: str) -> str | None:
  """The id of a registered source made from these exact PDF bytes."""
  for source_id in project.source_ids():
    try:
      record = _read_record(project, source_id)
    except DeskError:
      continue
    if record["sha256"] == digest:
      return source_id
  return None


def _reply(source_id: str, created: bool, record: dict) -> dict:
  empty = record.get("pages_without_text") or []
  reply = {
    "source_id": source_id,
    "created": created,
    "file": record.get("file"),
    "title": record.get("title"),
    "pages": record.get("page_count"),
    "pages_without_text": empty[:REPLY_PAGE_LIST_MAX],
  }
  if not created:
    reply["note"] = f"This PDF is already registered as {source_id}."
  elif empty:
    reply["note"] = (
      "Some pages have no extractable text (figures or scanned pages); "
      "findings on those pages cannot be quote-checked."
    )
  return reply


def add_source(binding: ProjectBinding, arguments: dict) -> dict:
  from desk.pdf_text import EXTRACTOR, MAX_PDF_BYTES, extract_pages

  path = inbox_pdf_path(arguments["file"])
  with binding.open() as project:
    data = project.read_bytes(path, max_bytes=MAX_PDF_BYTES)
    digest = _sha256(data)
    known = find_by_digest(project, digest)
    if known is not None:
      return _reply(known, False, _read_record(project, known))
    text = extract_pages(data)
    pages_bytes = _json_bytes({
      "schema": SCHEMA_VERSION,
      "pages": [{"page": index + 1, "text": page} for index, page in enumerate(text.pages)],
    })
    record = {
      "schema": SCHEMA_VERSION,
      "kind": "pdf",
      "file": path,
      "sha256": digest,
      "bytes": len(data),
      "title": text.title,
      "page_count": len(text.pages),
      "pages_without_text": [
        index + 1 for index, page in enumerate(text.pages) if not page.strip()
      ],
      "pages_sha256": _sha256(pages_bytes),
      "extractor": EXTRACTOR,
    }
    with project.staged_source() as stage:
      stage.write(PAGES_JSON, pages_bytes)
      stage.write(SOURCE_JSON, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
      with project_lock(binding.app_storage_dir, binding.project_id) as lock:
        source_id, created = project.publish_source(
          stage, lock, existing=lambda current: find_by_digest(current, digest),
        )
    if not created:
      record = _read_record(project, source_id)
  return _reply(source_id, created, record)
