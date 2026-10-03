# Evidence Desk

A Möbius Project app for comparing AI/ML research papers with source-grounded
evidence: every extracted value points to a source, page and exact quote, and
missing information is labelled “Not reported” instead of guessed.

**Status: 0.4.0.** The app has a manifest, a launcher, a Project template
with agent guidance, a service with four agent tools — `project_status`,
`add_source` (register an uploaded PDF and extract its page text with
pypdf), `check_evidence` (deterministic quotation checking) and
`export_comparison` (comparison CSV and view) — and a citation-linked
**Evidence comparison** Creation. Version 0.3.0 was tested end to end in a
hosted Möbius instance; the 0.4.0 comparison view has not been installed
yet. Online paper lookup (arXiv, DOI) is not implemented yet.

## Comparison view

An evidence matrix, not a chart: an overview of Reported / Not reported /
Not assessed per dimension and source (with markers for notes, checked
absences, contradictions and failed checks), each dimension's findings side
by side, and every quotation with its source, PDF page and check result.
Citations link to their quotation and back. It never plots values across
papers, because Evidence Desk cannot verify that metrics, datasets and
experimental settings are comparable, and it says so on the page. The page
is self-contained (no scripts, nothing remote) and every recorded text is
HTML-escaped.

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
| `index.jsx` | Launcher: create, list and open this app's projects through `window.mobius.projects`. |
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
| `evidence/S<n>.json`, `desk.json`, `synthesis.md` | Agent. |

## Service core

| Module | Responsibility |
|---|---|
| `desk/service.py` | Parses one request, rejects anything that is not a declared tool, binds the call to its project, dispatches, and maps refusals to safe responses. |
| `desk/sources.py` | `add_source`: registers an `inbox/` PDF as `sources/S<n>/` (`source.json`, `pages.json`), deduplicated by SHA-256, published atomically under the project lock; `load_source` refuses page text whose digest changed. |
| `desk/pdf_text.py` | Bounded pypdf extraction; refuses non-PDF, damaged, encrypted, oversized and text-less (scanned) files. |
| `desk/evidence.py` | Strict validation of `evidence/S<n>.json` (findings plus optional notes, checked absences and quote-backed contradictions); `check_evidence` matches each quotation, including both sides of every contradiction, on its cited page after a fixed normalization of PDF artefacts. No fuzzy matching. |
| `desk/export.py` | `export_comparison`: `exports/comparison.html` (see `desk/viewer.py`) and a deterministic, formula-safe `exports/comparison.csv` with the service's own check per finding; the original 27 columns keep their positions and each finding's note, checked absences and contradictions follow in appended columns. Refuses a cell over 32,000 characters instead of letting spreadsheet software cut it. |
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
