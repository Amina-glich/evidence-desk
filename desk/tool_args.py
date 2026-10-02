"""Strict parsing of the platform's app-tool requests.

The platform calls ``POST /tools/<name>`` on the service with a body of
``{"arguments": ..., "call": ...}`` plus an ``actor``. Only ``call`` and
``actor`` are platform-set; ``arguments`` is written by the model. This
module accepts exactly the declared arguments: unknown keys are refused
rather than ignored, and ``project_id`` in particular is rejected because
the project is derived from the trusted call (see ``binding``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from desk.errors import DeskError


MAX_ARGUMENT_CHARS = 2048


@dataclass(frozen=True)
class ToolSpec:
  writes: bool
  fields: frozenset[str]
  require_any: frozenset[str] = frozenset()


TOOL_SPECS = {
  # Registers an uploaded PDF. A remote `identifier` (arXiv id, DOI) joins
  # `file` when network lookups are added.
  "add_source": ToolSpec(
    writes=True,
    fields=frozenset({"file"}),
    require_any=frozenset({"file"}),
  ),
  "check_evidence": ToolSpec(writes=False, fields=frozenset()),
  "export_comparison": ToolSpec(writes=True, fields=frozenset()),
  "project_status": ToolSpec(writes=False, fields=frozenset()),
}


@dataclass(frozen=True)
class ToolRequest:
  name: str
  arguments: dict
  call: dict
  actor: dict


def validate_arguments(name: str, arguments: object) -> dict:
  """Return the arguments for tool ``name`` if they match its spec exactly."""
  spec = TOOL_SPECS.get(name)
  if spec is None:
    raise DeskError("unknown_tool", f"Unknown tool '{name}'.", status=404)
  if not isinstance(arguments, Mapping):
    raise DeskError("invalid_arguments", "Tool arguments must be an object.")
  unknown = sorted(set(arguments) - spec.fields)
  if "project_id" in unknown:
    raise DeskError(
      "unknown_argument",
      "Evidence Desk determines the project from this chat; do not pass project_id.",
    )
  if unknown:
    raise DeskError(
      "unknown_argument", f"Unknown argument(s): {', '.join(unknown)}.",
    )
  clean = {}
  for key, value in arguments.items():
    if (
      not isinstance(value, str)
      or not value.strip()
      or len(value) > MAX_ARGUMENT_CHARS
      or "\x00" in value
    ):
      raise DeskError(
        "invalid_arguments",
        f"'{key}' must be a non-empty string of at most {MAX_ARGUMENT_CHARS} characters.",
      )
    clean[key] = value.strip()
  if spec.require_any and not (spec.require_any & set(clean)):
    raise DeskError(
      "invalid_arguments",
      f"Provide at least one of: {', '.join(sorted(spec.require_any))}.",
    )
  return clean


def parse_tool_request(request: object) -> ToolRequest:
  """Parse one platform tool request, refusing writes from read-only callers."""
  if not isinstance(request, Mapping):
    raise DeskError("invalid_request", "The request must be a JSON object.")
  path = request.get("path")
  if request.get("method") != "POST" or not isinstance(path, str):
    raise DeskError("invalid_request", "Tools are called with POST.", status=405)
  prefix = "/tools/"
  if not path.startswith(prefix) or "/" in path[len(prefix):]:
    raise DeskError("unknown_tool", "Unknown tool path.", status=404)
  name = path[len(prefix):]
  body = request.get("body")
  if not isinstance(body, Mapping):
    raise DeskError("invalid_request", "The tool request has no body.")
  call = body.get("call")
  actor = request.get("actor")
  if not isinstance(call, Mapping) or not isinstance(actor, Mapping):
    raise DeskError(
      "invalid_call", "The tool call has no trusted identity.", status=403,
    )
  arguments = validate_arguments(name, body.get("arguments", {}))
  if TOOL_SPECS[name].writes and actor.get("access") != "write":
    raise DeskError(
      "read_only", "This caller may not change project files.", status=403,
    )
  return ToolRequest(name, arguments, dict(call), dict(actor))
