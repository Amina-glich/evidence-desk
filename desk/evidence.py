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
after a hyphen (read both as a split word and as a real hyphen). Case, words,
numbers and order must match. There is no fuzzy or model-judged match: a
quote either occurs or the check fails.

A Reported finding may carry qualifications that must survive into the
export: a ``note``, checked ``absences`` (items searched for within the
dimension and not found, with what was searched), and ``contradictions``
(passages of the same source that disagree). Every contradiction quotes each
side and each quote is checked like evidence; the contradiction itself is
recorded, never resolved.
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
MAX_ABSENCES_PER_FINDING = 20
MAX_CONTRADICTIONS_PER_FINDING = 10
MAX_MEASUREMENTS = 100
MAX_LABEL_CHARS = 200
MAX_VALUE_TEXT_CHARS = 40

# Structured measurements (see ``measurements``): every compatibility field is
# required, either backed by a quotation or explicitly unknown.
MEASUREMENT_FIELDS = ("task", "dataset", "split", "metric", "metric_definition", "variant")
HARDWARE_FIELD = "hardware"
METRIC_KINDS = ("quality", "speed", "training_cost", "other")
_MEASUREMENT_KEYS = (
  {"dimension", "metric_kind", "value_text", "unit", "evidence", "fields"}, {"setting"},
)
MIN_QUOTE_CHARS = 10
MAX_QUOTE_CHARS = 1000
MAX_TEXT_CHARS = 2000
REPORT_PROBLEMS_MAX = 200
REPORT_QUOTE_CHARS = 120

