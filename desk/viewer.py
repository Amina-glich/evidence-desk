"""The citation-linked evidence view: one self-contained HTML page.

It shows the same checked reports as the CSV export, as an evidence matrix:
an overview grid of statuses per dimension and source, then each dimension's
findings side by side, then the reported measurements, then every quotation
with its source, PDF page and check result. Each citation links to its
quotation and back.

"Reported measurements" lists every structured measurement with its value,
compatibility fields and status. Each value and each field links to the
quotation that backs it. A dot plot appears only for values that
``measurements.compare`` allows to be plotted together (same task, dataset,
split and metric, and hardware for speed or cost, all quote-backed, from at
least two sources, without conflicting values); metric definitions and model
or training variants may differ and label each point separately. Such a chart
is descriptive and never a head-to-head ranking. Every other value says why
it was not compared. Plots show values
as written, in source order, and never rank them. Status colors always come
with text, and nothing is resolved: notes, checked absences and
contradictions (with every side quoted and checked) are shown as recorded.

The page loads nothing remote, so it works as a sandboxed Möbius Creation,
in the Möbius file preview, and as a downloaded file. Its one script,
``NAV_SCRIPT``, is a fixed constant that never contains evidence: Möbius
shows the page as an ``srcdoc`` frame, whose base URL is the Möbius page
itself, so a plain ``href="#..."`` link would navigate the frame to Möbius
instead of scrolling. The script keeps such clicks inside the page. Without
scripts the links remain ordinary anchors, which work in a downloaded file.

Every value from evidence files, page text or PDF metadata is HTML-escaped;
anchor ids are built only from source ids, dimension ids and counters.
Output is deterministic.
"""

from __future__ import annotations

import re
from html import escape

from desk.evidence import (
  HARDWARE_FIELD, RESULT_LABELS, VERIFIED, Finding, Quote, QuoteCheck, SourceReport, file_name, status_of,
)
from desk.measurements import FIELD_NAMES, Assessed, Chart, Comparison, Row, compare
from desk.vocabulary import DIMENSIONS, STATUSES


STATUS_LABELS = dict(STATUSES)
TITLE_SHORT_CHARS = 80

