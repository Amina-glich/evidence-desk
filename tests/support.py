"""Offline fixtures: a temporary Möbius data root and platform database.

The schema mirrors only the platform columns Evidence Desk reads
(``chats``, ``delegations``, ``projects``), with the platform's table and
column names. The database runs in WAL mode with a writer connection held
open, like the live platform process.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path


APP_ID = 7
OTHER_APP_ID = 9
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "paper-comparison"


def _posix_supported() -> bool:
  from desk.errors import DeskError
  from desk.project_fs import require_posix
  try:
    require_posix()
    return True
  except DeskError:
    return False


POSIX = _posix_supported()
SKIP_REASON = "needs Linux O_NOFOLLOW, dir_fd and flock (run in the Möbius image)"


def tool_envelope(name, chat_id, *, arguments=None, access="write", method="POST", path=None):
  """A tool request exactly as the platform sends it to the service."""
  return {
    "schema": 1,
    "method": method,
    "path": path or f"/tools/{name}",
    "query": {},
    "headers": {},
    "body": {
      "arguments": arguments or {},
      "call": {"chat_id": chat_id, "run_id": "run-1", "provider": "claude", "call_id": None},
    },
    "public": False,
    "actor": {"scope": "owner", "app_id": None, "app_slug": None, "delegated": False, "access": access},
  }


def load_manifest() -> dict:
  return json.loads((REPO_ROOT / "mobius.json").read_text(encoding="utf-8"))


def template(manifest: dict | None = None) -> dict:
  manifest = manifest or load_manifest()
  return next(t for t in manifest["project_templates"] if t["id"] == TEMPLATE_ID)


def copy_template_files(root: Path) -> None:
  """Copy starter files into ``root`` the way Möbius does at project creation."""
  for destination, source in template()["files"].items():
    target = root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / source, target)

_SCHEMA = """
CREATE TABLE chats (
  id VARCHAR(64) PRIMARY KEY,
  created_by_app_id INTEGER,
  project_id VARCHAR(64),
  deleted_at DATETIME
);
CREATE TABLE delegations (
  id VARCHAR(64) PRIMARY KEY,
  parent_chat_id VARCHAR(64) NOT NULL,
  child_chat_id VARCHAR(64) NOT NULL UNIQUE
);
CREATE TABLE projects (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(256) NOT NULL,
  root_path VARCHAR(1024) NOT NULL UNIQUE,
  source_app_id INTEGER,
  deleted_at DATETIME
);
"""


def new_id() -> str:
  return str(uuid.uuid4())


class Platform:
  """A disposable data root with ``db/``, ``apps/<id>/`` and ``projects/``."""

  def __init__(self, schema: str = _SCHEMA):
    self.tmp = tempfile.mkdtemp(prefix="evidence-desk-test-")
    # Resolve so that comparisons are not confused by a symlinked temp dir.
    self.data_root = Path(os.path.realpath(self.tmp)) / "data"
    (self.data_root / "db").mkdir(parents=True)
    (self.data_root / "projects").mkdir()
    self.app_storage = self.data_root / "apps" / str(APP_ID)
    self.app_storage.mkdir(parents=True)
    self.db_path = self.data_root / "db" / "ultimate.db"
    self.writer = sqlite3.connect(self.db_path, isolation_level=None)
    self.writer.execute("PRAGMA journal_mode = WAL")
    self.writer.executescript(schema)

  def close(self) -> None:
    self.writer.close()
    shutil.rmtree(self.tmp, ignore_errors=True)

  def env(self, **overrides) -> dict:
    env = {"APP_ID": str(APP_ID), "APP_STORAGE_DIR": str(self.app_storage)}
    env.update(overrides)
    return {key: value for key, value in env.items() if value is not None}

  def add_chat(self, *, project_id=None, deleted=False, chat_id=None) -> str:
    chat_id = chat_id or new_id()
    self.writer.execute(
      "INSERT INTO chats (id, project_id, deleted_at) VALUES (?, ?, ?)",
      (chat_id, project_id, "2026-10-01 00:00:00" if deleted else None),
    )
    return chat_id

  def add_project(
    self, *, app_id=APP_ID, deleted=False, root_path=None, make_dir=True,
    name="Review", starter=False,
  ) -> str:
    project_id = new_id()
    self.writer.execute(
      "INSERT INTO projects (id, name, root_path, source_app_id, deleted_at) "
      "VALUES (?, ?, ?, ?, ?)",
      (
        project_id, name,
        root_path if root_path is not None else f"projects/{project_id}",
        app_id, "2026-10-01 00:00:00" if deleted else None,
      ),
    )
    if make_dir:
      (self.data_root / "projects" / project_id).mkdir()
      if starter:
        copy_template_files(self.data_root / "projects" / project_id)
    return project_id

  def add_delegation(self, parent_chat_id: str, child_chat_id: str) -> None:
    self.writer.execute(
      "INSERT INTO delegations (id, parent_chat_id, child_chat_id) VALUES (?, ?, ?)",
      (new_id(), parent_chat_id, child_chat_id),
    )

  def project_root(self, project_id: str) -> Path:
    return self.data_root / "projects" / project_id
