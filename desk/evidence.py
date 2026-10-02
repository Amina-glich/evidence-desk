"""Agent-written evidence files and deterministic quotation checking.

``evidence/S<n>.json`` holds the agent's findings for source ``S<n>`` (format
in ``evidence-desk.md``). Files are validated strictly: an unknown key,
dimension or status makes the whole file invalid rather than silently
ignored, so a typo can never turn into a missing finding.

A quotation is checked only against the registered page text of its source
(see ``sources.load_source``). Matching is exact substring search after a
fixed normalization that undoes PDF extraction artefacts only: Unicode
compatibility forms (ligatures such as "ﬁ"), typographic quotes and dashes,
soft hyphens and zero-width characters, whitespace runs, and line breaks
after a hyphen (read both as a split word and as a real hyphen). Case, words, numbers and order must match. There is
no fuzzy or model-judged match: a quote either occurs or the check fails.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from desk import SCHEMA_VERSION
from desk.binding import ProjectBinding
from desk.errors import DeskError
from desk.project_fs import SOURCE_ID_RE, Project
from desk.sources import Source, load_source
from desk.vocabulary import DIMENSIONS, STATUSES


EVIDENCE_DIR = "evidence"
EVIDENCE_FILE_RE = re.compile(r"^(S[1-9][0-9]{0,5})\.json$")
EVIDENCE_MAX_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_FILES = 500
MAX_QUOTES_PER_FINDING = 20
MIN_QUOTE_CHARS = 10
MAX_QUOTE_CHARS = 1000
MAX_TEXT_CHARS = 2000
REPORT_PROBLEMS_MAX = 200
REPORT_QUOTE_CHARS = 120

DIMENSION_IDS = tuple(dimension for dimension, _label in DIMENSIONS)
STATUS_IDS = frozenset(status for status, _label in STATUSES)
_FINDING_KEYS = {
  "reported": ({"status", "value", "evidence"}, {"note"}),
  "not_reported": ({"status", "checked"}, {"note"}),
  "not_assessed": ({"status"}, {"note"}),
}

# Quote check results. Only VERIFIED passes.
VERIFIED = "verified"
WRONG_PAGE = "wrong_page"
NOT_FOUND = "not_found"
PAGE_OUT_OF_RANGE = "page_out_of_range"
TOO_SHORT = "too_short"
SOURCE_UNAVAILABLE = "source_unavailable"


# Normalization.

_CHARACTER_MAP = str.maketrans({
  "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
  "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
  "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
  "―": "-", "−": "-",
  "­": None, "​": None, "‌": None, "‍": None, "⁠": None,
  "﻿": None,
})
_WHITESPACE = re.compile(r"\s+")
# A word split by a hyphen at a line break: "repre-\nsentation".
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-[ \t]*\r?\n\s*(\w)")


def _base(text: str) -> str:
  return unicodedata.normalize("NFKC", text).translate(_CHARACTER_MAP)


def _collapse(text: str) -> str:
  return _WHITESPACE.sub(" ", text).strip()


def normalize_quote(text: str) -> str:
  return _collapse(_base(text))


def page_variants(text: str) -> tuple[str, ...]:
  """Normalized forms of one page: as extracted; with a word split at a
  line-break hyphen joined ("repre-\\nsentation"); and with the hyphen kept
  but the break removed, for a real hyphen at a line end
  ("state-of-the-\\nart")."""
  base = _base(text)
  return (
    _collapse(base),
    _collapse(_LINE_BREAK_HYPHEN.sub(r"\1\2", base)),
    _collapse(_LINE_BREAK_HYPHEN.sub(r"\1-\2", base)),
  )


# Parsed evidence.

@dataclass(frozen=True)
class Quote:
  page: int
  quote: str


@dataclass(frozen=True)
class Finding:
  status: str
  value: str | None = None
  evidence: tuple[Quote, ...] = ()
  checked: str | None = None


@dataclass(frozen=True)
class QuoteCheck:
  page: int
  quote: str
  result: str
  # "exact" or "normalized" for a verified quote.
  match: str | None = None
  found_on: tuple[int, ...] = ()


def _text(value: object, *, max_chars: int = MAX_TEXT_CHARS) -> str | None:
  if isinstance(value, str) and value.strip() and len(value) <= max_chars and "\x00" not in value:
    return value.strip()
  return None


def parse_evidence(raw: bytes, source_id: str) -> tuple[dict[str, Finding] | None, list[str]]:
  """Findings by dimension id, or None and the reasons the file is invalid."""
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    return None, ["The file is not valid JSON."]
  if not isinstance(data, dict):
    return None, ["The file must contain a JSON object."]
  problems = []
  unknown = sorted(set(data) - {"schema", "source", "findings"})
  if unknown:
    problems.append(f"Unknown top-level key(s): {', '.join(unknown)}.")
  if data.get("schema") != SCHEMA_VERSION:
    problems.append(f'"schema" must be {SCHEMA_VERSION}.')
  if data.get("source") != source_id:
    problems.append(f'"source" must be "{source_id}", matching the file name.')
  raw_findings = data.get("findings")
  if not isinstance(raw_findings, dict):
    return None, problems + ['"findings" must be an object keyed by dimension id.']
  findings = {}
  for dimension, item in raw_findings.items():
    where = f"findings.{dimension}"
    if dimension not in DIMENSION_IDS:
      problems.append(f"{where}: unknown dimension; use one of {', '.join(DIMENSION_IDS)}.")
      continue
    if not isinstance(item, dict) or item.get("status") not in STATUS_IDS:
      problems.append(f"{where}: needs \"status\": one of {', '.join(sorted(STATUS_IDS))}.")
      continue
    status = item["status"]
    required, optional = _FINDING_KEYS[status]
    missing = sorted(required - set(item))
    extra = sorted(set(item) - required - optional)
    if missing:
      problems.append(f"{where}: {status} needs {', '.join(missing)}.")
    if extra:
      problems.append(f"{where}: {status} does not take {', '.join(extra)}.")
    if "note" in item and _text(item["note"]) is None:
      problems.append(f"{where}.note: must be non-empty text of at most {MAX_TEXT_CHARS} characters.")
    if missing or extra:
      continue
    if status == "reported":
      value = _text(item["value"])
      if value is None:
        problems.append(f"{where}.value: must be non-empty text of at most {MAX_TEXT_CHARS} characters.")
      quotes = []
      evidence = item["evidence"]
      if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_QUOTES_PER_FINDING:
        problems.append(f"{where}.evidence: needs 1-{MAX_QUOTES_PER_FINDING} items.")
        continue
      for index, entry in enumerate(evidence):
        at = f"{where}.evidence[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"page", "quote"}:
          problems.append(f'{at}: needs exactly "page" and "quote".')
          continue
        page, quote = entry["page"], _text(entry["quote"], max_chars=MAX_QUOTE_CHARS)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
          problems.append(f"{at}.page: must be a whole number from 1 (the PDF page position).")
        elif quote is None:
          problems.append(f"{at}.quote: must be non-empty text of at most {MAX_QUOTE_CHARS} characters.")
        else:
          quotes.append(Quote(page, quote))
      if value is not None and len(quotes) == len(evidence):
        findings[dimension] = Finding(status, value=value, evidence=tuple(quotes))
    elif status == "not_reported":
      checked = _text(item["checked"])
      if checked is None:
        problems.append(f"{where}.checked: say which part of the source was searched.")
      else:
        findings[dimension] = Finding(status, checked=checked)
    else:
      findings[dimension] = Finding(status)
  return (None, problems) if problems else (findings, [])


def check_quote(quote: Quote, pages: tuple[tuple[str, ...], ...], raw_pages: tuple[str, ...]) -> QuoteCheck:
  """Check one quote against normalized page variants of its source."""
  needle = normalize_quote(quote.quote)
  if len(needle) < MIN_QUOTE_CHARS:
    return QuoteCheck(quote.page, quote.quote, TOO_SHORT)
  found = tuple(
    index + 1 for index, variants in enumerate(pages)
    if any(needle in variant for variant in variants)
  )
  if quote.page > len(pages):
    return QuoteCheck(quote.page, quote.quote, PAGE_OUT_OF_RANGE, found_on=found)
  if quote.page in found:
    exact = quote.quote in raw_pages[quote.page - 1]
    return QuoteCheck(quote.page, quote.quote, VERIFIED, match="exact" if exact else "normalized")
  if found:
    return QuoteCheck(quote.page, quote.quote, WRONG_PAGE, found_on=found)
  return QuoteCheck(quote.page, quote.quote, NOT_FOUND)


# Whole-project check.

@dataclass
class SourceReport:
  source_id: str
  registered: bool
  # valid | invalid | missing | refused
  evidence: str = "missing"
  # ok | the DeskError code that made the source text unusable
  source_text: str = "ok"
  title: str | None = None
  file: str | None = None
  findings: dict[str, Finding] = field(default_factory=dict)
  checks: dict[str, tuple[QuoteCheck, ...]] = field(default_factory=dict)
  problems: list[str] = field(default_factory=list)


def _evidence_files(project: Project) -> list[str]:
  try:
    entries, truncated = project.list_dir(EVIDENCE_DIR, limit=MAX_EVIDENCE_FILES)
  except DeskError as exc:
    if exc.code == "not_found":
      return []
    raise
  if truncated:
    raise DeskError("too_large", f"evidence/ has more than {MAX_EVIDENCE_FILES} entries.")
  return [
    match.group(1) for name, kind in entries
    if kind == "file" and (match := EVIDENCE_FILE_RE.match(name))
  ]


def check_project(project: Project) -> list[SourceReport]:
  """Validate every evidence file and check every quotation, by source id."""
  registered = project.source_ids()
  with_evidence = _evidence_files(project)
  ids = sorted(set(registered) | set(with_evidence), key=lambda value: int(value[1:]))
  reports = []
  for source_id in ids:
    report = SourceReport(source_id, registered=source_id in registered)
    source: Source | None = None
    if report.registered:
      try:
        source = load_source(project, source_id)
        report.title = source.title
        report.file = source.record.get("file")
      except DeskError as exc:
        report.source_text = exc.code
        report.problems.append(exc.message)
    else:
      report.source_text = "source_missing"
      report.problems.append(f"evidence/{source_id}.json has no registered source {source_id}.")
    if source_id in with_evidence:
      try:
        raw = project.read_bytes(f"{EVIDENCE_DIR}/{source_id}.json", max_bytes=EVIDENCE_MAX_BYTES)
      except DeskError as exc:
        if exc.code not in ("unsafe_path", "too_large", "not_found"):
          raise
        report.evidence = "refused"
        report.problems.append(f"evidence/{source_id}.json could not be read: {exc.message}")
      else:
        findings, problems = parse_evidence(raw, source_id)
        if findings is None:
          report.evidence = "invalid"
          report.problems.extend(f"evidence/{source_id}.json: {problem}" for problem in problems)
        else:
          report.evidence = "valid"
          report.findings = findings
    if source is not None and report.findings:
      variants = tuple(page_variants(text) for text in source.pages)
      for dimension, finding in report.findings.items():
        if finding.evidence:
          report.checks[dimension] = tuple(
            check_quote(quote, variants, source.pages) for quote in finding.evidence
          )
    elif report.findings:
      for dimension, finding in report.findings.items():
        if finding.evidence:
          report.checks[dimension] = tuple(
            QuoteCheck(quote.page, quote.quote, SOURCE_UNAVAILABLE) for quote in finding.evidence
          )
    reports.append(report)
  return reports


def status_of(report: SourceReport, dimension: str) -> str:
  finding = report.findings.get(dimension)
  return finding.status if finding is not None else "not_assessed"


def _short(text: str) -> str:
  return text if len(text) <= REPORT_QUOTE_CHARS else text[:REPORT_QUOTE_CHARS - 1] + "…"


def check_evidence(binding: ProjectBinding, _arguments: dict) -> dict:
  """The ``check_evidence`` tool: a bounded report, writing nothing."""
  with binding.open() as project:
    reports = check_project(project)
  sources, problems = [], []
  totals = {"quotes": 0, "verified": 0, "failed": 0}
  for report in reports:
    counts = {status: 0 for status, _label in STATUSES}
    for dimension in DIMENSION_IDS:
      counts[status_of(report, dimension)] += 1
    verified = failed = 0
    for dimension in DIMENSION_IDS:
      for check in report.checks.get(dimension, ()):
        if check.result == VERIFIED:
          verified += 1
          continue
        failed += 1
        problem = {
          "source": report.source_id, "dimension": dimension, "page": check.page,
          "result": check.result, "quote": _short(check.quote),
        }
        if check.found_on:
          problem["found_on"] = list(check.found_on[:20])
        problems.append(problem)
    problems.extend({"source": report.source_id, "problem": text} for text in report.problems)
    totals["quotes"] += verified + failed
    totals["verified"] += verified
    totals["failed"] += failed
    sources.append({
      "source": report.source_id,
      "title": report.title,
      "evidence": report.evidence,
      "source_text": report.source_text,
      **counts,
      "quotes_verified": verified,
      "quotes_failed": failed,
    })
  result = {
    "ok": bool(reports) and not problems,
    "summary": {
      "sources": len(reports),
      "invalid_evidence_files": sum(report.evidence == "invalid" for report in reports),
      **totals,
    },
    "sources": sources,
    "problems": problems[:REPORT_PROBLEMS_MAX],
  }
  if len(problems) > REPORT_PROBLEMS_MAX:
    result["problems_truncated"] = len(problems) - REPORT_PROBLEMS_MAX
  if not reports:
    result["note"] = "No sources are registered yet. Register an inbox PDF with add_source."
  return result
