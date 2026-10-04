"""The Möbius ``json-v1`` service protocol for Evidence Desk.

The platform runs ``service.py`` once per request: one JSON request envelope
on stdin, one ``{"status", "body"}`` envelope on stdout. Evidence Desk serves
only agent tools (``POST /tools/<name>``); the launcher uses the platform's
Projects runtime and needs no service routes.

Every tool call is parsed strictly (``tool_args``), then bound to its project
from the trusted call identity (``binding``), before any handler runs. A
refusal becomes its ``DeskError`` status and safe message. Anything
unexpected is logged to stderr, which only the platform log sees, and
answered with a generic error.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Callable, Mapping

from desk import evidence, export, library, sources, status
# Imported here, not where it is used, so that Möbius's Apply-time smoke run of
# this entry imports pypdf and a broken app environment fails the Apply.
from desk import pdf_text  # noqa: F401
from desk.binding import ProjectBinding, bind_call
from desk.errors import DeskError
from desk.tool_args import parse_tool_request


# Matches the platform's SERVICE_REQUEST_MAX_BYTES.
MAX_REQUEST_BYTES = 8 * 1024 * 1024

# Tools this version implements. Every one is declared in mobius.json; the
# platform only routes declared tools here.
HANDLERS: dict[str, Callable[[ProjectBinding, dict], object]] = {
  "project_status": status.project_status,
  "add_source": sources.add_source,
  "check_evidence": evidence.check_evidence,
  "export_comparison": export.export_comparison,
  "search_literature": library.search_literature,
  "lookup_reference": library.lookup_reference,
  "save_reference": library.save_reference,
}


def _reply(code: int, body: object) -> dict:
  return {"status": code, "body": body}


def handle(request: object, env: Mapping[str, str]) -> dict:
  """Answer one parsed request envelope with a response envelope."""
  try:
    tool = parse_tool_request(request)
    handler = HANDLERS.get(tool.name)
    if handler is None:
      raise DeskError(
        "unknown_tool",
        f"'{tool.name}' is not available in this version of Evidence Desk.",
        status=404,
      )
    binding = bind_call(tool.call, env)
    return _reply(200, handler(binding, tool.arguments))
  except DeskError as exc:
    return _reply(exc.status, exc.to_body())


def _reject_constant(value: str):
  raise ValueError(f"invalid JSON constant: {value}")


def main(stdin=None, stdout=None, env: Mapping[str, str] | None = None) -> int:
  stdin = stdin if stdin is not None else sys.stdin.buffer
  stdout = stdout if stdout is not None else sys.stdout
  env = env if env is not None else os.environ
  raw = stdin.read(MAX_REQUEST_BYTES + 1)
  if len(raw) > MAX_REQUEST_BYTES:
    response = _reply(413, {"error": "too_large", "detail": "The request is too large."})
  else:
    try:
      request = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
      response = _reply(400, {"error": "invalid_request", "detail": "The request is not valid JSON."})
    else:
      try:
        response = handle(request, env)
      except Exception:
        traceback.print_exc(file=sys.stderr)
        response = _reply(500, {
          "error": "internal_error",
          "detail": "Evidence Desk hit an unexpected error.",
        })
  stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False))
  stdout.flush()
  return 0