DIMENSION_IDS = tuple(dimension for dimension, _label in DIMENSIONS)
STATUS_IDS = frozenset(status for status, _label in STATUSES)
_FINDING_KEYS = {
  "reported": ({"status", "value", "evidence"}, {"note", "absences", "contradictions"}),
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
# How a failed check reads to people, in the CSV and the evidence view.
RESULT_LABELS = {
  WRONG_PAGE: "found on a different page",
  NOT_FOUND: "not found in the source text",
  PAGE_OUT_OF_RANGE: "page is beyond the end of the PDF",
  TOO_SHORT: "quotation too short to check",
  SOURCE_UNAVAILABLE: "source text unavailable",
}


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
class Absence:
  """Something looked for within a dimension and not found."""
  item: str
  checked: str


@dataclass(frozen=True)
class Contradiction:
  """Passages of one source that disagree; each side is quoted."""
  description: str
  evidence: tuple[Quote, ...]


@dataclass(frozen=True)
class Finding:
  status: str
  value: str | None = None
  evidence: tuple[Quote, ...] = ()
  checked: str | None = None
  note: str | None = None
  absences: tuple[Absence, ...] = ()
  contradictions: tuple[Contradiction, ...] = ()


@dataclass(frozen=True)
class FieldEvidence:
  """One compatibility field of a measurement.

  Either ``label`` with the words ``as_written`` in the source, quoted by
  ``quote`` (or, when ``quote`` is None, by the measurement's own value
  quotation), or ``unknown`` with the reason the source does not state it.
  """
  label: str | None = None
  as_written: str | None = None
  quote: Quote | None = None
  unknown: str | None = None


@dataclass(frozen=True)
class Measurement:
  dimension: str
  metric_kind: str
  value_text: str
  unit: str
  evidence: tuple[Quote, ...]
  fields: dict[str, FieldEvidence]
  setting: str | None = None


@dataclass(frozen=True)
class ParsedEvidence:
  findings: dict[str, Finding]
  measurements: tuple[Measurement, ...] = ()


@dataclass(frozen=True)
class QuoteCheck:
  page: int
  quote: str
  result: str
  # "exact" or "normalized" for a verified quote.
  match: str | None = None
  found_on: tuple[int, ...] = ()


@dataclass(frozen=True)
class MeasurementCheck:
  """Quote checks of one measurement: its value quotations, and each field
  that has its own quotation."""
  value: tuple[QuoteCheck, ...]
  fields: dict[str, QuoteCheck]


def _text(value: object, *, max_chars: int = MAX_TEXT_CHARS) -> str | None:
  if isinstance(value, str) and value.strip() and len(value) <= max_chars and "\x00" not in value:
    return value.strip()
  return None


def _parse_quotes(items: object, where: str, problems: list[str], *, minimum: int) -> tuple[Quote, ...] | None:
  """A list of {page, quote} items, or None after recording what is wrong."""
  if not isinstance(items, list) or not minimum <= len(items) <= MAX_QUOTES_PER_FINDING:
    problems.append(f"{where}: needs {minimum}-{MAX_QUOTES_PER_FINDING} items.")
    return None
  quotes = []
  for index, entry in enumerate(items):
    at = f"{where}[{index}]"
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
  return tuple(quotes) if len(quotes) == len(items) else None


def _parse_absences(items: object, where: str, problems: list[str]) -> tuple[Absence, ...] | None:
  if not isinstance(items, list) or not 1 <= len(items) <= MAX_ABSENCES_PER_FINDING:
    problems.append(f"{where}: needs 1-{MAX_ABSENCES_PER_FINDING} items.")
    return None
  absences = []
  for index, entry in enumerate(items):
    at = f"{where}[{index}]"
    if not isinstance(entry, dict) or set(entry) != {"item", "checked"}:
      problems.append(f'{at}: needs exactly "item" and "checked".')
      continue
    item, checked = _text(entry["item"]), _text(entry["checked"])
    if item is None:
      problems.append(f"{at}.item: say what was looked for, in at most {MAX_TEXT_CHARS} characters.")
    elif checked is None:
      problems.append(f"{at}.checked: say which part of the source was searched.")
    else:
      absences.append(Absence(item, checked))
  return tuple(absences) if len(absences) == len(items) else None


def _parse_contradictions(items: object, where: str, problems: list[str]) -> tuple[Contradiction, ...] | None:
  if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONTRADICTIONS_PER_FINDING:
    problems.append(f"{where}: needs 1-{MAX_CONTRADICTIONS_PER_FINDING} items.")
    return None
  contradictions = []
  for index, entry in enumerate(items):
    at = f"{where}[{index}]"
    if not isinstance(entry, dict) or set(entry) != {"description", "evidence"}:
      problems.append(f'{at}: needs exactly "description" and "evidence".')
      continue
    description = _text(entry["description"])
    if description is None:
      problems.append(f"{at}.description: say what disagrees, in at most {MAX_TEXT_CHARS} characters.")
    # Each side of a contradiction is quoted, so at least two quotations, and
    # repeating one passage cannot show a disagreement.
    quotes = _parse_quotes(entry["evidence"], f"{at}.evidence", problems, minimum=2)
    if quotes is not None:
      sides = [(quote.page, normalize_quote(quote.quote)) for quote in quotes]
      if len(set(sides)) != len(sides):
        problems.append(f"{at}.evidence: repeats the same page and quotation; quote each side separately.")
        quotes = None
    if description is not None and quotes is not None:
      contradictions.append(Contradiction(description, quotes))
  return tuple(contradictions) if len(contradictions) == len(items) else None


def _parse_field(item: object, at: str, problems: list[str]) -> FieldEvidence | None:
  if isinstance(item, dict) and set(item) == {"unknown"}:
    reason = _text(item["unknown"])
    if reason is None:
      problems.append(f"{at}.unknown: say why the source does not state it.")
      return None
    return FieldEvidence(unknown=reason)
  if not isinstance(item, dict) or set(item) not in (
    {"label", "as_written"}, {"label", "as_written", "page", "quote"},
  ):
    problems.append(
      f'{at}: needs "label" and "as_written" (optionally "page" and "quote"), '
      'or {"unknown": reason}.'
    )
    return None
  label = _text(item["label"], max_chars=MAX_LABEL_CHARS)
  as_written = _text(item["as_written"], max_chars=MAX_LABEL_CHARS)
  if label is None or as_written is None:
    problems.append(f'{at}: "label" and "as_written" must be text of at most {MAX_LABEL_CHARS} characters.')
    return None
  quote = None
  if "quote" in item:
    quotes = _parse_quotes([{"page": item["page"], "quote": item["quote"]}], at, problems, minimum=1)
    if quotes is None:
      return None
    quote = quotes[0]
  return FieldEvidence(label=label, as_written=as_written, quote=quote)


def _parse_measurements(items: object, problems: list[str]) -> tuple[Measurement, ...] | None:
  if not isinstance(items, list) or len(items) > MAX_MEASUREMENTS:
    problems.append(f'"measurements" must be a list of at most {MAX_MEASUREMENTS} items.')
    return None
  measurements = []
  for index, item in enumerate(items):
    at = f"measurements[{index}]"
    required, optional = _MEASUREMENT_KEYS
    if not isinstance(item, dict) or not required <= set(item) or set(item) - required - optional:
      keys = ", ".join(sorted(required))
      problems.append(f"{at}: needs exactly {keys} (and optionally setting).")
      continue
    count = len(problems)
    if item["dimension"] not in DIMENSION_IDS:
      problems.append(f"{at}.dimension: use one of {', '.join(DIMENSION_IDS)}.")
    if item["metric_kind"] not in METRIC_KINDS:
      problems.append(f"{at}.metric_kind: use one of {', '.join(METRIC_KINDS)}.")
    value_text = _text(item["value_text"], max_chars=MAX_VALUE_TEXT_CHARS)
    if value_text is None:
      problems.append(f"{at}.value_text: the number exactly as the source writes it.")
    unit = _text(item["unit"], max_chars=MAX_VALUE_TEXT_CHARS)
    if unit is None:
      problems.append(f"{at}.unit: needs a unit, such as BLEU, % or ms.")
    setting = None
    if "setting" in item:
      setting = _text(item["setting"])
      if setting is None:
        problems.append(f"{at}.setting: must be non-empty text of at most {MAX_TEXT_CHARS} characters.")
    quotes = _parse_quotes(item["evidence"], f"{at}.evidence", problems, minimum=1)
    fields = {}
    raw_fields = item["fields"]
    if not isinstance(raw_fields, dict):
      problems.append(f"{at}.fields: must be an object keyed by field name.")
    else:
      allowed = set(MEASUREMENT_FIELDS) | {HARDWARE_FIELD}
      for name in sorted(set(raw_fields) - allowed):
        problems.append(f"{at}.fields.{name}: unknown field; use {', '.join(MEASUREMENT_FIELDS)} or {HARDWARE_FIELD}.")
      for name in MEASUREMENT_FIELDS:
        if name not in raw_fields:
          problems.append(f'{at}.fields.{name}: required; use {{"unknown": reason}} if the source does not state it.')
      for name in [name for name in (*MEASUREMENT_FIELDS, HARDWARE_FIELD) if name in raw_fields]:
        parsed = _parse_field(raw_fields[name], f"{at}.fields.{name}", problems)
        if parsed is not None:
          fields[name] = parsed
    if len(problems) == count:
      measurements.append(Measurement(
        dimension=item["dimension"], metric_kind=item["metric_kind"], value_text=value_text,
        unit=unit, evidence=quotes, fields=fields, setting=setting,
      ))
  return tuple(measurements) if len(measurements) == len(items) else None


def parse_evidence(raw: bytes, source_id: str) -> tuple[dict[str, Finding] | None, list[str]]:
  """Findings by dimension id, or None and the reasons the file is invalid."""
  parsed, problems = parse_evidence_file(raw, source_id)
  return (None if parsed is None else parsed.findings), problems


def parse_evidence_file(raw: bytes, source_id: str) -> tuple[ParsedEvidence | None, list[str]]:
  """Findings and measurements, or None and the reasons the file is invalid."""
  try:
    data = json.loads(raw)
  except (ValueError, RecursionError):
    return None, ["The file is not valid JSON."]
  if not isinstance(data, dict):
    return None, ["The file must contain a JSON object."]
  problems = []
  unknown = sorted(set(data) - {"schema", "source", "findings", "measurements"})
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
    note = None
    if "note" in item:
      note = _text(item["note"])
      if note is None:
        problems.append(f"{where}.note: must be non-empty text of at most {MAX_TEXT_CHARS} characters.")
    if missing or extra:
      continue
    if status == "reported":
      value = _text(item["value"])
      if value is None:
        problems.append(f"{where}.value: must be non-empty text of at most {MAX_TEXT_CHARS} characters.")
      quotes = _parse_quotes(item["evidence"], f"{where}.evidence", problems, minimum=1)
      absences = contradictions = ()
      if "absences" in item:
        absences = _parse_absences(item["absences"], f"{where}.absences", problems)
      if "contradictions" in item:
        contradictions = _parse_contradictions(item["contradictions"], f"{where}.contradictions", problems)
      if None not in (value, quotes, absences, contradictions):
        findings[dimension] = Finding(
          status, value=value, evidence=quotes, note=note,
          absences=absences, contradictions=contradictions,
        )
    elif status == "not_reported":
      checked = _text(item["checked"])
      if checked is None:
        problems.append(f"{where}.checked: say which part of the source was searched.")
      else:
        findings[dimension] = Finding(status, checked=checked, note=note)
    else:
      findings[dimension] = Finding(status, note=note)
  measurements = ()
  if "measurements" in data:
    measurements = _parse_measurements(data["measurements"], problems)
  if problems:
    return None, problems
  return ParsedEvidence(findings, measurements), []


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


# Source titles.

TITLE_REGION_CHARS = 2000
TITLE_MIN_LETTERS = 12
TITLE_MIN_WORDS = 2
_NOT_LETTERS = re.compile(r"[\W_]+")


def _letters(text: str) -> str:
  return _NOT_LETTERS.sub("", normalize_quote(text).casefold())


def file_name(path: str | None) -> str | None:
  """The last part of a project path (``inbox/paper.pdf`` -> ``paper.pdf``)."""
  name = (path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
  return name or None


TITLE_SCAN_LINES = 12
TITLE_MAX_LINES = 3
TITLE_MAX_CHARS = 200
TITLE_LINE_MAX_CHARS = 150
_TITLE_STOP_WORDS = frozenset(
  "of for the a an in on to with and via using by from at as is are or vs versus through into over "
  "under towards toward about without between".split()
)
_HEADER_LINE = re.compile(
  r"^(arxiv\b|preprint|under review|published\b|proceedings|accepted\b|submitted\b|conference|journal\b|"
  r"workshop|technical report|www\.|https?://|doi\b|©|copyright|\d+$|page \d|vol\.)", re.IGNORECASE,
)
_SECTION_LINE = re.compile(r"^(abstract|keywords?|index terms|summary|introduction|1\.? ?introduction)\b", re.IGNORECASE)
_AFFILIATION = re.compile(
  r"@|[*†‡§¶∗]|\b(university|universit[äée]|institute|laborator(y|ies)|labs?|department|school|college|"
  r"corporation|inc\.?|research|google|microsoft|deepmind|openai|meta)\b", re.IGNORECASE,
)


def _is_name_like(line: str) -> bool:
  """A line of capitalised words without small function words, as author lists are."""
  words = [word for word in re.split(r"[\s,]+", line) if word]
  return (
    2 <= len(words) <= 12
    and ":" not in line
    and all(word[0].isupper() for word in words)
    and not any(word.casefold() in _TITLE_STOP_WORDS for word in words)
  )


def _incomplete(title: str) -> bool:
  """Whether a title so far ends where a phrase cannot end (a line wrap)."""
  last = title.rstrip().rsplit(" ", 1)[-1]
  return title.rstrip().endswith((":", "-", ",")) or last.casefold() in _TITLE_STOP_WORDS


def first_page_title(first_page: str | None) -> str | None:
  """The title printed at the top of page 1, or None when it cannot be told
  with confidence. The title is the first lines before the first line that
  looks like authors, an affiliation, an abstract or a blank line; it must be
  followed by such a boundary within the first lines, so a title that runs
  into running text is never guessed. The result is a label taken verbatim
  from the stored page text, not a claim about the paper."""
  lines = [" ".join(normalize_quote(line).split()) for line in (first_page or "").splitlines()[:TITLE_SCAN_LINES * 2]]
  start = 0
  while start < len(lines) and (not lines[start] or _HEADER_LINE.match(lines[start])):
    start += 1
  collected: list[str] = []
  for line in lines[start:start + TITLE_SCAN_LINES]:
    if not line:
      if collected:
        break
      continue
    if _SECTION_LINE.match(line) or _AFFILIATION.search(line):
      break
    if collected and _is_name_like(line) and not _incomplete(" ".join(collected)):
      break
    if _HEADER_LINE.match(line) or len(line) > TITLE_LINE_MAX_CHARS or len(collected) >= TITLE_MAX_LINES:
      return None
    collected.append(line)
  else:
    return None
  title = " ".join(collected)
  if (
    not collected
    or len(collected) > TITLE_MAX_LINES
    or len(title) > TITLE_MAX_CHARS
    or len(title.split()) < TITLE_MIN_WORDS + 1
    or len(_letters(title)) < TITLE_MIN_LETTERS
    or title.endswith(".")
    or _incomplete(title)
  ):
    return None
  return title


def display_title(metadata_title: str | None, first_page: str | None, path: str | None) -> str | None:
  """The name to show for a source, from stored data only: the PDF's embedded
  title when its words appear at the top of page 1 (PDF metadata is often
  wrong: a template name, another paper, a word-processor file name), else the
  title read from the top of page 1, else the PDF file name. A label, not
  evidence about the paper."""
  title = " ".join(metadata_title.split()) if isinstance(metadata_title, str) else ""
  if (
    title
    and len(title.split()) >= TITLE_MIN_WORDS
    and len(_letters(title)) >= TITLE_MIN_LETTERS
    and _letters(title) in _letters((first_page or "")[:TITLE_REGION_CHARS])
  ):
    return title
  return first_page_title(first_page) or file_name(path)


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
  # Evidence quote checks, in the order of Finding.evidence.
  checks: dict[str, tuple[QuoteCheck, ...]] = field(default_factory=dict)
  # Per contradiction, its quote checks, in the order of Finding.contradictions.
  contradiction_checks: dict[str, tuple[tuple[QuoteCheck, ...], ...]] = field(default_factory=dict)
  measurements: tuple[Measurement, ...] = ()
  # One per measurement, in the same order.
  measurement_checks: tuple[MeasurementCheck, ...] = ()
  problems: list[str] = field(default_factory=list)

  def all_checks(self, dimension: str) -> tuple[QuoteCheck, ...]:
    """Every quotation check of one dimension: evidence, then contradictions."""
    return self.checks.get(dimension, ()) + tuple(
      check for checks in self.contradiction_checks.get(dimension, ()) for check in checks
    )

  def measurement_quote_checks(self) -> tuple[QuoteCheck, ...]:
    """Every quotation check of every measurement: values, then own field quotes."""
    return tuple(
      check for checks in self.measurement_checks
      for check in (*checks.value, *checks.fields.values())
    )

  def quote_checks(self) -> tuple[QuoteCheck, ...]:
    """Every quotation check of this source."""
    return tuple(
      check for dimension in DIMENSION_IDS for check in self.all_checks(dimension)
    ) + self.measurement_quote_checks()


def check_report_quotes(report: SourceReport, check) -> None:
  """Fill a report's quote checks; ``check`` maps a Quote to its QuoteCheck."""
  for dimension, finding in report.findings.items():
    if finding.evidence:
      report.checks[dimension] = tuple(check(quote) for quote in finding.evidence)
    if finding.contradictions:
      report.contradiction_checks[dimension] = tuple(
        tuple(check(quote) for quote in contradiction.evidence)
        for contradiction in finding.contradictions
      )
  report.measurement_checks = tuple(
    MeasurementCheck(
      value=tuple(check(quote) for quote in measurement.evidence),
      fields={
        name: check(evidence.quote)
        for name, evidence in measurement.fields.items() if evidence.quote is not None
      },
    )
    for measurement in report.measurements
  )


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
        report.file = source.record.get("file")
        report.title = display_title(source.title, source.pages[0] if source.pages else None, report.file)
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
        parsed, problems = parse_evidence_file(raw, source_id)
        if parsed is None:
          report.evidence = "invalid"
          report.problems.extend(f"evidence/{source_id}.json: {problem}" for problem in problems)
        else:
          report.evidence = "valid"
          report.findings = parsed.findings
          report.measurements = parsed.measurements
    if report.findings or report.measurements:
      if source is not None:
        variants = tuple(page_variants(text) for text in source.pages)
        check = lambda quote: check_quote(quote, variants, source.pages)
      else:
        check = lambda quote: QuoteCheck(quote.page, quote.quote, SOURCE_UNAVAILABLE)
      check_report_quotes(report, check)
    reports.append(report)
  return reports


def status_of(report: SourceReport, dimension: str) -> str:
  finding = report.findings.get(dimension)
  return finding.status if finding is not None else "not_assessed"


def _short(text: str) -> str:
  return text if len(text) <= REPORT_QUOTE_CHARS else text[:REPORT_QUOTE_CHARS - 1] + "…"


def check_evidence(binding: ProjectBinding, _arguments: dict) -> dict:
  """The ``check_evidence`` tool: a bounded report, writing nothing."""
  # measurements builds on this module, so it is imported where it is used.
  from desk.measurements import assess

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
      labelled = [("evidence", check) for check in report.checks.get(dimension, ())]
      labelled += [
        ("contradiction", check)
        for checks in report.contradiction_checks.get(dimension, ()) for check in checks
      ]
      for part, check in labelled:
        if check.result == VERIFIED:
          verified += 1
          continue
        failed += 1
        problem = {
          "source": report.source_id, "dimension": dimension, "part": part,
          "page": check.page, "result": check.result, "quote": _short(check.quote),
        }
        if check.found_on:
          problem["found_on"] = list(check.found_on[:20])
        problems.append(problem)
    for item in assess(report):
      quote_checks = (*item.checks.value, *item.checks.fields.values())
      for check in quote_checks:
        if check.result == VERIFIED:
          verified += 1
          continue
        failed += 1
        problem = {
          "source": report.source_id, "part": "measurement", "measurement": item.index + 1,
          "page": check.page, "result": check.result, "quote": _short(check.quote),
        }
        if check.found_on:
          problem["found_on"] = list(check.found_on[:20])
        problems.append(problem)
      # Quotation failures are reported above; these are the other V2-V6 problems.
      problems.extend(
        {"source": report.source_id, "part": "measurement", "measurement": item.index + 1,
         "problem": code, "detail": reason}
        for code, reason in item.problems if not code.startswith("quote_not_verified")
      )
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
      "absences": sum(len(finding.absences) for finding in report.findings.values()),
      "contradictions": sum(len(finding.contradictions) for finding in report.findings.values()),
      "measurements": len(report.measurements),
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
