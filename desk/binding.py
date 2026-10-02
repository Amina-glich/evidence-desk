"""Trusted binding from an app tool call to its Evidence Desk Project.

An app tool call carries a platform-set ``call.chat_id`` (taken from the
calling agent run's own token, never from the model) but no project. Tool
arguments are model-written and untrusted, so Evidence Desk tools accept no
``project_id`` at all. The project is derived here instead:

  calling chat --(delegations.child_chat_id -> parent_chat_id)*--> the chat
  that carries ``chats.project_id`` --> the ``projects`` row.

The platform exposes no app-token API for this lookup, so it reads the
platform database strictly read-only (SQLite ``mode=ro`` plus
``query_only``). That schema is internal: every table and column used is
checked first, and any missing piece, missing row, or mismatch refuses the
call. The bound project must be live, created from this app's template
(``source_app_id == APP_ID``), and stored exactly at
``<data root>/projects/<project id>``.

If the platform later passes a trusted project id in the call itself, it
should replace the database read here; the checks below stay the same.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import quote

from desk.errors import DeskError
from desk.project_fs import UUID_RE, Project, open_project


MAX_DELEGATION_DEPTH = 8
DB_RELATIVE = ("db", "ultimate.db")
REQUIRED_COLUMNS = {
  "chats": frozenset({"id", "project_id", "deleted_at"}),
  "delegations": frozenset({"parent_chat_id", "child_chat_id"}),
  "projects": frozenset({"id", "name", "root_path", "source_app_id", "deleted_at"}),
}


@dataclass(frozen=True)
class ProjectBinding:
  """The one project a tool call may act on, and how it was reached."""

  project_id: str
  project_name: str
  data_root: Path
  app_storage_dir: Path
  app_id: int
  # The chat that made the call (a helper chat for delegated work).
  chat_id: str
  # The project chat reached from ``chat_id``; its uploads belong to the user.
  project_chat_id: str

  def open(self) -> Project:
    """Open the bound project root, refusing symlinks on the way."""
    return open_project(self.data_root, self.project_id)


def resolve_platform_paths(env: Mapping[str, str]) -> tuple[Path, Path, int]:
  """Return (data root, app storage dir, app id) from the service environment.

  ``APP_STORAGE_DIR`` is always set by the platform as
  ``<data_dir>/apps/<app id>``, so the data root is derived from it.
  ``DATA_DIR`` is only inherited when the platform process has it; when
  present it must agree.
  """
  app_id_raw = env.get("APP_ID") or ""
  storage_raw = env.get("APP_STORAGE_DIR") or ""
  if not app_id_raw.isdigit() or int(app_id_raw) <= 0 or not storage_raw:
    raise DeskError(
      "platform_unavailable",
      "The service did not receive its Möbius app identity.",
      status=503,
    )
  app_id = int(app_id_raw)
  if not os.path.isabs(storage_raw):
    raise DeskError(
      "platform_unavailable", "The app storage path is not absolute.", status=503,
    )
  storage = Path(os.path.normpath(storage_raw))
  if storage.name != str(app_id) or storage.parent.name != "apps":
    raise DeskError(
      "platform_unavailable",
      "The app storage path does not have the expected Möbius layout.",
      status=503,
    )
  data_root = storage.parent.parent
  data_dir = env.get("DATA_DIR")
  if data_dir and os.path.normpath(data_dir) != str(data_root):
    raise DeskError(
      "data_root_mismatch",
      "DATA_DIR and APP_STORAGE_DIR disagree about the Möbius data folder.",
      status=503,
    )
  return data_root, storage, app_id


def _sqlite_uri(path: Path) -> str:
  posix = path.as_posix()
  if not posix.startswith("/"):
    # A Windows drive path; SQLite expects file:///C:/... there.
    posix = "/" + posix
  return f"file:{quote(posix, safe='/:')}?mode=ro"


def connect_readonly(data_root: Path) -> sqlite3.Connection:
  """Open the platform database read-only, refusing a symlinked file."""
  db_path = data_root.joinpath(*DB_RELATIVE)
  try:
    info = os.lstat(db_path)
  except OSError as exc:
    raise DeskError(
      "platform_unavailable", "The Möbius database is unavailable.", status=503,
    ) from exc
  if not stat.S_ISREG(info.st_mode):
    raise DeskError(
      "platform_unavailable", "The Möbius database is not a regular file.",
      status=503,
    )
  try:
    conn = sqlite3.connect(
      _sqlite_uri(db_path), uri=True, timeout=5.0, isolation_level=None,
    )
    conn.execute("PRAGMA query_only = ON")
  except sqlite3.Error as exc:
    raise DeskError(
      "platform_unavailable", "The Möbius database could not be opened.",
      status=503,
    ) from exc
  return conn


def require_schema(conn: sqlite3.Connection) -> None:
  """Refuse unless every table and column this lookup reads still exists."""
  for table, needed in REQUIRED_COLUMNS.items():
    # Table names come from the constant above, never from input.
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not needed <= present:
      raise DeskError(
        "platform_schema_changed",
        "This Möbius version stores projects differently; Evidence Desk "
        "needs an update before it can verify the project.",
        status=503,
      )


def _require_uuid(value: object, code: str, message: str) -> str:
  if not isinstance(value, str) or not UUID_RE.match(value):
    raise DeskError(code, message, status=403)
  return value


def walk_to_project_chat(conn: sqlite3.Connection, chat_id: str) -> tuple[str, str]:
  """Follow helper chats to their project chat; returns (chat id, project id)."""
  current = chat_id
  visited: set[str] = set()
  for _ in range(MAX_DELEGATION_DEPTH + 1):
    if current in visited:
      raise DeskError(
        "delegation_cycle", "The helper chat chain loops.", status=403,
      )
    visited.add(current)
    row = conn.execute(
      "SELECT project_id, deleted_at FROM chats WHERE id = ?", (current,),
    ).fetchone()
    if row is None:
      raise DeskError("chat_not_found", "The calling chat does not exist.", status=403)
    project_id, deleted_at = row
    if deleted_at is not None:
      raise DeskError("chat_deleted", "The calling chat was deleted.", status=403)
    if project_id:
      return current, _require_uuid(
        project_id, "invalid_project", "The chat's project id is not valid.",
      )
    parent = conn.execute(
      "SELECT parent_chat_id FROM delegations WHERE child_chat_id = ?",
      (current,),
    ).fetchone()
    if parent is None:
      raise DeskError(
        "not_project_chat",
        "Evidence Desk tools only work in a chat inside an Evidence Desk project.",
        status=403,
      )
    current = _require_uuid(
      parent[0], "invalid_call", "A helper chat has an invalid parent.",
    )
  raise DeskError(
    "delegation_too_deep", "The helper chat chain is too deep.", status=403,
  )


def _root_matches(stored: object, data_root: Path, project_id: str) -> bool:
  if not isinstance(stored, str) or not stored:
    return False
  if os.path.isabs(stored):
    expected = os.path.normpath(str(data_root / "projects" / project_id))
    return os.path.normpath(stored) == expected
  return PurePosixPath(stored) == PurePosixPath("projects", project_id)


def bind_call(call: object, env: Mapping[str, str]) -> ProjectBinding:
  """Bind a tool call to its verified Evidence Desk project, or refuse."""
  if not isinstance(call, Mapping):
    raise DeskError("invalid_call", "The tool call has no trusted identity.")
  chat_id = call.get("chat_id")
  if not isinstance(chat_id, str) or not UUID_RE.match(chat_id):
    raise DeskError(
      "invalid_call", "The tool call has no trusted chat identity.", status=403,
    )
  data_root, storage, app_id = resolve_platform_paths(env)
  conn = connect_readonly(data_root)
  try:
    require_schema(conn)
    project_chat_id, project_id = walk_to_project_chat(conn, chat_id)
    row = conn.execute(
      "SELECT id, name, root_path, source_app_id, deleted_at "
      "FROM projects WHERE id = ?",
      (project_id,),
    ).fetchone()
  except sqlite3.Error as exc:
    raise DeskError(
      "platform_unavailable", "The Möbius database could not be read.",
      status=503,
    ) from exc
  finally:
    conn.close()
  if row is None:
    raise DeskError("project_not_found", "The chat's project does not exist.", status=403)
  row_id, name, root_path, source_app_id, deleted_at = row
  if row_id != project_id:
    raise DeskError("project_not_found", "The chat's project does not exist.", status=403)
  if deleted_at is not None:
    raise DeskError("project_deleted", "The chat's project was deleted.", status=403)
  if source_app_id != app_id:
    raise DeskError(
      "wrong_app",
      "This project was not created from an Evidence Desk template.",
      status=403,
    )
  if not _root_matches(root_path, data_root, project_id):
    raise DeskError(
      "project_root_mismatch",
      "The project's folder is not where Möbius keeps projects.",
      status=403,
    )
  return ProjectBinding(
    project_id=project_id,
    project_name=name if isinstance(name, str) else "",
    data_root=data_root,
    app_storage_dir=storage,
    app_id=app_id,
    chat_id=chat_id,
    project_chat_id=project_chat_id,
  )
