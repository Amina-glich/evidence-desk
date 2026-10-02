"""Project confinement, symlink refusal, write coordination and publishing.

These behaviors depend on Linux file APIs (``O_NOFOLLOW``, directory
descriptors, ``flock``), which is what the Möbius container provides. On
other platforms the module refuses to run, and these tests are skipped with
that reason instead of passing vacuously.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import time
import unittest
from unittest import mock

from desk import project_fs
from desk.errors import DeskError
from desk.project_fs import open_project, project_lock, split_relative
from tests.support import Platform, new_id


def _posix_supported() -> bool:
  try:
    project_fs.require_posix()
    return True
  except DeskError:
    return False


POSIX = _posix_supported()
SKIP_REASON = "needs Linux O_NOFOLLOW, dir_fd and flock (run in the Möbius image)"


class PlatformRefusalTest(unittest.TestCase):

  @unittest.skipIf(POSIX, "only meaningful where the APIs are missing")
  def test_unsupported_platform_refuses_instead_of_falling_back(self):
    with self.assertRaises(DeskError) as caught:
      open_project(os.getcwd(), new_id())
    self.assertEqual(caught.exception.code, "unsupported_platform")


class SplitRelativeTest(unittest.TestCase):

  def test_escaping_or_malformed_paths_are_refused(self):
    for bad in ("", "/etc/passwd", "../x", "a/../b", "a//b", "a/./b", ".",
                "a\\b", "a\x00b", "x/" * 600, "a/" + "b" * 300, 5, None):
      with self.subTest(bad=bad):
        with self.assertRaises(DeskError) as caught:
          split_relative(bad)
        self.assertEqual(caught.exception.code, "unsafe_path")

  def test_ordinary_paths_split_into_components(self):
    self.assertEqual(split_relative("sources/S1/source.json"), ("sources", "S1", "source.json"))
    self.assertEqual(split_relative("inbox/My paper (2024).pdf"), ("inbox", "My paper (2024).pdf"))


def _publish_one(data_root, storage, project_id, payload, results):
  """Child-process body for the concurrency test (must be module level)."""
  with open_project(data_root, project_id) as project:
    with project.staged_source() as stage:
      stage.write("note.txt", payload)
      time.sleep(0.05)
      with project_lock(storage, project_id) as lock:
        source_id, created = project.publish_source(stage, lock)
  results.put((source_id, created, payload))


@unittest.skipUnless(POSIX, SKIP_REASON)
class ProjectFsTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)
    self.project_id = self.platform.add_project()
    self.root = self.platform.project_root(self.project_id)
    self.outside = self.platform.data_root / "outside"
    self.outside.mkdir()
    (self.outside / "secret.txt").write_bytes(b"do not touch")

  def open(self):
    project = open_project(self.platform.data_root, self.project_id)
    self.addCleanup(project.close)
    return project

  def lock(self, **kwargs):
    return project_lock(self.platform.app_storage, self.project_id, **kwargs)

  def assertRefused(self, code, func, *args, **kwargs):
    with self.assertRaises(DeskError) as caught:
      func(*args, **kwargs)
    self.assertEqual(caught.exception.code, code)

  def assertOutsideUntouched(self):
    self.assertEqual((self.outside / "secret.txt").read_bytes(), b"do not touch")
    self.assertEqual(sorted(os.listdir(self.outside)), ["secret.txt"])

  # Opening the project root.

  def test_symlinked_project_root_or_projects_folder_is_refused(self):
    linked_id = new_id()
    os.symlink(self.outside, self.platform.data_root / "projects" / linked_id)
    self.assertRefused("unsafe_path", open_project, self.platform.data_root, linked_id)
    self.assertRefused("project_root_missing", open_project, self.platform.data_root, new_id())
    self.assertRefused("invalid_project", open_project, self.platform.data_root, "../outside")
    projects = self.platform.data_root / "projects"
    os.rename(projects, self.platform.data_root / "real-projects")
    os.symlink(self.platform.data_root / "real-projects", projects)
    self.assertRefused("unsafe_path", open_project, self.platform.data_root, self.project_id)

  # Reading.

  def test_reads_stay_inside_the_project_and_refuse_symlinks(self):
    (self.root / "inbox").mkdir()
    (self.root / "inbox" / "paper.pdf").write_bytes(b"%PDF-1.7 test")
    os.symlink(self.outside / "secret.txt", self.root / "inbox" / "link.pdf")
    os.symlink(self.outside, self.root / "linked-dir")
    project = self.open()
    self.assertEqual(project.read_bytes("inbox/paper.pdf", max_bytes=100), b"%PDF-1.7 test")
    self.assertRefused("unsafe_path", project.read_bytes, "inbox/link.pdf", max_bytes=100)
    self.assertRefused("unsafe_path", project.read_bytes, "linked-dir/secret.txt", max_bytes=100)
    self.assertRefused("unsafe_path", project.read_bytes, "../outside/secret.txt", max_bytes=100)
    self.assertRefused("unsafe_path", project.read_bytes, "inbox", max_bytes=100)
    self.assertRefused("not_found", project.read_bytes, "inbox/missing.pdf", max_bytes=100)
    self.assertRefused("too_large", project.read_bytes, "inbox/paper.pdf", max_bytes=4)

  def test_listing_classifies_entries_without_following_symlinks(self):
    inbox = self.root / "inbox"
    inbox.mkdir()
    (inbox / "b.pdf").write_bytes(b"%PDF")
    (inbox / "a.pdf").write_bytes(b"%PDF")
    (inbox / "nested").mkdir()
    (inbox / ".tmp-x").write_bytes(b"")
    os.symlink(self.outside, inbox / "link")
    project = self.open()
    self.assertEqual(
      project.list_dir("inbox", limit=10),
      ([("a.pdf", "file"), ("b.pdf", "file"), ("link", "other"), ("nested", "folder")], False),
    )
    self.assertEqual(project.list_dir("inbox", limit=1), ([("a.pdf", "file")], True))
    os.symlink(self.outside, self.root / "evidence")
    self.assertRefused("unsafe_path", project.list_dir, "evidence", limit=10)
    self.assertRefused("not_found", project.list_dir, "exports", limit=10)
    self.assertRefused("unsafe_path", project.list_dir, "../outside", limit=10)
    self.assertOutsideUntouched()

  def test_revision_is_the_platform_sha256_identity(self):
    (self.root / "desk.json").write_bytes(b'{"schema": 1}')
    project = self.open()
    self.assertEqual(project.revision("desk.json"), hashlib.sha256(b'{"schema": 1}').hexdigest())
    self.assertIsNone(project.revision("missing.json"))
    self.assertIsNone(project.revision("evidence/S1.json"))
    snapshot = project.snapshot(["desk.json", "evidence/S1.json"])
    self.assertEqual(project.changed_since(snapshot), [])
    (self.root / "desk.json").write_bytes(b'{"schema": 1, "edited": true}')
    self.assertEqual(project.changed_since(snapshot), ["desk.json"])

  # Writing.

  def test_writes_are_atomic_and_limited_to_service_owned_roots(self):
    project = self.open()
    revision = project.write_atomic("exports/comparison.csv", b"a,b\n")
    self.assertEqual(revision, hashlib.sha256(b"a,b\n").hexdigest())
    self.assertEqual((self.root / "exports" / "comparison.csv").read_bytes(), b"a,b\n")
    project.write_atomic("exports/comparison.csv", b"a,b\n1,2\n")
    self.assertEqual(sorted(os.listdir(self.root / "exports")), ["comparison.csv"])
    for agent_owned in ("evidence/S1.json", "desk.json", "synthesis.md", "inbox/x.pdf"):
      with self.subTest(path=agent_owned):
        self.assertRefused("not_service_owned", project.write_atomic, agent_owned, b"x")
    self.assertRefused("unsafe_path", project.write_atomic, "sources/.staging/x", b"x")
    self.assertRefused("unsafe_path", project.write_atomic, "exports/../desk.json", b"x")

  def test_writes_never_follow_symlinks(self):
    os.symlink(self.outside, self.root / "exports")
    project = self.open()
    self.assertRefused("unsafe_path", project.write_atomic, "exports/comparison.csv", b"x")
    os.unlink(self.root / "exports")
    (self.root / "exports").mkdir()
    os.symlink(self.outside / "secret.txt", self.root / "exports" / "comparison.csv")
    self.assertRefused("unsafe_path", project.write_atomic, "exports/comparison.csv", b"x")
    self.assertOutsideUntouched()

  def test_expected_revision_detects_concurrent_changes(self):
    project = self.open()
    first = project.write_atomic("exports/comparison.csv", b"v1", expected_revision=None)
    self.assertRefused(
      "conflict", project.write_atomic, "exports/comparison.csv", b"v2", expected_revision=None,
    )
    (self.root / "exports" / "comparison.csv").write_bytes(b"edited elsewhere")
    self.assertRefused(
      "conflict", project.write_atomic, "exports/comparison.csv", b"v2", expected_revision=first,
    )
    self.assertEqual((self.root / "exports" / "comparison.csv").read_bytes(), b"edited elsewhere")
    self.assertEqual(sorted(os.listdir(self.root / "exports")), ["comparison.csv"])

  # Locking.

  def test_project_lock_serializes_writers_and_lives_outside_the_project(self):
    with self.lock() as held:
      self.assertTrue(held.held)
      self.assertRefused("busy", lambda: self.lock(timeout=0.2).__enter__())
    self.assertFalse(held.held)
    with self.lock(timeout=0.2):
      pass
    lock_file = self.platform.app_storage / "locks" / f"project-{self.project_id}.lock"
    self.assertTrue(lock_file.is_file())
    self.assertEqual(sorted(os.listdir(self.root)), [])

  def test_symlinked_lock_folder_is_refused(self):
    os.symlink(self.outside, self.platform.app_storage / "locks")
    self.assertRefused("unsafe_path", lambda: self.lock().__enter__())
    self.assertOutsideUntouched()

  # Staging and publishing sources.

  def test_published_sources_get_sequential_ids_and_appear_complete(self):
    project = self.open()
    for expected in ("S1", "S2"):
      with project.staged_source() as stage:
        stage.write("source.json", b'{"title": "%s"}' % expected.encode())
        stage.write("paper.pdf", b"%PDF")
        with self.lock() as lock:
          source_id, created = project.publish_source(stage, lock)
      self.assertEqual((source_id, created), (expected, True))
      self.assertEqual(sorted(os.listdir(self.root / "sources" / expected)),
                       ["paper.pdf", "source.json"])
    self.assertEqual(project.source_ids(), ["S1", "S2"])
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])

  def test_ids_continue_after_the_highest_existing_source(self):
    (self.root / "sources" / "S7").mkdir(parents=True)
    (self.root / "sources" / "notes").mkdir()
    project = self.open()
    with project.staged_source() as stage:
      stage.write("source.json", b"{}")
      with self.lock() as lock:
        self.assertEqual(project.publish_source(stage, lock), ("S8", True))

  def test_duplicate_detection_returns_existing_id_and_discards_stage(self):
    project = self.open()
    with project.staged_source() as stage:
      stage.write("source.json", b"{}")
      with self.lock() as lock:
        result = project.publish_source(stage, lock, existing=lambda _project: "S3")
    self.assertEqual(result, ("S3", False))
    self.assertFalse((self.root / "sources" / "S1").exists())
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])

  def test_publishing_requires_this_projects_lock(self):
    project = self.open()
    other_project = self.platform.add_project()
    with project.staged_source() as stage:
      stage.write("source.json", b"{}")
      self.assertRefused(
        "lock_required", project.publish_source, stage, project_fs.ProjectLock(self.project_id, held=False),
      )
      with project_lock(self.platform.app_storage, other_project) as other_lock:
        self.assertRefused("lock_required", project.publish_source, stage, other_lock)
    self.assertEqual(project.source_ids(), [])

  def test_failed_or_abandoned_stages_leave_nothing_behind(self):
    project = self.open()
    with self.assertRaises(RuntimeError):
      with project.staged_source() as stage:
        stage.write("paper.pdf", b"%PDF")
        raise RuntimeError("download failed")
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])
    self.assertEqual(project.source_ids(), [])
    with project.staged_source() as stage:
      self.assertRefused("unsafe_path", stage.write, "../escape.txt", b"x")
      self.assertRefused("unsafe_path", stage.write, "sub/file.txt", b"x")
      stage.write("a.txt", b"x")
      self.assertRefused("conflict", stage.write, "a.txt", b"y")

  def test_a_claimed_id_that_gains_content_is_never_overwritten(self):
    project = self.open()
    real_mkdir = os.mkdir

    def mkdir_then_intrude(name, mode=0o777, *, dir_fd=None):
      real_mkdir(name, mode, dir_fd=dir_fd)
      if name == "S1" and dir_fd is not None:
        fd = os.open("S1/agent.txt", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=dir_fd)
        os.write(fd, b"agent content")
        os.close(fd)

    with project.staged_source() as stage:
      stage.write("source.json", b"{}")
      with self.lock() as lock, mock.patch.object(project_fs.os, "mkdir", mkdir_then_intrude):
        self.assertRefused("conflict", project.publish_source, stage, lock)
    self.assertEqual((self.root / "sources" / "S1" / "agent.txt").read_bytes(), b"agent content")
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])

  def test_symlinked_sources_folder_is_refused(self):
    os.symlink(self.outside, self.root / "sources")
    project = self.open()
    with self.assertRaises(DeskError) as caught:
      with project.staged_source():
        pass
    self.assertEqual(caught.exception.code, "unsafe_path")
    self.assertOutsideUntouched()

  def test_parallel_publishers_in_separate_processes_get_unique_ids(self):
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    payloads = [f"paper-{index}".encode() for index in range(4)]
    workers = [
      context.Process(
        target=_publish_one,
        args=(self.platform.data_root, self.platform.app_storage,
              self.project_id, payload, results),
      )
      for payload in payloads
    ]
    for worker in workers:
      worker.start()
    for worker in workers:
      worker.join(30)
      self.assertEqual(worker.exitcode, 0)
    published = sorted(results.get(timeout=5) for _ in workers)
    self.assertEqual(sorted(item[0] for item in published), ["S1", "S2", "S3", "S4"])
    self.assertTrue(all(item[1] for item in published))
    for source_id, _created, payload in published:
      self.assertEqual((self.root / "sources" / source_id / "note.txt").read_bytes(), payload)
    self.assertEqual(os.listdir(self.root / "sources" / ".staging"), [])


if __name__ == "__main__":
  unittest.main()
