"""Model-written tool arguments are accepted only exactly as declared."""

from __future__ import annotations

import unittest

from desk.errors import DeskError
from desk.tool_args import MAX_ARGUMENT_CHARS, parse_tool_request, validate_arguments
from tests.support import new_id


def request(name, arguments=None, *, access="write", path=None, method="POST"):
  return {
    "schema": 1,
    "method": method,
    "path": path or f"/tools/{name}",
    "query": {},
    "headers": {},
    "body": {"arguments": arguments or {}, "call": {"chat_id": new_id()}},
    "public": False,
    "actor": {"scope": "owner", "delegated": False, "access": access},
  }


class ToolArgsTest(unittest.TestCase):

  def assertRefused(self, code, func, *args):
    with self.assertRaises(DeskError) as caught:
      func(*args)
    self.assertEqual(caught.exception.code, code)
    return caught.exception

  def test_declared_arguments_are_accepted_and_trimmed(self):
    parsed = parse_tool_request(request("add_source", {"file": " inbox/a.pdf "}))
    self.assertEqual(parsed.name, "add_source")
    self.assertEqual(parsed.arguments, {"file": "inbox/a.pdf"})
    self.assertEqual(validate_arguments("check_evidence", {}), {})

  def test_project_id_is_rejected_with_an_explanation(self):
    error = self.assertRefused(
      "unknown_argument", validate_arguments,
      "add_source", {"file": "inbox/a.pdf", "project_id": new_id()},
    )
    self.assertIn("determines the project from this chat", error.message)
    self.assertRefused(
      "unknown_argument", validate_arguments, "check_evidence", {"project_id": new_id()},
    )

  def test_remote_identifiers_are_not_accepted_yet(self):
    self.assertRefused(
      "unknown_argument", validate_arguments, "add_source", {"identifier": "2401.00001"},
    )

  def test_unknown_arguments_are_rejected_not_ignored(self):
    self.assertRefused(
      "unknown_argument", validate_arguments, "add_source", {"file": "inbox/a.pdf", "path": "y"},
    )
    self.assertRefused("unknown_argument", validate_arguments, "export_comparison", {"x": "1"})

  def test_argument_values_are_bounded_strings(self):
    for bad in ("", "   ", 5, None, ["a"], "a\x00b", "x" * (MAX_ARGUMENT_CHARS + 1)):
      with self.subTest(bad=bad):
        self.assertRefused(
          "invalid_arguments", validate_arguments, "add_source", {"file": bad},
        )
    self.assertRefused("invalid_arguments", validate_arguments, "add_source", {})
    self.assertRefused("invalid_arguments", validate_arguments, "add_source", "file")

  def test_unknown_tools_and_paths_are_refused(self):
    self.assertRefused("unknown_tool", validate_arguments, "delete_project", {})
    self.assertRefused("unknown_tool", parse_tool_request, request("x", path="/tools/a/b"))
    self.assertRefused("unknown_tool", parse_tool_request, request("x", path="/status"))
    self.assertRefused("invalid_request", parse_tool_request, request("check_evidence", method="GET"))

  def test_read_only_callers_cannot_use_write_tools(self):
    self.assertRefused(
      "read_only", parse_tool_request, request("add_source", {"file": "inbox/a.pdf"}, access="read"),
    )
    self.assertRefused("read_only", parse_tool_request, request("export_comparison", access="read"))
    parsed = parse_tool_request(request("check_evidence", access="read"))
    self.assertEqual(parsed.name, "check_evidence")
    parsed = parse_tool_request(request("project_status", access="read"))
    self.assertEqual(parsed.name, "project_status")

  def test_project_status_takes_no_arguments(self):
    self.assertEqual(validate_arguments("project_status", {}), {})
    self.assertRefused(
      "unknown_argument", validate_arguments, "project_status", {"project_id": new_id()},
    )
    self.assertRefused(
      "unknown_argument", validate_arguments, "project_status", {"path": "inbox"},
    )

  def test_missing_trusted_identity_is_refused(self):
    no_call = request("check_evidence")
    no_call["body"].pop("call")
    self.assertRefused("invalid_call", parse_tool_request, no_call)
    no_actor = request("check_evidence")
    no_actor.pop("actor")
    self.assertRefused("invalid_call", parse_tool_request, no_actor)
    self.assertRefused("invalid_request", parse_tool_request, "not a request")


if __name__ == "__main__":
  unittest.main()