_CSS = """
:root {
  color-scheme: light dark;
  --ed-text: var(--text, #1d2330);
  --ed-muted: var(--muted, #5b6474);
  --ed-bg: var(--bg, #ffffff);
  --ed-surface: var(--surface, #f5f6f8);
  --ed-border: var(--border, #d6dae1);
  --ed-accent: var(--accent, #2f5fd0);
  --ed-ok: #1d7a46;
  --ed-ok-bg: #e3f4ea;
  --ed-warn: #8a5a00;
  --ed-warn-bg: #fff3d6;
  --ed-bad: #b42318;
  --ed-bad-bg: #fde8e6;
  --ed-none-bg: #eceef2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ed-text: var(--text, #e6e9ef);
    --ed-muted: var(--muted, #a3abb9);
    --ed-bg: var(--bg, #14171c);
    --ed-surface: var(--surface, #1d2129);
    --ed-border: var(--border, #343a46);
    --ed-accent: var(--accent, #8fb0ff);
    --ed-ok: #7fd6a2;
    --ed-ok-bg: #173826;
    --ed-warn: #f2c86b;
    --ed-warn-bg: #3a2e12;
    --ed-bad: #ff9a8f;
    --ed-bad-bg: #401b18;
    --ed-none-bg: #2a2f38;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--ed-bg); color: var(--ed-text);
  font: 15px/1.5 var(--font, system-ui, -apple-system, "Segoe UI", sans-serif); }
main { max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 8px; }
h2 { font-size: 1.25rem; margin: 36px 0 12px; }
h3 { font-size: 1.05rem; margin: 24px 0 10px; }
a { color: var(--ed-accent); }
.muted { color: var(--ed-muted); }
.notice { border: 1px solid var(--ed-border); border-left: 4px solid var(--ed-accent);
  background: var(--ed-surface); padding: 10px 14px; border-radius: 6px; }
.sources { padding-left: 20px; }
.scroll { overflow-x: auto; }
table.overview { border-collapse: collapse; min-width: 100%; }
.overview th, .overview td { border: 1px solid var(--ed-border); padding: 8px; text-align: left; vertical-align: top; }
.overview thead th { background: var(--ed-surface); }
.status { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; border: 1px solid transparent; white-space: nowrap; }
.status-reported { background: var(--ed-ok-bg); color: var(--ed-ok); }
.status-not_reported { background: var(--ed-warn-bg); color: var(--ed-warn); }
.status-not_assessed { background: var(--ed-none-bg); color: var(--ed-muted); border-style: dashed; border-color: var(--ed-border); }
.flags { display: block; margin-top: 4px; font-size: 0.8rem; color: var(--ed-muted); }
.flag-bad { color: var(--ed-bad); font-weight: 600; }
.flag-warn { color: var(--ed-warn); font-weight: 600; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.card { border: 1px solid var(--ed-border); border-radius: 8px; padding: 12px 14px; background: var(--ed-bg); }
.card:target, li:target, .card.is-target, li.is-target { outline: 2px solid var(--ed-accent); outline-offset: 2px; }
.card h4 { margin: 0 0 8px; font-size: 0.95rem; display: flex; gap: 8px; justify-content: space-between; align-items: baseline; }
.value { margin: 6px 0; }
.cite { white-space: nowrap; font-size: 0.85rem; }
.cite-bad { color: var(--ed-bad); font-weight: 600; }
.box { margin-top: 10px; padding: 8px 10px; border-radius: 6px; background: var(--ed-surface); font-size: 0.9rem; }
.box-warn { background: var(--ed-warn-bg); }
.box ul { margin: 4px 0 0; padding-left: 18px; }
blockquote { margin: 6px 0; padding: 6px 12px; border-left: 3px solid var(--ed-border); background: var(--ed-surface); }
.quotes { padding-left: 22px; }
.quotes li { margin-bottom: 14px; }
.check-ok { color: var(--ed-ok); font-weight: 600; }
.check-bad { color: var(--ed-bad); font-weight: 600; }
footer { margin-top: 40px; font-size: 0.85rem; color: var(--ed-muted); }
table.measures { border-collapse: collapse; min-width: 100%; font-size: 0.88rem; }
.measures th, .measures td { border: 1px solid var(--ed-border); padding: 6px 8px; text-align: left; vertical-align: top; }
.measures thead th { background: var(--ed-surface); }
.plain { margin: 4px 0 0; padding-left: 18px; color: var(--ed-muted); }
.measures p.plain { padding-left: 0; }
.notice.plain { margin: 12px 0; }
.measures tr.is-target { outline: 2px solid var(--ed-accent); outline-offset: -2px; }
.as-written { display: block; font-size: 0.8rem; color: var(--ed-muted); }
.plotted { color: var(--ed-ok); font-weight: 600; }
.chart { margin: 16px 0 24px; padding: 12px 14px; border: 1px solid var(--ed-border); border-radius: 8px; }
.chart.is-target { outline: 2px solid var(--ed-accent); outline-offset: 2px; }
.chart figcaption { margin-bottom: 8px; }
.chart svg { width: 100%; min-width: 480px; height: auto; max-width: 720px; display: block; }
.chart .axis { stroke: var(--ed-border); stroke-width: 1; }
.chart .guide { stroke: var(--ed-border); stroke-width: 1; stroke-dasharray: 2 4; }
.chart .dot { fill: var(--ed-accent); }
.chart text { fill: var(--ed-text); font-size: 13px; font-family: inherit; }
.chart text.tick { fill: var(--ed-muted); font-size: 12px; }
"""


# In-page navigation for an srcdoc frame (see the module docstring). It only
# moves within this document: no network, storage, history or parent access.
NAV_SCRIPT = """
document.addEventListener("click", function (event) {
  var link = event.target.closest ? event.target.closest('a[href^="#"]') : null;
  if (!link) return;
  var target = document.getElementById(link.getAttribute("href").slice(1));
  if (!target) return;
  event.preventDefault();
  var previous = document.querySelector(".is-target");
  if (previous) previous.classList.remove("is-target");
  target.classList.add("is-target");
  if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
  target.scrollIntoView({ block: "start" });
  target.focus({ preventScroll: true });
});
"""


