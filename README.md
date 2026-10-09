# Evidence Desk

A Möbius Project app for comparing AI/ML research papers with source-grounded
evidence: every extracted value points to a source, page and exact quote, and
missing information is labelled “Not reported” instead of guessed.

**Status: local code is preparation for 0.6.5; the hosted app is 0.6.4.** The app has a manifest, a launcher, a Project template
with agent guidance, a service with seven agent tools — `project_status`,
`add_source` (register an uploaded PDF and extract its page text with
pypdf), `check_evidence` (deterministic quotation checking),
`export_comparison` (comparison CSV and view), and arXiv discovery:
`search_literature`, `lookup_reference` and `save_reference` — and a
citation-linked **Evidence comparison** Creation. Version 0.4.1 was tested
end to end in a hosted Möbius instance, and 0.6.4 (plain-English no-chart
explanations, a compact `exports/comparison-by-dimension.csv`, shorter
plain-English summary guidance and paper titles) is the version the owner
reports installed and tested live there; that live test found 0.6.4 still
showed PDF file names instead of titles. The code in this repository is
`0.6.5` in `mobius.json`: it also reads a title from a source's stored
first-page text when the PDF has no usable embedded title, keeping the PDF
file name as the fallback. Nothing of 0.6.5 is committed, pushed or installed
yet, so none of it has been exercised in the hosted instance. Features added
after 0.4.1 were covered by the offline test suite; only what was exercised in
the hosted instance counts as live-verified. Publisher-DOI lookup (Crossref) is
not implemented yet.

## Literature discovery and the reference library

`search_literature` and `lookup_reference` read arXiv metadata;
`save_reference` stores one record the owner chose in
`library/references.json`, a service-owned area kept apart from `inbox/`,
`sources/`, `evidence/` and `exports/`. Records are discovery metadata,
never evidence, and no PDF is downloaded: the owner uploads the paper's PDF
to `inbox/` and registers it with `add_source` as before.

Only `https://export.arxiv.org/api/query` is contacted, with fixed
parameters, no redirects and no proxies; caller-supplied URLs are never
fetched. Requests are paced across processes (one at a time, at least
3 seconds apart, honouring `Retry-After`) by a lock in app storage. A saved
record is built from arXiv's response, not from tool arguments, with the
request, time and response SHA-256 as provenance; duplicates (same arXiv id
or publisher DOI) are not saved twice.

## Comparison view

An evidence matrix: an overview of Reported / Not reported / Not assessed
per dimension and source (with markers for notes, checked absences,
contradictions and failed checks), each dimension's findings side by side,
a **Reported measurements** section, and every quotation with its source,
PDF page and check result. Citations link to their quotation and back.

Reported measurements lists every structured measurement (optional, in the
evidence files) with its value, compatibility fields and a status: "Plotted
in chart N" or the reason it was not compared. Each value and field links
to the quotation that backs it. A dot plot is drawn only when at least two
sources report the same task, dataset, split, metric, metric definition and
model variant (and hardware for speed or training cost), every field backed
by a verified quotation, nothing unknown, no source reporting conflicting
values for that combination (checked across all of its measurements, an
unknown field counting as possibly the same), and no value taken from a
contradiction recorded in the same source and dimension
(`desk/measurements.py`, rules P1-P6 and evidence checks V1-V6, including
percent signs that must agree between unit and quotation). Each plotted
point links to the verified quotation that shows its value. Plots never
rank values and say they are not controlled experiments.

Each paper is named by its PDF's embedded title when that title appears at the top of page 1, otherwise by the title read from the top of the stored page-1 text (only when the title block is clearly delimited by an author, affiliation, abstract or blank line), otherwise by its PDF file name. The name is computed when the view is built, from stored data; registered sources, page text and evidence are never changed. It is a label, not evidence, and embedded metadata is never trusted on its own.

The page is self-contained (nothing remote) and every recorded text is
HTML-escaped.
Its one fixed script keeps citation links inside the page: Möbius previews
HTML in an `srcdoc` frame, where a plain `#` link would navigate the frame
to Möbius itself. Without scripts the links work as ordinary anchors.

