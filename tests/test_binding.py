"""The project a tool acts on comes only from the trusted chat identity."""

from __future__ import annotations

import hashlib
import sqlite3
import unittest

from desk import binding
from desk.binding import bind_call
from desk.errors import DeskError
from tests.support import OTHER_APP_ID, Platform, new_id


class BindingTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)

  def assertRefused(self, code, call, env=None):
    with self.assertRaises(DeskError) as caught:
      bind_call(call, env if env is not None else self.platform.env())
    self.assertEqual(caught.exception.code, code)
    return caught.exception

  def test_project_chat_binds_to_its_project(self):
    project_id = self.platform.add_project(name="ML review")
    chat_id = self.platform.add_chat(project_id=project_id)
    bound = bind_call({"chat_id": chat_id}, self.platform.env())
    self.assertEqual(bound.project_id, project_id)
    self.assertEqual(bound.project_name, "ML review")
    self.assertEqual(bound.chat_id, chat_id)
    self.assertEqual(bound.project_chat_id, chat_id)
    self.assertEqual(bound.data_root, self.platform.data_root)
    self.assertEqual(bound.app_storage_dir, self.platform.app_storage)

  def test_helper_chats_bind_through_their_parents(self):
    project_id = self.platform.add_project()
    root_chat = self.platform.add_chat(project_id=project_id)
    helper = self.platform.add_chat()
    nested_helper = self.platform.add_chat()
    self.platform.add_delegation(root_chat, helper)
    self.platform.add_delegation(helper, nested_helper)
    bound = bind_call({"chat_id": nested_helper}, self.platform.env())
    self.assertEqual(bound.project_id, project_id)
    self.assertEqual(bound.chat_id, nested_helper)
    self.assertEqual(bound.project_chat_id, root_chat)

  def test_model_supplied_project_id_in_call_is_ignored(self):
    mine = self.platform.add_project()
    other = self.platform.add_project()
    chat_id = self.platform.add_chat(project_id=mine)
    bound = bind_call(
      {"chat_id": chat_id, "project_id": other}, self.platform.env(),
    )
    self.assertEqual(bound.project_id, mine)

  def test_chat_outside_any_project_is_refused(self):
    chat_id = self.platform.add_chat()
    self.assertRefused("not_project_chat", {"chat_id": chat_id})

  def test_helper_of_a_non_project_chat_is_refused(self):
    parent = self.platform.add_chat()
    helper = self.platform.add_chat()
    self.platform.add_delegation(parent, helper)
    self.assertRefused("not_project_chat", {"chat_id": helper})

  def test_project_from_another_app_is_refused(self):
    project_id = self.platform.add_project(app_id=OTHER_APP_ID)
    chat_id = self.platform.add_chat(project_id=project_id)
    self.assertRefused("wrong_app", {"chat_id": chat_id})

  def test_core_template_project_is_refused(self):
    project_id = self.platform.add_project(app_id=None)
    chat_id = self.platform.add_chat(project_id=project_id)
    self.assertRefused("wrong_app", {"chat_id": chat_id})

  def test_deleted_project_and_deleted_chat_are_refused(self):
    deleted_project = self.platform.add_project(deleted=True)
    chat_id = self.platform.add_chat(project_id=deleted_project)
    self.assertRefused("project_deleted", {"chat_id": chat_id})
    live_project = self.platform.add_project()
    deleted_chat = self.platform.add_chat(project_id=live_project, deleted=True)
    self.assertRefused("chat_deleted", {"chat_id": deleted_chat})

  def test_missing_rows_are_refused(self):
    self.assertRefused("chat_not_found", {"chat_id": new_id()})
    dangling = self.platform.add_chat(project_id=new_id())
    self.assertRefused("project_not_found", {"chat_id": dangling})

  def test_untrusted_or_missing_chat_identity_is_refused(self):
    for call in ({}, {"chat_id": ""}, {"chat_id": "../etc"},
                 {"chat_id": 42}, {"chat_id": "not-a-uuid"}):
      with self.subTest(call=call):
        self.assertRefused("invalid_call", call)
    self.assertRefused("invalid_call", ["not", "a", "mapping"])

  def test_project_root_must_be_the_canonical_location(self):
    elsewhere = self.platform.add_project(root_path=f"apps/7/{new_id()}")
    chat_a = self.platform.add_chat(project_id=elsewhere)
    self.assertRefused("project_root_mismatch", {"chat_id": chat_a})
    traversal = self.platform.add_project(root_path="projects/../db")
    chat_b = self.platform.add_chat(project_id=traversal)
    self.assertRefused("project_root_mismatch", {"chat_id": chat_b})

  def test_absolute_canonical_root_is_accepted(self):
    project_id = self.platform.add_project(make_dir=False, root_path="placeholder")
    absolute = str(self.platform.data_root / "projects" / project_id)
    self.platform.writer.execute(
      "UPDATE projects SET root_path = ? WHERE id = ?", (absolute, project_id),
    )
    chat_id = self.platform.add_chat(project_id=project_id)
    bound = bind_call({"chat_id": chat_id}, self.platform.env())
    self.assertEqual(bound.project_id, project_id)

  def test_delegation_cycles_and_deep_chains_are_refused(self):
    a, b = self.platform.add_chat(), self.platform.add_chat()
    self.platform.add_delegation(a, b)
    self.platform.add_delegation(b, a)
    self.assertRefused("delegation_cycle", {"chat_id": b})
    chain = [self.platform.add_chat() for _ in range(binding.MAX_DELEGATION_DEPTH + 2)]
    for parent, child in zip(chain, chain[1:]):
      self.platform.add_delegation(parent, child)
    self.assertRefused("delegation_too_deep", {"chat_id": chain[-1]})

  def test_platform_identity_must_be_consistent(self):
    project_id = self.platform.add_project()
    call = {"chat_id": self.platform.add_chat(project_id=project_id)}
    self.assertRefused("platform_unavailable", call, self.platform.env(APP_ID=None))
    self.assertRefused("platform_unavailable", call, self.platform.env(APP_ID="abc"))
    self.assertRefused(
      "platform_unavailable", call,
      self.platform.env(APP_STORAGE_DIR=str(self.platform.data_root / "apps" / "8")),
    )
    self.assertRefused(
      "platform_unavailable", call, self.platform.env(APP_STORAGE_DIR="apps/7"),
    )
    self.assertRefused(
      "data_root_mismatch", call,
      self.platform.env(DATA_DIR=str(self.platform.data_root / "other")),
    )
    bound = bind_call(call, self.platform.env(DATA_DIR=str(self.platform.data_root)))
    self.assertEqual(bound.project_id, project_id)

  def test_missing_database_fails_closed(self):
    project_id = self.platform.add_project()
    call = {"chat_id": self.platform.add_chat(project_id=project_id)}
    env = self.platform.env(
      APP_STORAGE_DIR=str(self.platform.data_root / "elsewhere" / "apps" / "7"),
    )
    self.assertRefused("platform_unavailable", call, env)

  def test_changed_platform_schema_fails_closed(self):
    other = Platform(schema="""
      CREATE TABLE chats (id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64),
                          deleted_at DATETIME);
      CREATE TABLE delegations (parent_chat_id VARCHAR(64), child_chat_id VARCHAR(64));
      CREATE TABLE projects (id VARCHAR(64) PRIMARY KEY, name VARCHAR(256),
                             root_path VARCHAR(1024), deleted_at DATETIME);
    """)
    self.addCleanup(other.close)
    chat_id = other.add_chat()
    with self.assertRaises(DeskError) as caught:
      bind_call({"chat_id": chat_id}, other.env())
    self.assertEqual(caught.exception.code, "platform_schema_changed")

  def test_binding_never_writes_the_platform_database(self):
    project_id = self.platform.add_project()
    chat_id = self.platform.add_chat(project_id=project_id)
    self.platform.writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest()
    bind_call({"chat_id": chat_id}, self.platform.env())
    conn = binding.connect_readonly(self.platform.data_root)
    try:
      with self.assertRaises(sqlite3.Error):
        conn.execute("DELETE FROM projects")
    finally:
      conn.close()
    self.platform.writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest()
    self.assertEqual(before, after)
    count = self.platform.writer.execute("SELECT COUNT(*) FROM projects").fetchone()
    self.assertEqual(count[0], 1)


if __name__ == "__main__":
  unittest.main()