def _e(text: object) -> str:
  return escape(str(text), quote=True)


def _short(text: str, limit: int) -> str:
  return text if len(text) <= limit else text[:limit - 1] + "…"


class _Citations:
  """Assigns each quotation an anchor and remembers it for the appendix."""

  def __init__(self) -> None:
    # source id -> [(anchor, back anchor, back text, dimension label, role, quote, check)]
    self.by_source: dict[str, list[tuple[str, str, str, str, str, Quote, QuoteCheck]]] = {}

  def add(
    self, source_id: str, dimension: str, label: str, role: str, quote: Quote, check: QuoteCheck,
    *, back: str | None = None, back_text: str = "Back to the finding",
  ) -> str:
    entries = self.by_source.setdefault(source_id, [])
    anchor = f"q-{source_id}-{len(entries) + 1}"
    entries.append((anchor, back or f"f-{dimension}-{source_id}", back_text, label, role, quote, check))
    return anchor


def _check_text(check: QuoteCheck) -> str:
  if check.result == VERIFIED:
    return "verified" if check.match == "exact" else "verified after normalizing PDF text"
  text = "check failed: " + RESULT_LABELS.get(check.result, check.result)
  if check.found_on:
    text += " (found on p." + ", p.".join(map(str, check.found_on[:5])) + ")"
  return text


def _cite(source_id: str, anchor: str, check: QuoteCheck) -> str:
  ok = check.result == VERIFIED
  mark = "verified" if ok else "check failed"
  css = "cite" if ok else "cite cite-bad"
  return (
    f'<a class="{css}" href="#{anchor}">[{_e(source_id)} p.{check.page}, {mark}]</a>'
  )


def _status(status: str, href: str | None = None) -> str:
  label = _e(STATUS_LABELS[status])
  if href:
    return f'<a class="status status-{status}" href="{href}">{label}</a>'
  return f'<span class="status status-{status}">{label}</span>'


def _source_name(report: SourceReport) -> str:
  name = report.title or file_name(report.file)
  title = _short(name, TITLE_SHORT_CHARS) if name else "Untitled source"
  return f"{_e(report.source_id)} · {_e(title)}"


def _flags(report: SourceReport, dimension: str) -> str:
  finding = report.findings.get(dimension)
  flags = []
  failed = sum(check.result != VERIFIED for check in report.all_checks(dimension))
  if failed:
    flags.append(f'<span class="flag-bad">{failed} failed check{"s" if failed != 1 else ""}</span>')
  if finding is not None and finding.contradictions:
    count = len(finding.contradictions)
    flags.append(f'<span class="flag-warn">{count} contradiction{"s" if count != 1 else ""}</span>')
  if finding is not None and finding.absences:
    count = len(finding.absences)
    flags.append(f"{count} checked absence{'s' if count != 1 else ''}")
  if finding is not None and finding.note:
    flags.append("note")
  return f'<span class="flags">{" · ".join(flags)}</span>' if flags else ""


