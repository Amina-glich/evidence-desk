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

``exports/comparison-by-dimension.csv`` holds the same cells in eleven columns,
one row per source and dimension, which is easier to read on a narrow screen
(``build_narrow_csv``).

The same checked reports also render ``exports/comparison.html``, the
citation-linked evidence view (see ``viewer``), so the CSV and the view can
never disagree. ``select_reports`` is the one scope and refusal rule both
use, and the Project builder of the comparison Creation uses it too.

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
from dataclasses import dataclass

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.evidence import RESULT_LABELS, VERIFIED, QuoteCheck, SourceReport, check_project, status_of
from desk.project_fs import SOURCE_ID_RE, Project, project_lock
from desk.viewer import render_html
from desk.vocabulary import DIMENSIONS, STATUSES


EXPORT_PATH = "exports/comparison.csv"
VIEW_PATH = "exports/comparison.html"
NARROW_PATH = "exports/comparison-by-dimension.csv"
QUESTION_MAX_CHARS = 500
DESK_JSON_MAX_BYTES = 256 * 1024
# Excel stores at most 32,767 characters in a cell and silently cuts the rest
# (other spreadsheet apps have similar limits). Stay below it, with room for
# the formula-neutralizing prefix.
MAX_CELL_CHARS = 32_000
STATUS_LABELS = dict(STATUSES)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str) -> str:
  """Neutralize spreadsheet formula injection (OWASP CSV injection)."""
  return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


@dataclass(frozen=True)
class Selection:
  """The checked reports in scope, in desk.json order, and the question."""
  reports: list[SourceReport]
  research_question: str | None


def _desk(project: Project) -> tuple[list[str] | None, str | None]:
  """desk.json ``compare_sources`` (None means all) and ``research_question``."""
  try:
    raw = project.read_bytes("desk.json", max_bytes=DESK_JSON_MAX_BYTES)
  except DeskError as exc:
    if exc.code == "not_found":
      return None, None
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
  question = data.get("research_question")
  question = question.strip()[:QUESTION_MAX_CHARS] if isinstance(question, str) and question.strip() else None
  return list(dict.fromkeys(scope)) or None, question


def _failure(check: QuoteCheck) -> str:
  return (
    f"p.{check.page} {RESULT_LABELS.get(check.result, check.result)}"
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


NARROW_HEADER = [
  "Source", "Title", "File", "Dimension", "Status", "Finding", "Evidence", "Check",
  "Note", "Checked absences", "Contradictions",
]


def narrow_rows(report: SourceReport) -> list[list[str]]:
  """The same cells as ``_row``, one row per dimension (see ``build_narrow_csv``)."""
  wide = _row(report)
  base = 3 + 4 * len(DIMENSIONS)
  rows = []
  for index, (_dimension, label) in enumerate(DIMENSIONS):
    first, extra = 3 + 4 * index, base + 3 * index
    rows.append([report.source_id, report.title or "", report.file or "", label, *wide[first:first + 4], *wide[extra:extra + 3]])
  return rows


def build_narrow_csv(reports: list[SourceReport]) -> bytes:
  """The same findings as ``build_csv`` in eleven columns instead of 45: one row
  per source and dimension, so a narrow screen shows a short row of related
  cells instead of a very wide one. Every status, finding, quotation, check,
  note, checked absence and contradiction is the same text."""
  return _write_csv(NARROW_HEADER, [(report, row) for report in reports for row in narrow_rows(report)])


def build_csv(reports: list[SourceReport]) -> bytes:
  """The CSV bytes, or ``cell_too_large`` instead of a cell a spreadsheet would cut."""
  return _write_csv(header_row(), [(report, _row(report)) for report in reports])


def _write_csv(header: list[str], rows: list[tuple[SourceReport, list[str]]]) -> bytes:
  out = io.StringIO()
  writer = csv.writer(out, lineterminator="\r\n")
  writer.writerow(header)
  for report, raw in rows:
    row = [safe_cell(cell) for cell in raw]
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


def select_reports(project: Project) -> Selection:
  """Check the project and return the sources in scope, or refuse."""
  scope, question = _desk(project)
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
  return Selection(selected, question)


def export_comparison(binding: ProjectBinding, _arguments: dict) -> dict:
  with binding.open() as project:
    selection = select_reports(project)
    data = build_csv(selection.reports)
    narrow = build_narrow_csv(selection.reports)
    view = render_html(selection.reports, research_question=selection.research_question).encode("utf-8")
    with project_lock(binding.app_storage_dir, binding.project_id):
      revision = project.write_atomic(EXPORT_PATH, data)
      narrow_revision = project.write_atomic(NARROW_PATH, narrow)
      view_revision = project.write_atomic(VIEW_PATH, view)
  checks = [check for report in selection.reports for check in report.quote_checks()]
  verified = sum(check.result == VERIFIED for check in checks)
  return {
    "file": EXPORT_PATH,
    "view": VIEW_PATH,
    "narrow_file": NARROW_PATH,
    "sources": [report.source_id for report in selection.reports],
    "quotes_verified": verified,
    "quotes_failed": len(checks) - verified,
    "revision": revision,
    "view_revision": view_revision,
    "narrow_revision": narrow_revision,
  }