It is produced two ways from the same renderer and the same checked
evidence: `export_comparison` writes `exports/comparison.html` in every
project, and projects created from the 0.4.0 template also get an
**Evidence comparison** Creation (built by `build.sh`). Möbius snapshots a
project's template when the project is created, so older projects keep using
the exported file.

## Package

| Path | Role |
|---|---|
| `mobius.json` | App manifest: service, agent tool, skill, and the **Paper comparison** project template. |
| `index.jsx` | Launcher: create, list and open this app's projects through `window.mobius.projects`; a short workflow guide with instructions for uploading PDFs to `inbox/` (no upload control: see below), a collapsed explanation of the project files; and three quick prompts (find papers, register PDFs and check evidence, export and review) whose text opens for editing, each with a Copy prompt button (`window.mobius.clipboard.writeText`, with a select-and-copy-manually fallback). It runs no tools and reads no project files. |
| `service.py` | Service entry the platform runs once per request (`json-v1`). Delegates to `desk.service`. |
| `build.sh`, `build_view.py` | Project builder of the Evidence comparison Creation. Runs on the platform's Python (no pypdf), with the same file safety and scope rules as the export. |
| `requirements.in`, `requirements.lock` | The service's Python dependency (pypdf), hash-pinned; Möbius builds the app's own environment from the lock (`"python": {"lock": ...}`). |
| `evidence-desk.md` | Agent skill: layout, ownership, workflow, evidence format, rules. |
| `templates/` | Starter files copied once into each new project. |

## Project layout

| Path | Written by |
|---|---|
| `inbox/` | Owner (PDF uploads). Service and agent only read it. |
| `sources/S<n>/` | Service only. |
| `exports/` | Service only. |
| `library/` | Service only (owner-selected arXiv references; not evidence). |
| `evidence/S<n>.json`, `desk.json`, `synthesis.md` | Agent. |

## Service core

| Module | Responsibility |
|---|---|
| `desk/service.py` | Parses one request, rejects anything that is not a declared tool, binds the call to its project, dispatches, and maps refusals to safe responses. |
| `desk/sources.py` | `add_source`: registers an `inbox/` PDF as `sources/S<n>/` (`source.json`, `pages.json`), deduplicated by SHA-256, published atomically under the project lock; `load_source` refuses page text whose digest changed. |
| `desk/pdf_text.py` | Bounded pypdf extraction; refuses non-PDF, damaged, encrypted, oversized and text-less (scanned) files. |
| `desk/evidence.py` | Strict validation of `evidence/S<n>.json` (findings plus optional notes, checked absences and quote-backed contradictions); `check_evidence` matches each quotation, including both sides of every contradiction, on its cited page after a fixed normalization of PDF artefacts. No fuzzy matching. |
| `desk/export.py` | `export_comparison`: `exports/comparison.html` (see `desk/viewer.py`) and a deterministic, formula-safe `exports/comparison.csv` with the service's own check per finding; the original 27 columns keep their positions and each finding's note, checked absences and contradictions follow in appended columns. `exports/comparison-by-dimension.csv` holds the same cells in 11 columns (one row per source and dimension) for narrow screens. Refuses a cell over 32,000 characters instead of letting spreadsheet software cut it. |
| `desk/arxiv.py` | Read-only arXiv client: identifier and query validation, fixed-host HTTPS transport without redirects or proxies, cross-process pacing, safe error mapping, and a strict Atom parser. |
| `desk/library.py` | The reference library and the `search_literature`, `lookup_reference` and `save_reference` tools. |
| `desk/measurements.py` | Pure comparability rules: evidence checks for measurements (number in its quotation, field words in theirs, unit, no differences or ratios, hardware for speed and cost), the compatibility key, and which values may share a plot. |
| `desk/viewer.py` | The comparison view: one self-contained, escaped, deterministic HTML page from checked reports. |
| `desk/status.py` | `project_status`: the bound project's name, id and area contents. Never lists other projects or follows symlinks. |
| `desk/binding.py` | Derives the one Project a tool call may act on from the platform-set `call.chat_id` (following helper chats to their project chat), never from model-written arguments. Requires a live project created by this app at `<data root>/projects/<id>`. Fails closed on any doubt. |
| `desk/project_fs.py` | Opens project paths one component at a time without following symlinks; confines service writes to `sources/` and `exports/`; atomic file replacement with revision checks; a per-project cross-process lock kept outside the project; atomic publishing of complete `sources/Sn` folders. Linux only. |
| `desk/tool_args.py` | Accepts exactly the declared tool arguments (no `project_id`), and refuses write tools for read-only callers. |
| `desk/vocabulary.py` | The six comparison dimensions and the three finding statuses. |
| `desk/errors.py` | `DeskError`, the single safe refusal type. |