def _card(report: SourceReport, dimension: str, label: str, citations: _Citations) -> str:
  source_id = report.source_id
  status = status_of(report, dimension)
  finding: Finding | None = report.findings.get(dimension)
  parts = [
    f'<article class="card" id="f-{dimension}-{source_id}">',
    f"<h4><span>{_source_name(report)}</span>{_status(status)}</h4>",
  ]
  if status == "reported" and finding is not None:
    parts.append(f'<p class="value">{_e(finding.value)}</p>')
    cites = [
      _cite(source_id, citations.add(source_id, dimension, label, "evidence", quote, check), check)
      for quote, check in zip(finding.evidence, report.checks.get(dimension, ()))
    ]
    parts.append(f'<p>Evidence: {" ".join(cites)}</p>')
  elif status == "not_reported" and finding is not None:
    parts.append(f'<p class="value">Checked and not found. Searched: {_e(finding.checked)}</p>')
  elif report.evidence == "missing":
    parts.append('<p class="muted">Not checked yet: this source has no evidence file.</p>')
  else:
    parts.append('<p class="muted">Not checked yet.</p>')
  if finding is not None and finding.note:
    parts.append(f'<div class="box"><strong>Note:</strong> {_e(finding.note)}</div>')
  if finding is not None and finding.absences:
    items = "".join(
      f"<li>{_e(absence.item)} <span class=\"muted\">(searched: {_e(absence.checked)})</span></li>"
      for absence in finding.absences
    )
    parts.append(f'<div class="box"><strong>Checked and not found within this dimension</strong><ul>{items}</ul></div>')
  if finding is not None and finding.contradictions:
    for contradiction, checks in zip(finding.contradictions, report.contradiction_checks.get(dimension, ())):
      sides = "".join(
        "<li>"
        + _cite(source_id, citations.add(source_id, dimension, label, "contradiction", quote, check), check)
        + f" “{_e(quote.quote)}”</li>"
        for quote, check in zip(contradiction.evidence, checks)
      )
      parts.append(
        '<div class="box box-warn"><strong>Contradiction in the source (not resolved):</strong> '
        f"{_e(contradiction.description)}<ul>{sides}</ul></div>"
      )
  parts.append("</article>")
  return "".join(parts)


def _source_summary(report: SourceReport) -> str:
  checks = report.quote_checks()
  verified = sum(check.result == VERIFIED for check in checks)
  details = []
  if report.file:
    details.append(f"<code>{_e(report.file)}</code>")
  if report.evidence == "missing":
    details.append("no evidence file yet")
  elif checks:
    css = "check-ok" if verified == len(checks) else "check-bad"
    details.append(f'<span class="{css}">{verified} of {len(checks)} quotations verified</span>')
  else:
    details.append("no quotations")
  if report.source_text != "ok":
    details.append(f'<span class="check-bad">source text unavailable ({_e(report.source_text)})</span>')
  return f'<li><a href="#src-{report.source_id}">{_source_name(report)}</a> — {"; ".join(details)}</li>'


DIMENSION_LABELS = dict(DIMENSIONS)
PLOT_RULE = (
  "A value is plotted only when at least two sources report the same task, dataset, split and "
  "metric (and hardware for speed or training cost), every one backed by a verified quotation, "
  "stating its metric definition and model variant, no source reports conflicting values for "
  "that combination, and the value is not part of a contradiction recorded in the source. "
  "Metric definitions and model or training variants may differ: each is labelled beside its own point."
)


def _row_anchor(item: Assessed) -> str:
  return f"m-{item.source_id}-{item.index + 1}"


class _MeasurementCitations:
  """Registers every quotation of every measurement once, before rendering."""

  def __init__(self, comparison: Comparison, citations: _Citations) -> None:
    self.value: dict[str, list[tuple[str, QuoteCheck]]] = {}
    self.fields: dict[str, dict[str, tuple[str, QuoteCheck]]] = {}
    for row in comparison.rows:
      item = row.assessed
      anchor = _row_anchor(item)
      label = DIMENSION_LABELS[item.measurement.dimension]
      common = {"back": anchor, "back_text": "Back to the measurement"}
      self.value[anchor] = [
        (citations.add(item.source_id, item.measurement.dimension, label, "measurement value", quote, check, **common), check)
        for quote, check in zip(item.measurement.evidence, item.checks.value)
      ]
      self.fields[anchor] = {
        name: (
          citations.add(
            item.source_id, item.measurement.dimension, label, f"measurement {FIELD_NAMES[name]}",
            evidence.quote, item.checks.fields[name], **common,
          ),
          item.checks.fields[name],
        )
        for name, evidence in item.measurement.fields.items() if evidence.quote is not None
      }


def _field_cell(item: Assessed, name: str, cites: _MeasurementCitations) -> str:
  evidence = item.measurement.fields.get(name)
  if evidence is None:
    return '<span class="muted">—</span>'
  if evidence.unknown is not None:
    return f'<span class="check-bad">unknown</span><span class="as-written">{_e(evidence.unknown)}</span>'
  anchor = _row_anchor(item)
  own = cites.fields[anchor].get(name)
  links = [own] if own else cites.value[anchor]
  return (
    f"{_e(evidence.label)} " + " ".join(_cite(item.source_id, link, check) for link, check in links)
    + f'<span class="as-written">as written: “{_e(evidence.as_written)}”</span>'
  )


