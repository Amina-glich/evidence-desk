"""The citation-linked evidence view: one self-contained HTML page.

It shows the same checked reports as the CSV export, as an evidence matrix:
an overview grid of statuses per dimension and source, then each dimension's
findings side by side, then every quotation with its source, PDF page and
check result. Each citation links to its quotation and back.

The view deliberately draws no chart of values across papers. Evidence Desk
cannot verify that metrics, datasets and experimental settings are
comparable, and a chart would imply that they are; the page says so. Status
colors always come with text, and nothing is resolved or ranked: notes,
checked absences and contradictions (with every side quoted and checked) are
shown as recorded.

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

from html import escape

from desk.evidence import RESULT_LABELS, VERIFIED, Finding, Quote, QuoteCheck, SourceReport, status_of
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
    # source id -> [(anchor, back anchor, dimension label, role, quote, check)]
    self.by_source: dict[str, list[tuple[str, str, str, str, Quote, QuoteCheck]]] = {}

  def add(self, source_id: str, dimension: str, label: str, role: str, quote: Quote, check: QuoteCheck) -> str:
    entries = self.by_source.setdefault(source_id, [])
    anchor = f"q-{source_id}-{len(entries) + 1}"
    entries.append((anchor, f"f-{dimension}-{source_id}", label, role, quote, check))
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
  title = _short(report.title, TITLE_SHORT_CHARS) if report.title else "Untitled source"
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
  checks = [check for dimension, _label in DIMENSIONS for check in report.all_checks(dimension)]
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

  appendix = []
  for report in reports:
    entries = citations.by_source.get(report.source_id, [])
    items = "".join(
      f'<li id="{anchor}"><span class="muted">Page {quote.page} · {_e(label)} · {role}</span> · '
      f'<span class="{"check-ok" if check.result == VERIFIED else "check-bad"}">{_e(_check_text(check))}</span>'
      f"<blockquote>{_e(quote.quote)}</blockquote>"
      f'<a href="#{back}">Back to the finding</a></li>'
      for anchor, back, label, role, quote, check in entries
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
    '<p class="notice" role="note"><strong>This is an evidence matrix, not a chart.</strong> '
    "Findings are placed side by side with their citations. Evidence Desk does not plot "
    "values across papers, because it cannot verify that metrics, datasets and experimental "
    "settings are comparable. Read each finding with its source, page and quotation.</p>"
    f'<ul class="sources">{"".join(_source_summary(report) for report in reports)}</ul>'
    "</header>"
    '<section aria-labelledby="h-overview"><h2 id="h-overview">Overview</h2>'
    f'<p class="muted">Status per dimension and source. Select a status to open the finding. {legend}</p>'
    f'<div class="scroll"><table class="overview"><thead><tr><th scope="col">Dimension</th>{head_cells}</tr></thead>'
    f"<tbody>{overview_rows}</tbody></table></div></section>"
    '<section aria-labelledby="h-findings"><h2 id="h-findings">Findings by dimension</h2>'
    f'{"".join(dimension_sections)}</section>'
    '<section aria-labelledby="h-quotes"><h2 id="h-quotes">Quotations</h2>'
    '<p class="muted">Every quotation, with the position of its page in the PDF file and the result '
    "of checking it against the registered page text when this view was built.</p>"
    f'{"".join(appendix)}</section>'
    "<footer>Built by Evidence Desk from the project's evidence files. Statuses: "
    "Reported = supported by a page and exact quotation; Not reported = checked in the "
    "available source material and absent; Not assessed = not checked yet.</footer>"
    f"</main><script>{NAV_SCRIPT}</script></body></html>\n"
  )