### Why the project is read from the platform database

An app tool call carries a trusted chat id but no project id, and the
platform offers no app-token API that maps one to the other. `binding.py`
therefore reads the platform's SQLite database strictly read-only, checks that
every table and column it uses exists, and refuses the call otherwise. This
depends on an internal schema; a platform change makes the tools refuse
rather than act on the wrong project.

## Prompts and project actions

The launcher cannot call the app's tools: they run only for the agent in a
project chat. It therefore offers three prompts to copy, edit, paste into
the project's chat, review and send: **Find papers**, **Register PDFs and
check evidence** and **Export and review the comparison**. The same three
are Project template actions (buttons on a project's page that open an
editable draft), together with **Check project setup** and **Plan a
comparison**. A project snapshots its template when it is created, so only
projects created from 0.6.1 get the new buttons; the launcher's copy
buttons work for every project. `tests/test_manifest.py` keeps each launcher
prompt identical to its action and checks that no prompt weakens an
evidence rule.

## Adding papers and understanding the project files

Evidence Desk cannot offer an upload button. A launcher frame only has the
Projects runtime (`list`, `create`, `open`, `browse`, `templates`) and no API
to write project files, so the launcher, the starter `README.md`,
`inbox/README.md` and the agent guidance say what to do instead: open the
`inbox/` folder in the project's file list and choose **Upload** in the file
list toolbar. Möbius's Upload writes into the folder that is open, which is
why `inbox/` has to be open first.

The file list shows what the project template created and the tools wrote;
Möbius offers no way to hide or rename files. The starter files therefore
explain them: `inbox/` is for your PDFs, `synthesis.md` is the readable
research summary and comparison, and `README.md` and `desk.json` are support
files. The project guidance for new projects and the launcher say the same.

**Projects created before Evidence Desk 0.6.3 keep what they were created
with.** Möbius snapshots a project's template and starter files when the
project is created, so those projects keep their own `README.md`,
`inbox/README.md`, `synthesis.md`, suggested prompt buttons and template
guidance. Installing 0.6.3 does not update those existing project files, and
the app never overwrites an owner's files to refresh them. What does apply to
every project, old or new, is the launcher (including its upload
instructions, file explanation and copyable prompts) and the service and its
tools. To get the 0.6.3 starter files, create a new comparison; an existing
project can keep working as it is.

The **Changes** indicator with its line counts in the file pane belongs to
Möbius (its project file list), not to Evidence Desk, and the app cannot hide
it.

## Verifying Project binding in Möbius

Create a comparison from the launcher, open its chat and use the template
action **Check project setup** (or ask the agent to call `project_status`).
It reports the bound project's name and id, which should match the project
you are in. From a chat outside an Evidence Desk project the tool refuses.

## Tests

`unittest`, no network. The tests import pypdf, so run them in an
environment built from the lock:

```bash
python -m venv .venv && .venv/bin/pip install --only-binary=:all: --require-hashes --no-deps -r requirements.lock
.venv/bin/python -m unittest discover -s tests -t . -v
```

The file-safety, locking and status tests need Linux (`O_NOFOLLOW`, `dir_fd`,
`flock`) and are skipped elsewhere with that reason. To run everything in the
same Python as the Möbius image:

```bash
docker run --rm -v "$PWD":/work:ro -w /work -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.12-slim-trixie sh -c "pip install -q --only-binary=:all: --require-hashes --no-deps -r requirements.lock && python -m unittest discover -s tests -t . -v"
```