def _number_text(value: float) -> str:
  return f"{value:g}"


def value_with_unit(value_text: str, unit: str) -> str:
  """The value as written, with its unit unless the value already shows it."""
  return value_text if value_text.endswith(unit) else f"{value_text} {unit}"


def _chart_svg(chart: Chart, cites: _MeasurementCitations) -> str:
  width, left, right, row, top = 780, 250, 110, 50, 16
  values = [point.number for point in chart.points]
  low, high = min(values), max(values)
  span = high - low or (abs(high) * 0.1 or 1.0)
  lo, hi = low - 0.12 * span, high + 0.12 * span
  plot = width - left - right
  x = lambda value: left + (value - lo) / (hi - lo) * plot
  height = top + row * len(chart.points) + 34
  axis_y = top + row * len(chart.points) + 6
  parts = [
    f'<svg viewBox="0 0 {width} {height}" role="group" aria-labelledby="chart-{chart.number}-title">',
    f'<line class="axis" x1="{left}" y1="{axis_y}" x2="{width - right}" y2="{axis_y}"/>',
  ]
  for value in sorted({low, high}):
    tx = x(value)
    parts.append(f'<line class="axis" x1="{tx:.1f}" y1="{axis_y}" x2="{tx:.1f}" y2="{axis_y + 5}"/>')
    parts.append(f'<text class="tick" x="{tx:.1f}" y="{axis_y + 20}" text-anchor="middle">{_e(_number_text(value))}</text>')
  for position, point in enumerate(chart.points):
    cy = top + row * position + row / 2
    cx = x(point.number)
    # The verified quotation that shows this value, not merely the first one.
    anchor = cites.value[_row_anchor(point.assessed)][point.assessed.value_quote][0]
    value_text = point.assessed.measurement.value_text
    parts.append(f'<line class="guide" x1="{left}" y1="{cy:.1f}" x2="{width - right}" y2="{cy:.1f}"/>')
    parts.append(f'<text x="8" y="{cy - 8:.1f}">{_e(point.source_id)} · {_e(_short(point.variant, 34))}</text>')
    parts.append(f'<text class="tick" x="8" y="{cy + 8:.1f}">{_e(_short(point.definition, 40))}</text>')
    parts.append(
      f'<a href="#{anchor}"><title>{_e(point.source_id)} · {_e(point.variant)} · {_e(point.definition)}: {_e(value_with_unit(value_text, chart.unit))} — open the quotation</title>'
      f'<circle class="dot" cx="{cx:.1f}" cy="{cy:.1f}" r="6"/>'
      f'<text x="{cx + 10:.1f}" y="{cy + 4:.1f}">{_e(value_text)}</text></a>'
    )
  parts.append("</svg>")
  return "".join(parts)


def _chart(chart: Chart, cites: _MeasurementCitations) -> str:
  labels = chart.labels
  values = [point.number for point in chart.points]
  axis = (
    f"The horizontal axis covers only the plotted range ({_number_text(min(values))} to "
    f"{_number_text(max(values))} {_e(chart.unit)}), not zero, so small differences can look large."
  )
  shared = f"Shared hardware: {_e(labels[HARDWARE_FIELD])}. " if HARDWARE_FIELD in labels else ""
  definitions = {" ".join(point.definition.casefold().split()) for point in chart.points}
  differ = (
    "These points use different metric definitions, so they do not measure quite the same thing. "
    if len(definitions) > 1 else ""
  )
  return (
    f'<figure class="chart" id="chart-{chart.number}">'
    f'<figcaption><strong id="chart-{chart.number}-title">Chart {chart.number}: '
    f"{_e(labels['metric'])} ({_e(chart.unit)}) on {_e(labels['dataset'])}, {_e(labels['split'])} — "
    f"{_e(labels['task'])}</strong><br>"
    f'<span class="muted">{shared}Same recorded task, dataset, split, metric and unit. Each point is '
    "labelled with its own model or training variant and metric definition, which can differ between "
    f"points; settings may still differ (see the Setting column). {differ}Results with different "
    "evaluation setups are descriptive and must not be ranked as a head-to-head comparison. This is "
    f"not a controlled experiment, and values are not ranked. {axis} Select a point to open its quotation.</span>"
    f'</figcaption><div class="scroll">{_chart_svg(chart, cites)}</div></figure>'
  )


