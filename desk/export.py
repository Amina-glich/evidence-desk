"""``export_comparison``: ``exports/comparison.csv`` from checked evidence.

One row per source in scope. The first 27 columns are unchanged from the
original format: source, title, file, then for each dimension the agent's
status, the finding, its evidence (page and quotation) and the service's own
quotation check. After them come three qualification columns per dimension:
the finding's note, its checked absences, and contradictions within the
source. The status is always the plain vocabulary label, so Not reported and
Not assessed stay distinct; qualifications never change it.

The check column covers every quotation of the dimension, evidence and
contradictions alike. A finding whose quotations do not all verify keeps its
status but says so; the export never presents an unchecked quotation as
verified. Contradictions are listed side by side, each quotation with its own
check result, and are never resolved.

The export refuses while an in-scope evidence file is invalid, because its
findings cannot be interpreted. The output is deterministic for the same
inputs (no timestamps), UTF-8 with a byte order mark for spreadsheet apps,
and every cell that a spreadsheet could run as a formula is neutralized. A
cell longer than ``MAX_CELL_CHARS`` refuses the whole export, naming the
cell, rather than letting a spreadsheet truncate it unseen.
"""

from __future__ import annotations

import csv
import io
import json

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.evidence import VERIFIED, QuoteCheck, SourceReport, check_project, status_of
from desk.project_fs import SOURCE_ID_RE, Project, project_lock
from desk.vocabulary import DIMENSIONS, STATUSES


EXPORT_PATH = "exports/comparison.csv"
DESK_JSON_MAX_BYTES = 256 * 1024
# Excel stores at most 32,767 characters in a cell and silently cuts the rest
# (other spreadsheet apps have similar limits). Stay below it, with room for
# the formula-neutralizing prefix.
MAX_CELL_CHARS = 32_000
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


def _failure(check: QuoteCheck) -> str:
  return (
    f"p.{check.page} {_RESULT_LABELS.get(check.result, check.result)}"
    + (f" (found on p.{', p.'.join(map(str, check.found_on[:5]))})" if check.found_on else "")
  )


def _check_cell(report: SourceReport, dimension: str) -> str:
  evidence = report.checks.get(dimension, ())
  contradictions = [
    check for checks in report.contradiction_checks.get(dimension, ()) for check in checks
  ]
  checks = (*evidence, *contradictions)
  if not checks:
    return ""
  failures = [_failure(check) for check in evidence if check.result != VERIFIED]
  failures += [
    f"{_failure(check)} (contradiction)" for check in contradictions if check.result != VERIFIED
  ]
  if not failures:
    return f"Verified ({len(checks)} of {len(checks)} quotations)"
  return f"Failed ({len(failures)} of {len(checks)} quotations): " + "; ".join(failures)


def _absences_cell(report: SourceReport, dimension: str) -> str:
  finding = report.findings.get(dimension)
  if finding is None:
    return ""
  return " | ".join(f"{absence.item} (searched: {absence.checked})" for absence in finding.absences)


def _contradictions_cell(report: SourceReport, dimension: str) -> str:
  finding = report.findings.get(dimension)
  if finding is None or not finding.contradictions:
    return ""
  all_checks = report.contradiction_checks.get(dimension, ())
  cells = []
  for contradiction, checks in zip(finding.contradictions, all_checks):
    sides = " vs ".join(
      f'p.{quote.page}: "{quote.quote}" ['
      + ("verified" if check.result == VERIFIED else "check failed: " + _failure(check))
      + "]"
      for quote, check in zip(contradiction.evidence, checks)
    )
    cells.append(f"{contradiction.description}: {sides}")
  return " | ".join(cells)


def header_row() -> list[str]:
  """Column names: the original 27 columns, then the qualification columns."""
  header = ["Source", "Title", "File"]
  for _dimension, label in DIMENSIONS:
    header += [f"{label}: status", f"{label}: finding", f"{label}: evidence", f"{label}: check"]
  for _dimension, label in DIMENSIONS:
    header += [f"{label}: note", f"{label}: checked absences", f"{label}: contradictions"]
  return header


def _row(report: SourceReport) -> list[str]:
  row = [report.source_id, report.title or "", report.file or ""]
  qualifications = []
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
    qualifications += [
      (finding.note or "") if finding is not None else "",
      _absences_cell(report, dimension), _contradictions_cell(report, dimension),
    ]
  return row + qualifications


def build_csv(reports: list[SourceReport]) -> bytes:
  """The CSV bytes, or ``cell_too_large`` instead of a cell a spreadsheet would cut."""
  header = header_row()
  out = io.StringIO()
  writer = csv.writer(out, lineterminator="\r\n")
  writer.writerow(header)
  for report in reports:
    row = [safe_cell(cell) for cell in _row(report)]
    for column, cell in zip(header, row):
      if len(cell) > MAX_CELL_CHARS:
        raise DeskError(
          "cell_too_large",
          f"The {report.source_id} \"{column}\" cell would have {len(cell):,} characters; "
          f"the limit is {MAX_CELL_CHARS:,} because spreadsheet software silently cuts "
          "longer cells. Shorten its quotations, notes or descriptions.",
          status=409,
        )
    writer.writerow(row)
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
    check for report in selected for dimension, _label in DIMENSIONS
    for check in report.all_checks(dimension)
  ]
  verified = sum(check.result == VERIFIED for check in checks)
  return {
    "file": EXPORT_PATH,
    "sources": chosen,
    "quotes_verified": verified,
    "quotes_failed": len(checks) - verified,
    "revision": revision,
  }
