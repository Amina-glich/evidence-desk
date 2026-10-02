"""``project_status``: what the calling chat is bound to, and what it holds.

This is the safe way to verify Project binding inside Möbius. It reports only
the one project derived from the trusted call (see ``binding``): its own
name and id, which project areas exist, and what they contain. It never takes
a path or project argument, never lists another project, and never follows a
symlink: an area that is a symlink or not a folder is reported as refused and
left unread.
"""

from __future__ import annotations

import json

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.project_fs import SOURCE_ID_RE, Project


LIST_LIMIT = 100
DESK_JSON_MAX_BYTES = 256 * 1024
QUESTION_MAX_CHARS = 500

# Who writes each area. The service writes only sources/ and exports/.
AREAS = (
  ("inbox", "owner"),
  ("sources", "service"),
  ("evidence", "agent"),
  ("exports", "service"),
)
AGENT_FILES = ("desk.json", "synthesis.md")


def _refused(exc: DeskError) -> dict:
  if exc.code == "unsafe_path":
    return {"state": "refused", "detail": "It is a symlink or not a regular folder or file; it was not read."}
  if exc.code == "too_large":
    return {"state": "refused", "detail": "It is larger than Evidence Desk reads."}
  raise exc


def _area(project: Project, name: str, writer: str) -> dict:
  try:
    entries, truncated = project.list_dir(name, limit=LIST_LIMIT)
  except DeskError as exc:
    if exc.code == "not_found":
      return {"written_by": writer, "state": "missing"}
    return {"written_by": writer, **_refused(exc)}
  report = {"written_by": writer, "state": "present"}
  listed, ignored = [], []
  for entry, kind in entries:
    if name == "sources":
      expected = kind == "folder" and SOURCE_ID_RE.match(entry)
    else:
      expected = kind == "file"
    (listed if expected else ignored).append(entry)
  if name == "sources":
    report["source_ids"] = sorted(listed, key=lambda value: int(value[1:]))
  else:
    report["files"] = listed
  if ignored:
    report["ignored"] = ignored
  if truncated:
    report["truncated"] = True
  return report


def _desk_json(project: Project) -> dict:
  try:
    raw = project.read_bytes("desk.json", max_bytes=DESK_JSON_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      return {"written_by": "agent", "state": "missing"}
    return {"written_by": "agent", **_refused(exc)}
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    return {"written_by": "agent", "state": "invalid", "detail": "desk.json is not valid JSON."}
  if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
    return {
      "written_by": "agent", "state": "invalid",
      "detail": f"desk.json must be an object with \"schema\": {SCHEMA_VERSION}.",
    }
  question = data.get("research_question")
  report = {"written_by": "agent", "state": "valid"}
  if isinstance(question, str) and question.strip():
    report["research_question"] = question.strip()[:QUESTION_MAX_CHARS]
  else:
    report["research_question"] = None
  return report


def _agent_file(project: Project, name: str) -> dict:
  try:
    present = project.revision(name) is not None
  except DeskError as exc:
    return {"written_by": "agent", **_refused(exc)}
  return {"written_by": "agent", "state": "present" if present else "missing"}


def project_status(binding: ProjectBinding, _arguments: dict) -> dict:
  """The status report returned to the agent."""
  with binding.open() as project:
    areas = {name: _area(project, name, writer) for name, writer in AREAS}
    files = {"desk.json": _desk_json(project), "synthesis.md": _agent_file(project, "synthesis.md")}
  pdfs = [
    name for name in areas["inbox"].get("files", [])
    if name.lower().endswith(".pdf")
  ]
  return {
    "project": {"id": binding.project_id, "name": binding.project_name},
    "bound_through": (
      "project chat" if binding.chat_id == binding.project_chat_id else "helper chat"
    ),
    "data_format": SCHEMA_VERSION,
    "areas": areas,
    "files": files,
    "inbox_pdfs": len(pdfs),
  }