_DIFFERS = re.compile(r"(?:the |; the )(metric definition|model variant|hardware) differs \(“(.*?)” vs “(.*?)”\)")
_PARTNER = re.compile(r"\b(S\d+) reports the same task")

_PROBLEM_HELP = {
  "quote_not_verified": (
    "A quotation recorded for this value could not be found on the PDF page it cites, so the value is not trusted yet.",
    "Ask the agent to re-run the evidence check and correct the page or the quotation.",
  ),
  "value_not_in_quote": (
    "The number written in the table does not appear in its quotation.",
    "Ask the agent to copy the number exactly as the paper prints it, with its quotation.",
  ),
  "value_unparsed": (
    "The value is not a plain number, so it cannot be placed on a scale.",
    "It stays listed here with its citation; compare it by reading the quotation.",
  ),
  "relative_value": (
    "This is a difference or a ratio, not a value the paper measured itself.",
    "Record the paper's own measured value instead, if it reports one.",
  ),
  "percent_mismatch": (
    "The percent sign in the quotation and the unit recorded here disagree.",
    "Ask the agent to fix the unit or the value so they agree with the quotation.",
  ),
  "unit_mismatch": (
    "The unit recorded for this value does not match how the value is written.",
    "Ask the agent to fix the unit so it agrees with the value.",
  ),
  "hardware_missing": (
    "Speed and training cost depend on the hardware, and none is recorded.",
    "Check the paper for the hardware; if it names it, ask the agent to record it with a quotation.",
  ),
  "field_not_evidenced": (
    "A label recorded for this value (task, dataset, metric or similar) is not in the words of its quotation.",
    "Ask the agent to quote the passage that states it, or to correct the label.",
  ),
}


def _plain_help(row: Row) -> list[tuple[str, str]]:
  """Plain-English (what happened, what to do next) pairs for a value that is not plotted."""
  item, reason = row.assessed, row.reason or ""
  found: list[tuple[str, str]] = []
  for code, _text in item.problems:
    found.append(_PROBLEM_HELP.get(code.split(":")[0], _PROBLEM_HELP["quote_not_verified"]))
  for name in item.unknown:
    field_name = FIELD_NAMES[name]
    found.append((
      f"The paper does not say what the {field_name} is, or it was not found, and Evidence Desk never fills it in. "
      "Without it this value cannot be shown to measure the same thing as another paper's.",
      f"Look in the paper. If it states the {field_name}, ask the agent to record it with a quotation; otherwise this value stays unplotted.",
    ))
  if "conflicting values" in reason:
    found.append((
      "This paper gives different numbers for what looks like the same measurement, so none of them can be picked for a chart.",
      "Open the cited quotations and see how the paper explains them (for example different settings), then ask the agent to record the difference.",
    ))
  if "contradiction recorded" in reason or "contradiction in this dimension" in reason:
    found.append((
      "This paper contradicts itself about this value, as recorded in the findings.",
      "Read both quotations of the contradiction and decide yourself which one applies; the chart is not drawn for either.",
    ))
  differences = _DIFFERS.findall(reason)
  if differences:
    partner = (_PARTNER.search(reason) or [None, "another paper"])[1]
    parts = "; ".join(f"{kind}: this paper says “{mine}”, {partner} says “{theirs}”" for kind, mine, theirs in differences)
    found.append((
      f"{partner} reports the same task, dataset, split and metric, but under a different setup ({parts}). "
      "These are different experiments, so putting them on one scale would mislead.",
      "Compare them by reading the quotations. A chart appears only when two papers report the same setup.",
    ))
  if "is excluded because of conflicting or contradicted values" in reason:
    found.append((
      "The other paper's matching value is held back because that paper conflicts with itself.",
      "Resolve that paper's conflicting values first.",
    ))
  if "no other source reports this exact combination" in reason:
    found.append((
      "No other registered paper reports this same measurement with verified evidence, so there is nothing to compare it with.",
      "Upload another paper that reports it to inbox/ and register it, or search arXiv for one.",
    ))
  return list(dict.fromkeys(found))


