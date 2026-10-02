"""Symlink-safe, project-confined file access for the Evidence Desk service.

The service runs with the platform's file authority, so confinement is this
module's job. Every project path is opened one component at a time relative
to an already-open directory descriptor with ``O_NOFOLLOW``: a symlink placed
anywhere under the project (for example ``sources/S9 -> /data``) is refused
instead of followed, and ``..`` or absolute paths never reach the filesystem.

Writes are confined to the service-owned roots (``sources/`` and
``exports/``). The agent owns ``evidence/``, ``desk.json`` and
``synthesis.md``; the owner's uploads live in ``inbox/``, which the service
only reads. The platform offers no lock shared with the agent's file
tools, so ownership is separated by path and service writers are serialized
by ``project_lock``. A file's revision is the SHA-256 hex digest of its bytes,
the same identity the platform's Projects file API uses.

These APIs require Linux (``O_NOFOLLOW``, directory descriptors, ``flock``).
On any other platform they refuse with ``unsupported_platform`` rather than
silently falling back to unsafe path handling.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping

from desk.errors import DeskError

try:
  import fcntl
except ImportError:  # Windows development machines have no flock.
  fcntl = None


# Project and chat ids are canonical dashed UUIDs on the platform.
UUID_RE = re.compile(
  r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
  re.IGNORECASE,
)
SERVICE_OWNED_ROOTS = frozenset({"sources", "exports"})
STAGING_DIR = ".staging"
LOCK_TIMEOUT_SECONDS = 30.0
SOURCE_ID_RE = re.compile(r"^S([1-9][0-9]{0,5})$")
_STAGE_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PATH_MAX_CHARS = 1024
_PART_MAX_BYTES = 255
_READ_CHUNK = 128 * 1024
_CLAIM_ATTEMPTS = 8
# Sentinel meaning "replace whatever is there" for write_atomic.
ANY_REVISION = object()


def require_posix() -> None:
  """Refuse to run where symlink-safe directory-relative I/O is missing."""
  if (
    fcntl is None
    or not hasattr(os, "O_NOFOLLOW")
    or not hasattr(os, "O_DIRECTORY")
    or os.open not in os.supports_dir_fd
    or os.rename not in os.supports_dir_fd
  ):
    raise DeskError(
      "unsupported_platform",
      "Evidence Desk file handling requires Linux symlink-safe file APIs.",
      status=503,
    )


def validate_project_id(value: object) -> str:
  """Return ``value`` if it is a canonical project id, else refuse."""
  if not isinstance(value, str) or not UUID_RE.match(value):
    raise DeskError("invalid_project", "The project id is not valid.")
  return value


def split_relative(path: object) -> tuple[str, ...]:
  """Split a project-relative POSIX path, refusing anything that could escape.

  Rejects non-strings, empty paths, absolute paths, backslashes, NUL bytes,
  empty components, ``.`` and ``..``. Symlinks are refused later, when each
  component is opened.
  """
  if (
    not isinstance(path, str)
    or not path
    or len(path) > _PATH_MAX_CHARS
    or "\x00" in path
    or "\\" in path
  ):
    raise DeskError("unsafe_path", "The path is not a valid project path.")
  if path.startswith("/"):
    raise DeskError("unsafe_path", "Absolute paths are not allowed.")
  parts = tuple(path.split("/"))
  for part in parts:
    if part in ("", ".", ".."):
      raise DeskError("unsafe_path", "The path must stay inside the project.")
    if len(part.encode("utf-8")) > _PART_MAX_BYTES:
      raise DeskError("unsafe_path", "A path component is too long.")
  return parts


def _dir_flags() -> int:
  return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _refuse_link(name: str, exc: OSError) -> DeskError:
  if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
    return DeskError(
      "unsafe_path", f"'{name}' is a symlink or not a regular folder or file.",
    )
  return DeskError("io_error", f"Could not open '{name}'.", status=500)


def _open_child_dir(parent_fd: int, name: str, *, create: bool = False) -> int:
  """Open one child directory of ``parent_fd`` without following symlinks."""
  try:
    return os.open(name, _dir_flags(), dir_fd=parent_fd)
  except FileNotFoundError:
    if not create:
      raise DeskError("not_found", f"Folder '{name}' does not exist.", status=404)
  except OSError as exc:
    raise _refuse_link(name, exc) from exc
  try:
    os.mkdir(name, 0o755, dir_fd=parent_fd)
  except FileExistsError:
    pass
  # Reopen with the same refusal rules: something may have raced a symlink in.
  return _open_child_dir(parent_fd, name, create=False)


def _lstat_at(dir_fd: int, name: str) -> os.stat_result | None:
  try:
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
  except FileNotFoundError:
    return None


def _open_regular_file(dir_fd: int, name: str) -> int | None:
  """Open a regular file for reading, or return None when it is absent."""
  try:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
  except FileNotFoundError:
    return None
  except OSError as exc:
    raise _refuse_link(name, exc) from exc
  if not stat.S_ISREG(os.fstat(fd).st_mode):
    os.close(fd)
    raise DeskError("unsafe_path", f"'{name}' is not a regular file.")
  return fd


def _hash_fd(fd: int) -> str:
  digest = hashlib.sha256()
  while True:
    chunk = os.read(fd, _READ_CHUNK)
    if not chunk:
      return digest.hexdigest()
    digest.update(chunk)


def _write_all(fd: int, data: bytes) -> None:
  view = memoryview(data)
  while view:
    written = os.write(fd, view)
    view = view[written:]


def open_project(data_root: Path, project_id: str) -> "Project":
  """Open ``<data_root>/projects/<project_id>`` refusing any symlink on the way.

  ``data_root`` itself is the platform's trusted volume (it may be a mount
  point); every component below it must be a real directory.
  """
  require_posix()
  validate_project_id(project_id)
  try:
    data_fd = os.open(str(data_root), _dir_flags() & ~os.O_NOFOLLOW)
  except OSError as exc:
    raise DeskError(
      "platform_unavailable", "The Möbius data folder is unavailable.", status=503,
    ) from exc
  try:
    try:
      projects_fd = _open_child_dir(data_fd, "projects")
    except DeskError as exc:
      if exc.code == "not_found":
        raise DeskError(
          "project_root_missing", "The project folder does not exist.", status=404,
        ) from exc
      raise
    try:
      root_fd = _open_child_dir(projects_fd, project_id)
    except DeskError as exc:
      if exc.code == "not_found":
        raise DeskError(
          "project_root_missing", "The project folder does not exist.", status=404,
        ) from exc
      raise
    finally:
      os.close(projects_fd)
  finally:
    os.close(data_fd)
  return Project(root_fd, project_id)


@dataclass
class ProjectLock:
  """Proof that the caller holds the per-project service write lock."""

  project_id: str
  held: bool = True


@contextlib.contextmanager
def project_lock(
  app_storage_dir: Path, project_id: str, *, timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Iterator[ProjectLock]:
  """Serialize Evidence Desk writers for one project across processes.

  App tool calls run concurrently and each service request is its own
  process, so an in-memory lock is not enough. The lock file lives in the
  app's private storage, never inside the project, so it cannot appear in the
  project's files or Git history.
  """
  require_posix()
  validate_project_id(project_id)
  os.makedirs(app_storage_dir, exist_ok=True)
  base_fd = os.open(str(app_storage_dir), _dir_flags() & ~os.O_NOFOLLOW)
  try:
    locks_fd = _open_child_dir(base_fd, "locks", create=True)
  finally:
    os.close(base_fd)
  try:
    lock_fd = os.open(
      f"project-{project_id}.lock",
      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
      0o600,
      dir_fd=locks_fd,
    )
  except OSError as exc:
    os.close(locks_fd)
    raise _refuse_link("project lock", exc) from exc
  token = ProjectLock(project_id)
  try:
    deadline = time.monotonic() + timeout
    while True:
      try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except BlockingIOError:
        if time.monotonic() >= deadline:
          raise DeskError(
            "busy", "Another Evidence Desk operation is still running.", status=503,
          ) from None
        time.sleep(0.05)
    try:
      yield token
    finally:
      token.held = False
      fcntl.flock(lock_fd, fcntl.LOCK_UN)
  finally:
    os.close(lock_fd)
    os.close(locks_fd)


@dataclass
class SourceStage:
  """A private staging folder whose contents become one ``sources/Sn``."""

  _stage_fd: int
  name: str
  _sources_fd: int
  _staging_fd: int
  files: dict[str, str] = field(default_factory=dict)
  published: bool = False

  def write(self, filename: str, data: bytes) -> str:
    """Create one new flat file in the stage; returns its revision."""
    if not isinstance(filename, str) or not _STAGE_FILE_RE.match(filename):
      raise DeskError("unsafe_path", "Staged file names must be simple names.")
    if self.published:
      raise DeskError("stage_closed", "This source was already published.")
    try:
      fd = os.open(
        filename,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644,
        dir_fd=self._stage_fd,
      )
    except FileExistsError:
      raise DeskError("conflict", f"'{filename}' was already staged.") from None
    try:
      _write_all(fd, data)
      os.fsync(fd)
    finally:
      os.close(fd)
    revision = hashlib.sha256(data).hexdigest()
    self.files[filename] = revision
    return revision


class Project:
  """An open, verified project root. Use as a context manager."""

  def __init__(self, root_fd: int, project_id: str):
    self._fd = root_fd
    self.project_id = project_id

  def close(self) -> None:
    if self._fd >= 0:
      os.close(self._fd)
      self._fd = -1

  def __enter__(self) -> "Project":
    return self

  def __exit__(self, *exc) -> None:
    self.close()

  def _open_dir(self, parts: tuple[str, ...], *, create: bool = False) -> int:
    """Open the directory named by ``parts`` (may be empty for the root)."""
    fd = os.dup(self._fd)
    try:
      for part in parts:
        child = _open_child_dir(fd, part, create=create)
        os.close(fd)
        fd = child
    except BaseException:
      os.close(fd)
      raise
    return fd

  def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
    """Read one regular file, refusing symlinks and files over ``max_bytes``."""
    parts = split_relative(path)
    dir_fd = self._open_dir(parts[:-1])
    try:
      fd = _open_regular_file(dir_fd, parts[-1])
    finally:
      os.close(dir_fd)
    if fd is None:
      raise DeskError("not_found", f"'{path}' does not exist.", status=404)
    try:
      if os.fstat(fd).st_size > max_bytes:
        raise DeskError("too_large", f"'{path}' is larger than allowed.")
      chunks = []
      total = 0
      while True:
        chunk = os.read(fd, _READ_CHUNK)
        if not chunk:
          break
        total += len(chunk)
        if total > max_bytes:
          raise DeskError("too_large", f"'{path}' is larger than allowed.")
        chunks.append(chunk)
      return b"".join(chunks)
    finally:
      os.close(fd)

  def revision(self, path: str) -> str | None:
    """SHA-256 hex digest of one file, or None when it does not exist."""
    parts = split_relative(path)
    try:
      dir_fd = self._open_dir(parts[:-1])
    except DeskError as exc:
      if exc.code == "not_found":
        return None
      raise
    try:
      fd = _open_regular_file(dir_fd, parts[-1])
    finally:
      os.close(dir_fd)
    if fd is None:
      return None
    try:
      return _hash_fd(fd)
    finally:
      os.close(fd)

  def list_dir(self, path: str, *, limit: int) -> tuple[list[tuple[str, str]], bool]:
    """Entries of one project folder, without following symlinks.

    Returns ``([(name, kind), ...], truncated)`` sorted by name, where kind is
    ``file``, ``folder`` or ``other`` (a symlink, socket, device …). Hidden
    names (service staging, temporary files) are skipped.
    """
    parts = split_relative(path)
    fd = self._open_dir(parts)
    try:
      entries = []
      for name in sorted(os.listdir(fd)):
        if name.startswith("."):
          continue
        info = _lstat_at(fd, name)
        if info is None:
          continue
        if stat.S_ISREG(info.st_mode):
          kind = "file"
        elif stat.S_ISDIR(info.st_mode):
          kind = "folder"
        else:
          kind = "other"
        entries.append((name, kind))
    finally:
      os.close(fd)
    return entries[:limit], len(entries) > limit

  def snapshot(self, paths) -> dict[str, str | None]:
    """Revisions of several files, to detect concurrent edits by comparison."""
    return {path: self.revision(path) for path in paths}

  def changed_since(self, snapshot: Mapping[str, str | None]) -> list[str]:
    """Paths whose revision differs from ``snapshot`` now."""
    return [path for path, rev in snapshot.items() if self.revision(path) != rev]

  def write_atomic(
    self, path: str, data: bytes, *, expected_revision=ANY_REVISION,
  ) -> str:
    """Atomically replace one service-owned file; returns its new revision.

    The bytes are fully written and fsynced to a temporary sibling first, so
    readers see either the old or the new file, never a partial one. With
    ``expected_revision`` (a digest, or None for "must not exist"), the write
    is refused with ``conflict`` if the file changed. Without a lock shared
    with the agent this narrows, but cannot close, the check-to-replace
    window; service-owned paths are not agent-edited, and service writers are
    serialized by ``project_lock``.
    """
    parts = split_relative(path)
    if len(parts) < 2 or parts[0] not in SERVICE_OWNED_ROOTS:
      raise DeskError(
        "not_service_owned",
        "The service only writes under sources/ and exports/.",
      )
    if any(part.startswith(".") for part in parts):
      raise DeskError("unsafe_path", "Hidden service paths are reserved.")
    dir_fd = self._open_dir(parts[:-1], create=True)
    name = parts[-1]
    temp = f".tmp-{uuid.uuid4().hex}"
    try:
      existing = _lstat_at(dir_fd, name)
      if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise DeskError("unsafe_path", f"'{path}' is not a regular file.")
      fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644,
        dir_fd=dir_fd,
      )
      try:
        _write_all(fd, data)
        os.fsync(fd)
      finally:
        os.close(fd)
      if expected_revision is not ANY_REVISION:
        current_fd = _open_regular_file(dir_fd, name)
        current = None
        if current_fd is not None:
          try:
            current = _hash_fd(current_fd)
          finally:
            os.close(current_fd)
        if current != expected_revision:
          raise DeskError(
            "conflict", f"'{path}' changed since it was read.", status=409,
          )
      os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
      temp = None
      os.fsync(dir_fd)
    finally:
      if temp is not None:
        with contextlib.suppress(FileNotFoundError):
          os.unlink(temp, dir_fd=dir_fd)
      os.close(dir_fd)
    return hashlib.sha256(data).hexdigest()

  @contextlib.contextmanager
  def staged_source(self) -> Iterator[SourceStage]:
    """A private folder under ``sources/.staging/``, removed unless published."""
    sources_fd = self._open_dir(("sources",), create=True)
    try:
      staging_fd = _open_child_dir(sources_fd, STAGING_DIR, create=True)
    except BaseException:
      os.close(sources_fd)
      raise
    name = uuid.uuid4().hex
    try:
      os.mkdir(name, 0o755, dir_fd=staging_fd)
      stage_fd = _open_child_dir(staging_fd, name)
    except BaseException:
      os.close(staging_fd)
      os.close(sources_fd)
      raise
    stage = SourceStage(stage_fd, name, sources_fd, staging_fd)
    try:
      yield stage
    finally:
      os.close(stage_fd)
      if not stage.published:
        with contextlib.suppress(OSError):
          shutil.rmtree(name, dir_fd=staging_fd)
      os.close(staging_fd)
      os.close(sources_fd)

  def source_ids(self) -> list[str]:
    """Published source ids (``S1``, ``S2`` …) in numeric order."""
    try:
      sources_fd = self._open_dir(("sources",))
    except DeskError as exc:
      if exc.code == "not_found":
        return []
      raise
    try:
      names = os.listdir(sources_fd)
    finally:
      os.close(sources_fd)
    numbered = [
      (int(match.group(1)), name)
      for name in names
      if (match := SOURCE_ID_RE.match(name))
    ]
    return [name for _number, name in sorted(numbered)]

  def publish_source(
    self,
    stage: SourceStage,
    lock: ProjectLock,
    *,
    existing: Callable[["Project"], str | None] | None = None,
  ) -> tuple[str, bool]:
    """Publish a stage as the next ``sources/Sn``; returns (id, created).

    Must be called while holding this project's ``project_lock``. When
    ``existing`` finds an equivalent published source (deduplication), that
    id is returned with ``created=False`` and the stage is discarded.

    Publication first claims ``Sn`` with an exclusive ``mkdir`` (two writers
    can never claim the same id), then atomically renames the complete stage
    over that empty claim, so the source's files appear all at once. If
    anything put content into the claim meanwhile, the rename fails with
    ``conflict`` and nothing is overwritten.
    """
    if not lock.held or lock.project_id != self.project_id:
      raise DeskError(
        "lock_required", "Publishing a source requires the project lock.",
        status=500,
      )
    if stage.published:
      raise DeskError("stage_closed", "This source was already published.")
    if existing is not None:
      found = existing(self)
      if found is not None:
        return found, False
    sources_fd = stage._sources_fd
    staging_fd = stage._staging_fd
    os.fsync(stage._stage_fd)
    ids = self.source_ids()
    number = int(SOURCE_ID_RE.match(ids[-1]).group(1)) + 1 if ids else 1
    for _attempt in range(_CLAIM_ATTEMPTS):
      target = f"S{number}"
      try:
        os.mkdir(target, 0o755, dir_fd=sources_fd)
        break
      except FileExistsError:
        number += 1
    else:
      raise DeskError("conflict", "Could not claim a new source id.", status=409)
    try:
      os.rename(stage.name, target, src_dir_fd=staging_fd, dst_dir_fd=sources_fd)
    except OSError as exc:
      with contextlib.suppress(OSError):
        os.rmdir(target, dir_fd=sources_fd)
      if exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR):
        raise DeskError(
          "conflict", f"'sources/{target}' changed during publication.",
          status=409,
        ) from exc
      raise DeskError("io_error", "Could not publish the source.", status=500) from exc
    stage.published = True
    os.fsync(sources_fd)
    os.fsync(staging_fd)
    return target, True
