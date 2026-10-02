"""The service entry: protocol handling, refusals, and ``project_status``.

Protocol and binding refusals run everywhere. The status report reads project
files, so those tests need the Linux file APIs and are skipped elsewhere.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from desk import service
from desk.tool_args import TOOL_SPECS, ToolSpec
from tests.support import (
  OTHER_APP_ID, POSIX, REPO_ROOT, SKIP_REASON, Platform, new_id, tool_envelope,
)


envelope = tool_envelope


def run_main(raw: bytes, env: dict) -> dict:
  out = io.StringIO()
  code = service.main(stdin=io.BytesIO(raw), stdout=out, env=env)
  return {"exit": code, **json.loads(out.getvalue())}


class ServiceProtocolTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)
    self.env = self.platform.env()

  def test_non_tool_paths_and_methods_are_refused(self):
    chat = self.platform.add_chat()
    self.assertEqual(service.handle(envelope("x", chat, path="/status"), self.env)["status"], 404)
    self.assertEqual(service.handle(envelope("x", chat, path="/tools/a/b"), self.env)["status"], 404)
    self.assertEqual(service.handle(envelope("project_status", chat, method="GET"), self.env)["status"], 405)

  def test_tools_not_implemented_yet_are_refused_before_binding(self):
    project = self.platform.add_project()
    chat = self.platform.add_chat(project_id=project)
    future = {"future_tool": ToolSpec(writes=False, fields=frozenset())}
    with mock.patch.dict(TOOL_SPECS, future), mock.patch.object(service, "bind_call") as bind:
      reply = service.handle(envelope("future_tool", chat), self.env)
    bind.assert_not_called()
    self.assertEqual(reply["status"], 404)
    self.assertEqual(reply["body"]["error"], "unknown_tool")

  def test_project_id_argument_is_refused(self):
    project = self.platform.add_project()
    chat = self.platform.add_chat(project_id=project)
    reply = service.handle(envelope("project_status", chat, arguments={"project_id": project}), self.env)
    self.assertEqual(reply["status"], 400)
    self.assertIn("determines the project from this chat", reply["body"]["detail"])

  def test_binding_refusals_reveal_no_project_data(self):
    foreign = self.platform.add_project(app_id=OTHER_APP_ID, name="Someone else's thesis")
    cases = {
      "not_project_chat": self.platform.add_chat(),
      "wrong_app": self.platform.add_chat(project_id=foreign),
      "chat_not_found": new_id(),
    }
    for code, chat in cases.items():
      with self.subTest(code=code):
        reply = service.handle(envelope("project_status", chat), self.env)
        self.assertEqual(reply["status"], 403)
        self.assertEqual(set(reply["body"]), {"error", "detail"})
        self.assertEqual(reply["body"]["error"], code)
        self.assertNotIn(foreign, json.dumps(reply))
        self.assertNotIn("thesis", json.dumps(reply))

  def test_missing_platform_identity_fails_closed(self):
    project = self.platform.add_project()
    chat = self.platform.add_chat(project_id=project)
    reply = service.handle(envelope("project_status", chat), {})
    self.assertEqual(reply["status"], 503)
    self.assertEqual(reply["body"]["error"], "platform_unavailable")

  def test_main_answers_bad_input_with_an_envelope(self):
    self.assertEqual(run_main(b"{not json", self.env)["status"], 400)
    self.assertEqual(run_main(b'{"a": NaN}', self.env)["status"], 400)
    self.assertEqual(run_main(b"\xff\xfe", self.env)["status"], 400)
    with mock.patch.object(service, "MAX_REQUEST_BYTES", 16):
      reply = run_main(b" " * 17, self.env)
    self.assertEqual((reply["exit"], reply["status"]), (0, 413))

  def test_unexpected_errors_are_generic_and_logged_only_to_stderr(self):
    project = self.platform.add_project()
    chat = self.platform.add_chat(project_id=project)
    failing = mock.Mock(side_effect=RuntimeError("/data/projects/secret path"))
    raw = json.dumps(envelope("project_status", chat)).encode()
    with mock.patch.dict(service.HANDLERS, {"project_status": failing}), \
         mock.patch.object(service, "bind_call"), \
         mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
      reply = run_main(raw, self.env)
    self.assertEqual((reply["exit"], reply["status"]), (0, 500))
    self.assertEqual(reply["body"]["error"], "internal_error")
    self.assertNotIn("secret", json.dumps(reply))
    self.assertIn("RuntimeError", stderr.getvalue())

  def test_entry_runs_as_the_platform_runs_it(self):
    # Same shape as the platform: `python service.py` from the app root,
    # request on stdin, one JSON envelope on stdout, exit 0.
    chat = self.platform.add_chat()
    env = {**self.env, "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")}
    if "SYSTEMROOT" in os.environ:
      env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    completed = subprocess.run(
      [sys.executable, "service.py"], cwd=REPO_ROOT, env=env,
      input=json.dumps(envelope("project_status", chat)).encode(),
      capture_output=True, timeout=30, check=False,
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    reply = json.loads(completed.stdout)
    self.assertEqual(reply["status"], 403)
    self.assertEqual(reply["body"]["error"], "not_project_chat")

  @unittest.skipIf(POSIX, "only meaningful where the APIs are missing")
  def test_status_refuses_where_file_safety_is_unavailable(self):
    project = self.platform.add_project(starter=True)
    chat = self.platform.add_chat(project_id=project)
    reply = service.handle(envelope("project_status", chat), self.env)
    self.assertEqual(reply["status"], 503)
    self.assertEqual(reply["body"]["error"], "unsupported_platform")


@unittest.skipUnless(POSIX, SKIP_REASON)
class ProjectStatusTest(unittest.TestCase):

  def setUp(self):
    self.platform = Platform()
    self.addCleanup(self.platform.close)
    self.project_id = self.platform.add_project(name="Detector survey", starter=True)
    self.root = self.platform.project_root(self.project_id)
    self.chat = self.platform.add_chat(project_id=self.project_id)

  def status(self, chat=None, access="write"):
    reply = service.handle(envelope("project_status", chat or self.chat, access=access), self.platform.env())
    self.assertEqual(reply["status"], 200, reply)
    return reply["body"]

  def test_new_project_from_the_template_reports_its_starter_state(self):
    (self.root / "inbox" / "yolo.pdf").write_bytes(b"%PDF")
    body = self.status(access="read")
    self.assertEqual(body["project"], {"id": self.project_id, "name": "Detector survey"})
    self.assertEqual(body["bound_through"], "project chat")
    self.assertEqual(body["data_format"], 1)
    self.assertEqual(body["areas"]["inbox"], {
      "written_by": "owner", "state": "present", "files": ["README.md", "yolo.pdf"],
    })
    self.assertEqual(body["inbox_pdfs"], 1)
    for area in ("sources", "evidence", "exports"):
      self.assertEqual(body["areas"][area]["state"], "missing")
    self.assertEqual(body["files"]["desk.json"], {
      "written_by": "agent", "state": "valid", "research_question": None,
    })
    self.assertEqual(body["files"]["synthesis.md"]["state"], "present")

  def test_status_writes_nothing(self):
    before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
    self.status()
    after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
    self.assertEqual(before, after)

  def test_helper_chats_report_the_parent_project(self):
    helper = self.platform.add_chat()
    self.platform.add_delegation(self.chat, helper)
    body = self.status(chat=helper)
    self.assertEqual(body["project"]["id"], self.project_id)
    self.assertEqual(body["bound_through"], "helper chat")

  def test_other_projects_are_never_listed(self):
    other = self.platform.add_project(name="Private grant", starter=True)
    other_root = self.platform.project_root(other)
    (other_root / "inbox" / "confidential-draft.pdf").write_bytes(b"%PDF")
    (other_root / "evidence").mkdir()
    (other_root / "evidence" / "S1.json").write_text("{}")
    text = json.dumps(self.status())
    for leak in (other, "Private grant", "confidential-draft"):
      self.assertNotIn(leak, text)

  def test_symlinked_areas_are_refused_and_not_followed(self):
    outside = self.platform.data_root / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF")
    os.symlink(outside, self.root / "evidence")
    os.symlink(outside / "secret.pdf", self.root / "inbox" / "linked.pdf")
    os.unlink(self.root / "desk.json")
    os.symlink(outside / "secret.pdf", self.root / "desk.json")
    body = self.status()
    self.assertEqual(body["areas"]["evidence"]["state"], "refused")
    self.assertEqual(body["areas"]["inbox"]["files"], ["README.md"])
    self.assertEqual(body["areas"]["inbox"]["ignored"], ["linked.pdf"])
    self.assertEqual(body["inbox_pdfs"], 0)
    self.assertEqual(body["files"]["desk.json"]["state"], "refused")
    self.assertNotIn("secret", json.dumps(body))

  def test_sources_are_listed_in_numeric_order_with_strays_ignored(self):
    for name in ("S10", "S2", "S1", "notes"):
      (self.root / "sources" / name).mkdir(parents=True)
    (self.root / "sources" / ".staging").mkdir()
    (self.root / "sources" / "S3").write_text("not a folder")
    sources = self.status()["areas"]["sources"]
    self.assertEqual(sources["source_ids"], ["S1", "S2", "S10"])
    self.assertEqual(sources["ignored"], ["S3", "notes"])

  def test_desk_json_problems_are_reported_not_raised(self):
    cases = {
      b"{broken": "invalid",
      b'{"schema": 2}': "invalid",
      b"[]": "invalid",
      b'{"schema": 1, "research_question": "  Which detectors run in real time?  "}': "valid",
    }
    for raw, state in cases.items():
      with self.subTest(raw=raw):
        (self.root / "desk.json").write_bytes(raw)
        self.assertEqual(self.status()["files"]["desk.json"]["state"], state)
    self.assertEqual(
      self.status()["files"]["desk.json"]["research_question"], "Which detectors run in real time?",
    )
    os.unlink(self.root / "desk.json")
    self.assertEqual(self.status()["files"]["desk.json"]["state"], "missing")

  def test_long_listings_are_truncated(self):
    (self.root / "inbox" / "a.pdf").write_bytes(b"%PDF")
    with mock.patch("desk.status.LIST_LIMIT", 1):
      inbox = self.status()["areas"]["inbox"]
    self.assertEqual(inbox["files"], ["README.md"])
    self.assertTrue(inbox["truncated"])


if __name__ == "__main__":
  unittest.main()