def _status_cell(row: Row) -> str:
  help_items = _plain_help(row)
  if not help_items:
    return _e(row.reason)
  plain = "".join(f'<li>{_e(what)} <em>What you can do:</em> {_e(todo)}</li>' for what, todo in help_items)
  return f'{_e(row.reason)}<p class="plain"><strong>In plain English</strong></p><ul class="plain">{plain}</ul>'


def _no_chart_guide(rows: list[Row]) -> str:
  """A short, shared explanation of why nothing was plotted and the next steps."""
  steps = list(dict.fromkeys(todo for row in rows for _what, todo in _plain_help(row)))
  items = "".join(f"<li>{_e(step)}</li>" for step in steps)
  return (
    '<div class="notice plain" role="note"><p><strong>Why there is no chart.</strong> '
    "A chart is drawn only when two papers report the same measurement under the same conditions. "
    "Evidence Desk will not guess or adjust a value to make papers match, so every value is shown "
    "below with its paper and PDF page, and each row says in plain English what is different or missing.</p>"
    + (f"<p><strong>What you can do next</strong></p><ul>{items}</ul>" if items else "")
    + "</div>"
  )


def _measurements_section(reports: list[SourceReport], citations: _Citations) -> str:
  comparison = compare(reports)
  heading = '<section aria-labelledby="h-measures"><h2 id="h-measures">Reported measurements</h2>'
  if not comparison.rows:
    return heading + '<p class="muted">No structured measurements are recorded for these sources yet.</p></section>'
  cites = _MeasurementCitations(comparison, citations)
  if comparison.charts:
    summary = (
      f'<p class="notice" role="note"><strong>{len(comparison.charts)} '
      f'chart{"s" if len(comparison.charts) != 1 else ""} of {len(comparison.rows)} measurements.</strong> '
      f"{PLOT_RULE} Every other value is listed below with the reason it was not compared.</p>"
    )
  else:
    summary = (
      '<p class="notice" role="note"><strong>No values are plotted.</strong> '
      f"{PLOT_RULE} No recorded values meet all of these conditions; the Status column says why "
      "for each one.</p>"
    ) + _no_chart_guide(comparison.rows)
  charts = "".join(_chart(chart, cites) for chart in comparison.charts)
  body = []
  for row in comparison.rows:
    item = row.assessed
    anchor = _row_anchor(item)
    measurement = item.measurement
    value = (
      f"{_e(value_with_unit(measurement.value_text, measurement.unit))} "
      + " ".join(_cite(item.source_id, link, check) for link, check in cites.value[anchor])
    )
    if row.chart is not None:
      status = f'<a class="plotted" href="#chart-{row.chart}">Plotted in chart {row.chart}</a>'
    else:
      status = _status_cell(row)
    setting = _e(measurement.setting) if measurement.setting else '<span class="muted">—</span>'
    body.append(
      f'<tr id="{anchor}"><td>{_e(item.source_id)}</td><td>{value}</td>'
      f"<td>{_field_cell(item, 'metric', cites)}<br>{_field_cell(item, 'metric_definition', cites)}</td>"
      f"<td>{_field_cell(item, 'dataset', cites)}<br>{_field_cell(item, 'split', cites)}</td>"
      f"<td>{_field_cell(item, 'task', cites)}</td>"
      f"<td>{_field_cell(item, 'variant', cites)}<br>{_field_cell(item, HARDWARE_FIELD, cites)}</td>"
      f"<td>{setting}</td>"
      f"<td>{status}</td></tr>"
    )
  table = (
    '<div class="scroll"><table class="measures"><thead><tr>'
    '<th scope="col">Source</th><th scope="col">Value</th><th scope="col">Metric · definition</th>'
    '<th scope="col">Dataset · split</th><th scope="col">Task</th><th scope="col">Variant · hardware</th>'
    '<th scope="col">Setting</th><th scope="col">Status</th>'
    f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
  )
  return heading + summary + charts + table + "</section>"


