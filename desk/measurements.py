"""Which recorded measurements may be plotted together, and why others not.

A measurement (``evidence.Measurement``) is a number as the source writes
it, with its unit and compatibility fields: task, dataset, split, metric,
metric definition and model variant, plus hardware for speed and training
cost. Every field is backed by a verified quotation or explicitly unknown.

Checks on one measurement (problems in its evidence):

- V1 every quotation of the value and of its fields verifies on its page;
- V2 the value is a plain number and appears as a whole token in a verified
  value quotation (``28.4`` does not match ``128.4`` or ``-28.4``);
- V3 each field's ``as_written`` words appear in its quotation; this
  includes hardware whenever it is supplied, for every metric kind;
- V4 a percent value has unit ``%`` and the reverse, and the quotation
  agrees: a ``%`` value is quoted with a percent sign, and a value quoted
  as a percentage is not declared with another unit;
- V5 differences and ratios (``+1.6``, ``9.3x``, ``2 times``) are refused;
- V6 speed and training-cost measurements name their hardware.

Rules for a plot (all must hold):

- P1 at least two sources;
- P2 every plotted value passes V1-V6;
- P3 identical labels (case and spacing aside) for every compatibility field,
  the unit and the metric kind; hardware too for speed and training cost;
- P4 no compatibility field is unknown: a missing field is never inferred;
- P5 a source that reports different values for one combination is never
  plotted for it. Conflicts are found among all of a source's measurements,
  whatever their other problems, and an unknown field counts as possibly the
  same combination;
- P6 a value involved in a contradiction recorded in the same source and
  dimension is never plotted. If that dimension's recorded contradiction
  cannot be fully verified, no value from it is plotted.

Everything else is reported, with the reason, never dropped. Nothing here
ranks values or decides which is better.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from desk.evidence import (
  HARDWARE_FIELD, MEASUREMENT_FIELDS, RESULT_LABELS, VERIFIED, FieldEvidence, Measurement,
  MeasurementCheck, Quote, QuoteCheck, SourceReport, normalize_quote,
)


HARDWARE_KINDS = frozenset({"speed", "training_cost"})
FIELD_NAMES = {
  "task": "task", "dataset": "dataset", "split": "split", "metric": "metric",
  "metric_definition": "metric definition", "variant": "model variant",
  HARDWARE_FIELD: "hardware",
}
PROTOCOL_FIELDS = ("metric_definition", "variant", HARDWARE_FIELD)

_NUMBER = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?%?$")
_NUMBER_IN_TEXT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_RELATIVE = re.compile(r"^[+\-−±]|×|\d\s*[xX]$|\btimes\b", re.IGNORECASE)
_PERCENT_AFTER = r"\s?(?:%|percent\b)"


@dataclass(frozen=True)
class Assessed:
  """One measurement with the outcome of V1-V6."""
  source_id: str
  index: int
  measurement: Measurement
  checks: MeasurementCheck
  number: float | None
  # (code, readable reason) for each evidence problem.
  problems: tuple[tuple[str, str], ...]
  # Compatibility fields the source does not state.
  unknown: tuple[str, ...]
  # Index in measurement.evidence of the first verified quotation that shows
  # the value as declared; None when there is none.
  value_quote: int | None = None
  # The number, or the normalized text when it is not a plain number; used
  # to tell whether two values of one source differ.
  identity: object = None
  # Normalized compatibility labels, None where unknown or missing.
  parts: tuple[str | None, ...] = ()

  @property
  def eligible(self) -> bool:
    return not self.problems and not self.unknown

  def fields_to_check(self) -> tuple[str, ...]:
    names = MEASUREMENT_FIELDS
    if self.measurement.metric_kind in HARDWARE_KINDS:
      names = (*names, HARDWARE_FIELD)
    return names


@dataclass
class Row:
  """One table row: plotted in ``chart``, or not compared for ``reason``."""
  assessed: Assessed
  chart: int | None = None
  reason: str | None = None


@dataclass(frozen=True)
class Point:
  source_id: str
  number: float
  assessed: Assessed


@dataclass
class Chart:
  number: int
  # The shared labels, from the first plotted measurement.
  labels: dict[str, str]
  unit: str
  metric_kind: str
  points: list[Point] = field(default_factory=list)


@dataclass
class Comparison:
  rows: list[Row]
  charts: list[Chart]


def parse_number(value_text: str) -> float | None:
  """The value of a plain written number (``28.4``, ``50,000``, ``91.2%``), else None."""
  text = value_text.strip()
  if not _NUMBER.match(text):
    return None
  return float(text.replace(",", "").rstrip("%"))


def _number_pattern(value_text: str) -> str:
  base = normalize_quote(value_text).rstrip("%").rstrip()
  return rf"(?<![\d.,+\-−]){re.escape(base)}(?!\d|[.,]\d)"


def value_in_quote(value_text: str, quote: str, *, percent: bool | None = None) -> bool:
  """Whether the number occurs in ``quote`` as a whole token, as a percentage
  exactly when ``percent`` (by default: when ``value_text`` ends with %)."""
  if percent is None:
    percent = value_text.strip().endswith("%")
  pattern = _number_pattern(value_text)
  pattern += _PERCENT_AFTER if percent else rf"(?!{_PERCENT_AFTER})"
  return re.search(pattern, normalize_quote(quote), flags=re.IGNORECASE) is not None


def _number_anywhere(value_text: str, quote: str) -> bool:
  return re.search(_number_pattern(value_text), normalize_quote(quote)) is not None


def _numbers_in(text: str) -> set[float]:
  return {float(match.replace(",", "")) for match in _NUMBER_IN_TEXT.findall(normalize_quote(text))}


def _words_in(as_written: str, quote: str) -> bool:
  return normalize_quote(as_written).casefold() in normalize_quote(quote).casefold()


def _label(value: str) -> str:
  return " ".join(value.casefold().split())


def _failed(check: QuoteCheck) -> str:
  return f"p.{check.page} {RESULT_LABELS.get(check.result, check.result)}"


def _key_names(measurement: Measurement) -> tuple[str, ...]:
  names = MEASUREMENT_FIELDS
  if measurement.metric_kind in HARDWARE_KINDS:
    names = (*names, HARDWARE_FIELD)
  return names


def _parts(measurement: Measurement) -> tuple[str | None, ...]:
  parts = []
  for name in _key_names(measurement):
    evidence = measurement.fields.get(name)
    parts.append(None if evidence is None or evidence.label is None else _label(evidence.label))
  return tuple(parts)


def _identity(value_text: str) -> object:
  number = None if _RELATIVE.search(value_text) else parse_number(value_text)
  return number if number is not None else ("text", " ".join(normalize_quote(value_text).split()))


def assess(report: SourceReport) -> list[Assessed]:
  """V1-V6 for every measurement of one source."""
  assessed = []
  for index, (measurement, checks) in enumerate(zip(report.measurements, report.measurement_checks)):
    problems: list[tuple[str, str]] = []
    unknown: list[str] = []
    value_text = measurement.value_text
    unit_is_percent = measurement.unit.strip() == "%"
    verified = [
      (position, quote.quote) for position, (quote, check) in enumerate(zip(measurement.evidence, checks.value))
      if check.result == VERIFIED
    ]
    verified_quotes = [quote for _position, quote in verified]
    for check in checks.value:
      if check.result != VERIFIED:
        problems.append(("quote_not_verified", f"the value's quotation did not verify ({_failed(check)})"))
    number = None
    value_quote = None
    if _RELATIVE.search(value_text):
      problems.append(("relative_value", "a difference or ratio, not a measured value"))
    else:
      number = parse_number(value_text)
      if number is None:
        problems.append(("value_unparsed", f'"{value_text}" is not a plain written number'))
      elif verified:
        value_quote = next(
          (position for position, quote in verified if value_in_quote(value_text, quote, percent=unit_is_percent)),
          None,
        )
        if value_quote is None:
          if any(_number_anywhere(value_text, quote) for quote in verified_quotes):
            shown = "as a percentage" if not unit_is_percent else "without a percent sign"
            problems.append((
              "percent_mismatch",
              f'the quotation shows "{value_text.rstrip("%")}" {shown}, which does not match the unit "{measurement.unit}"',
            ))
          else:
            problems.append(("value_not_in_quote", f'"{value_text}" does not appear in its quotation'))
    if value_text.strip().endswith("%") != unit_is_percent:
      problems.append(("unit_mismatch", f'"{value_text}" does not match the unit "{measurement.unit}"'))
    names = list(MEASUREMENT_FIELDS)
    if measurement.metric_kind in HARDWARE_KINDS:
      names.append(HARDWARE_FIELD)
      if HARDWARE_FIELD not in measurement.fields:
        problems.append(("hardware_missing", "speed and training-cost values must name their hardware"))
    elif HARDWARE_FIELD in measurement.fields and measurement.fields[HARDWARE_FIELD].unknown is None:
      # Not part of the comparison here, but shown, so its words are checked too.
      names.append(HARDWARE_FIELD)
    for name in names:
      evidence: FieldEvidence | None = measurement.fields.get(name)
      if evidence is None:
        continue
      if evidence.unknown is not None:
        unknown.append(name)
        continue
      if evidence.quote is not None:
        check = checks.fields[name]
        if check.result != VERIFIED:
          problems.append((f"quote_not_verified:{name}", f"the {FIELD_NAMES[name]} quotation did not verify ({_failed(check)})"))
          continue
        quotes = [evidence.quote.quote]
      else:
        quotes = verified_quotes
      if not any(_words_in(evidence.as_written, quote) for quote in quotes):
        problems.append((
          f"field_not_evidenced:{name}",
          f'the {FIELD_NAMES[name]} words "{evidence.as_written}" are not in its quotation',
        ))
    assessed.append(Assessed(
      report.source_id, index, measurement, checks,
      number if not problems else None, tuple(problems), tuple(unknown),
      value_quote=value_quote if not problems else None,
      identity=_identity(value_text),
      parts=_parts(measurement),
    ))
  return assessed


def _key(item: Assessed) -> tuple:
  return (item.measurement.metric_kind, _label(item.measurement.unit), *item.parts)


def _benchmark(item: Assessed) -> tuple:
  measurement = item.measurement
  return (measurement.metric_kind, _label(measurement.unit), *item.parts[:4])


def _may_share_key(a: Assessed, b: Assessed) -> bool:
  """Whether two measurements could describe the same combination: equal
  where both labels are known, and an unknown or missing label matches."""
  if a.measurement.metric_kind != b.measurement.metric_kind:
    return False
  if _label(a.measurement.unit) != _label(b.measurement.unit):
    return False
  return all(x is None or y is None or x == y for x, y in zip(a.parts, b.parts))


def _conflicts(items: list[Assessed]) -> dict[int, list[Assessed]]:
  """For each measurement of one source, the others it conflicts with."""
  found: dict[int, list[Assessed]] = {}
  for position, item in enumerate(items):
    for other in items[position + 1:]:
      if item.identity != other.identity and _may_share_key(item, other):
        found.setdefault(item.index, []).append(other)
        found.setdefault(other.index, []).append(item)
  return found


def _overlaps(a: str, b: str) -> bool:
  a, b = normalize_quote(a).casefold(), normalize_quote(b).casefold()
  return a in b or b in a


def _involved(item: Assessed, side: Quote) -> bool:
  """Whether a recorded contradiction side concerns this measurement: it
  overlaps one of the value's verified quotations on the same page, or it
  shows the same number."""
  for quote, check in zip(item.measurement.evidence, item.checks.value):
    if check.result == VERIFIED and quote.page == side.page and _overlaps(quote.quote, side.quote):
      return True
  if isinstance(item.identity, float) and item.identity in _numbers_in(side.quote):
    return True
  return _number_anywhere(item.measurement.value_text, side.quote)


def _contradiction(item: Assessed, report: SourceReport) -> str | None:
  """Why a recorded contradiction keeps this value out of plots, if it does."""
  dimension = item.measurement.dimension
  finding = report.findings.get(dimension)
  if finding is None or not finding.contradictions:
    return None
  checks = report.contradiction_checks.get(dimension, ())
  complete = len(checks) == len(finding.contradictions) and all(
    len(side_checks) == len(contradiction.evidence)
    and all(check.result == VERIFIED for check in side_checks)
    for contradiction, side_checks in zip(finding.contradictions, checks)
  )
  if not complete:
    return (
      "the source records a contradiction in this dimension whose quotations could not all be "
      "verified, so no value from this dimension is plotted"
    )
  for contradiction in finding.contradictions:
    if any(_involved(item, side) for side in contradiction.evidence):
      return (
        "this value is part of a contradiction recorded in the source "
        f"(“{contradiction.description.rstrip('.')}”)"
      )
  return None


def _evidence_reasons(item: Assessed) -> list[str]:
  reasons = [reason for _code, reason in item.problems]
  reasons += [
    f"{FIELD_NAMES[name]} unknown ({item.measurement.fields[name].unknown.rstrip('.')})"
    for name in item.unknown
  ]
  return reasons


def _differences(item: Assessed, other: Assessed) -> list[str]:
  differences = []
  for name in PROTOCOL_FIELDS:
    mine, theirs = item.measurement.fields.get(name), other.measurement.fields.get(name)
    if mine is None or theirs is None or mine.label is None or theirs.label is None:
      continue
    if _label(mine.label) != _label(theirs.label):
      differences.append(f"{FIELD_NAMES[name]} differs (“{mine.label}” vs “{theirs.label}”)")
  return differences


def _alone(item: Assessed, eligible: list[Assessed], excluded: dict[tuple[str, int], str]) -> str:
  """Why a plottable value has no partner from another source."""
  blocked = sorted({
    other.source_id for other in eligible
    if other.source_id != item.source_id and _key(other) == _key(item)
    and (other.source_id, other.index) in excluded
  })
  if blocked:
    return (
      "Not compared: the other source with this exact combination "
      f"({', '.join(blocked)}) is excluded because of conflicting or contradicted values."
    )
  for other in eligible:
    if other.source_id != item.source_id and _benchmark(other) == _benchmark(item) and _key(other) != _key(item):
      differences = _differences(item, other)
      if differences:
        return (
          f"Not compared: {other.source_id} reports the same task, dataset, split and metric, "
          f"but the {'; the '.join(differences)}."
        )
  return "Not compared: no other source reports this exact combination with verified evidence."


def compare(reports: list[SourceReport]) -> Comparison:
  """Rows for every measurement, and the charts P1-P6 allow."""
  by_report = {report.source_id: assess(report) for report in reports}
  assessed = [item for report in reports for item in by_report[report.source_id]]
  rows = {(item.source_id, item.index): Row(item) for item in assessed}

  # P5 and P6 apply to every measurement, whatever its other problems.
  excluded: dict[tuple[str, int], str] = {}
  for report in reports:
    items = by_report[report.source_id]
    conflicts = _conflicts(items)
    for item in items:
      clauses = []
      if item.index in conflicts:
        involved = sorted([item, *conflicts[item.index]], key=lambda entry: entry.index)
        values = [entry.measurement.value_text for entry in involved]
        clauses.append(
          "this source reports conflicting values for the same combination "
          f"({', '.join(dict.fromkeys(values))})"
        )
      contradiction = _contradiction(item, report)
      if contradiction:
        clauses.append(contradiction)
      if clauses:
        excluded[(item.source_id, item.index)] = "; ".join(clauses)

  for item in assessed:
    reasons = _evidence_reasons(item)
    if (item.source_id, item.index) in excluded:
      reasons.append(excluded[(item.source_id, item.index)])
    if reasons:
      rows[(item.source_id, item.index)].reason = "Not compared: " + "; ".join(reasons) + "."

  eligible = [item for item in assessed if item.eligible]
  plottable = [item for item in eligible if (item.source_id, item.index) not in excluded]
  buckets: dict[tuple, list[Assessed]] = {}
  for item in plottable:
    buckets.setdefault(_key(item), []).append(item)

  charts: list[Chart] = []
  for members in buckets.values():
    by_source: dict[str, list[Assessed]] = {}
    for item in members:
      by_source.setdefault(item.source_id, []).append(item)
    if len(by_source) >= 2:
      first = members[0]
      chart = Chart(
        number=len(charts) + 1,
        labels={name: first.measurement.fields[name].label for name in first.fields_to_check()},
        unit=first.measurement.unit,
        metric_kind=first.measurement.metric_kind,
      )
      for source_id, items in by_source.items():
        # Identical values only: P5 removed every source with differing ones.
        chart.points.append(Point(source_id, items[0].number, items[0]))
        for item in items:
          rows[(item.source_id, item.index)].chart = chart.number
      charts.append(chart)
      continue
    for item in members:
      rows[(item.source_id, item.index)].reason = _alone(item, eligible, excluded)

  return Comparison([rows[(item.source_id, item.index)] for item in assessed], charts)
