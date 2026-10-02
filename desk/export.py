"""``export_comparison``: ``exports/comparison.csv`` from checked evidence.

One row per source in scope, and for each dimension four columns: the
agent's status, the finding, its evidence (page and quotation), and the
service's own quotation check. A Reported finding whose quotations do not all
verify keeps its status but says so in the check column; the export never
presents an unchecked quotation as verified.

The export refuses while an in-scope evidence file is invalid, because its
findings cannot be interpreted. The output is deterministic for the same
inputs (no timestamps), UTF-8 with a byte order mark for spreadsheet apps,
and every cell that a spreadsheet could run as a formula is neutralized.
"""

from __future__ import annotations

import csv
import io
import json

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.evidence import VERIFIED, SourceReport, check_project, status_of
from desk.project_fs import SOURCE_ID_RE, Project, project_lock
from desk.vocabulary import DIMENSIONS, STATUSES


EXPORT_PATH = "exports/comparison.csv"
DESK_JSON_MAX_BYTES = 256 * 1024
STATUS_LABELS = dict(STATUSES)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_RESULT_LABELS = {
  "wrong_page": "found on a different page",
  "not_found": "not found in the source text",
  "page_out_of_range": "page is beyond the end of the PDF",
  "too_short": "quotation too short to check",
  "source_unavailable": "source text unavailable",
}


def safe_cell(value: str) -> str:
  """Neutralize spreadsheet formula injection (OWASP CSV injection)."""
  return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _scope(project: Project) -> list[str] | None:
  """Source ids listed in desk.json ``compare_sources``; None means all."""
  try:
    raw = project.read_bytes("desk.json", max_bytes=DESK_JSON_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      return None
    raise
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    data = None
  scope = data.get("compare_sources", []) if isinstance(data, dict) else None
  if (
    not isinstance(data, dict)
    or data.get("schema") != SCHEMA_VERSION
    or not isinstance(scope, list)
    or any(not isinstance(item, str) or not SOURCE_ID_RE.match(item) for item in scope)
  ):
    raise DeskError(
      "invalid_desk_json",
      f'desk.json must have "schema": {SCHEMA_VERSION} and "compare_sources" as a list of source ids like "S1".',
      status=409,
    )
  return list(dict.fromkeys(scope)) or None


def _check_cell(report: SourceReport, dimension: str) -> str:
  checks = report.checks.get(dimension, ())
  if not checks:
    return ""
  failures = [
    f"p.{check.page} {_RESULT_LABELS.get(check.result, check.result)}"
    + (f" (found on p.{', p.'.join(map(str, check.found_on[:5]))})" if check.found_on else "")
    for check in checks if check.result != VERIFIED
  ]
  if not failures:
    return f"Verified ({len(checks)} of {len(checks)} quotations)"
  return f"Failed ({len(failures)} of {len(checks)} quotations): " + "; ".join(failures)


def build_csv(reports: list[SourceReport]) -> bytes:
  header = ["Source", "Title", "File"]
  for _dimension, label in DIMENSIONS:
    header += [f"{label}: status", f"{label}: finding", f"{label}: evidence", f"{label}: check"]
  out = io.StringIO()
  writer = csv.writer(out, lineterminator="\r\n")
  writer.writerow(header)
  for report in reports:
    row = [report.source_id, report.title or "", report.file or ""]
    for dimension, _label in DIMENSIONS:
      finding = report.findings.get(dimension)
      status = status_of(report, dimension)
      if finding is not None and status == "reported":
        text = finding.value or ""
        evidence = " | ".join(f'p.{quote.page}: "{quote.quote}"' for quote in finding.evidence)
      elif finding is not None and status == "not_reported":
        text, evidence = f"Searched: {finding.checked}", ""
      else:
        text, evidence = "", ""
      row += [STATUS_LABELS[status], text, evidence, _check_cell(report, dimension)]
    writer.writerow([safe_cell(cell) for cell in row])
  return ("﻿" + out.getvalue()).encode("utf-8")


def export_comparison(binding: ProjectBinding, _arguments: dict) -> dict:
  with binding.open() as project:
    scope = _scope(project)
    reports = {report.source_id: report for report in check_project(project)}
    registered = [source_id for source_id, report in reports.items() if report.registered]
    if not registered:
      raise DeskError("no_sources", "No sources are registered yet; nothing to export.", status=409)
    if scope is None:
      chosen = registered
    else:
      unknown = [source_id for source_id in scope if source_id not in registered]
      if unknown:
        raise DeskError(
          "unknown_source",
          f"desk.json compare_sources names unregistered source(s): {', '.join(unknown)}.",
          status=409,
        )
      chosen = scope
    selected = [reports[source_id] for source_id in chosen]
    invalid = [report.source_id for report in selected if report.evidence in ("invalid", "refused")]
    if invalid:
      raise DeskError(
        "invalid_evidence",
        f"Fix the evidence file(s) for {', '.join(invalid)} first; check_evidence lists the problems.",
        status=409,
      )
    data = build_csv(selected)
    with project_lock(binding.app_storage_dir, binding.project_id):
      revision = project.write_atomic(EXPORT_PATH, data)
  checks = [
    check for report in selected for dimension_checks in report.checks.values()
    for check in dimension_checks
  ]
  verified = sum(check.result == VERIFIED for check in checks)
  return {
    "file": EXPORT_PATH,
    "sources": chosen,
    "quotes_verified": verified,
    "quotes_failed": len(checks) - verified,
    "revision": revision,
  }