def render_html(reports: list[SourceReport], *, research_question: str | None) -> str:
  """The complete page for the given checked reports, in their order."""
  citations = _Citations()
  dimension_sections = []
  for dimension, label in DIMENSIONS:
    cards = "".join(_card(report, dimension, label, citations) for report in reports)
    dimension_sections.append(
      f'<section id="d-{dimension}" aria-labelledby="h-{dimension}">'
      f'<h3 id="h-{dimension}">{_e(label)}</h3><div class="cards">{cards}</div></section>'
    )

  head_cells = "".join(f'<th scope="col">{_source_name(report)}</th>' for report in reports)
  overview_rows = "".join(
    f'<tr><th scope="row"><a href="#d-{dimension}">{_e(label)}</a></th>'
    + "".join(
      f"<td>{_status(status_of(report, dimension), f'#f-{dimension}-{report.source_id}')}"
      f"{_flags(report, dimension)}</td>"
      for report in reports
    )
    + "</tr>"
    for dimension, label in DIMENSIONS
  )
  measurements = _measurements_section(reports, citations)

  appendix = []
  for report in reports:
    entries = citations.by_source.get(report.source_id, [])
    items = "".join(
      f'<li id="{anchor}"><span class="muted">Page {quote.page} · {_e(label)} · {_e(role)}</span> · '
      f'<span class="{"check-ok" if check.result == VERIFIED else "check-bad"}">{_e(_check_text(check))}</span>'
      f"<blockquote>{_e(quote.quote)}</blockquote>"
      f'<a href="#{back}">{back_text}</a></li>'
      for anchor, back, back_text, label, role, quote, check in entries
    )
    file_line = f"<p class=\"muted\">File: <code>{_e(report.file)}</code></p>" if report.file else ""
    appendix.append(
      f'<section id="src-{report.source_id}"><h3>{_source_name(report)}</h3>{file_line}'
      + (f'<ol class="quotes">{items}</ol>' if items else '<p class="muted">No quotations recorded.</p>')
      + "</section>"
    )

  question = (
    f"<p><strong>Research question:</strong> {_e(research_question)}</p>"
    if research_question else '<p class="muted">Research question: not set in desk.json.</p>'
  )
  legend = " ".join(_status(status) for status, _label in STATUSES)
  return (
    "<!doctype html>\n"
    '<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="mobius-theme" content="inherit">'
    "<title>Evidence comparison</title>"
    f"<style>{_CSS}</style></head><body><main>"
    "<header><h1>Evidence comparison</h1>"
    f"{question}"
    '<p class="notice" role="note"><strong>This is an evidence matrix.</strong> '
    "Findings are placed side by side with their citations; read each one with its source, "
    "page and quotation. Values from different papers are plotted only under the strict "
    "conditions explained in Reported measurements, and even then the comparison is not a "
    "controlled experiment.</p>"
    f'<ul class="sources">{"".join(_source_summary(report) for report in reports)}</ul>'
    "</header>"
    '<section aria-labelledby="h-overview"><h2 id="h-overview">Overview</h2>'
    f'<p class="muted">Status per dimension and source. Select a status to open the finding. {legend}</p>'
    f'<div class="scroll"><table class="overview"><thead><tr><th scope="col">Dimension</th>{head_cells}</tr></thead>'
    f"<tbody>{overview_rows}</tbody></table></div></section>"
    '<section aria-labelledby="h-findings"><h2 id="h-findings">Findings by dimension</h2>'
    f'{"".join(dimension_sections)}</section>'
    f"{measurements}"
    '<section aria-labelledby="h-quotes"><h2 id="h-quotes">Quotations</h2>'
    '<p class="muted">Every quotation, with the position of its page in the PDF file and the result '
    "of checking it against the registered page text when this view was built.</p>"
    f'{"".join(appendix)}</section>'
    "<footer>Built by Evidence Desk from the project's evidence files. Statuses: "
    "Reported = supported by a page and exact quotation; Not reported = checked in the "
    "available source material and absent; Not assessed = not checked yet.</footer>"
    f"</main><script>{NAV_SCRIPT}</script></body></html>\n"
  )
