# Evidence Desk — notes for Claude Code

Möbius Project app for the Möbius Hackathon 2026, Private Pro Desk challenge.
A researcher supplies AI/ML papers; the agent records findings with
page-level evidence, quotations are checked against the source text, and the
app produces a research brief (`synthesis.md`) and a comparison CSV.

## Repositories

- This repository is the app. Work here.
- The Möbius platform is expected as a sibling checkout at `../mobius`.
  **Read only, never edit.** Useful references: `PROJECT-APPS.md`,
  `examples/project-apps/game-studio/`, `backend/app/manifest_contract.py`
  (manifest rules), `backend/app/app_services.py` (service protocol),
  `backend/app/app_tools.py` (tool calls), `backend/app/routes/projects.py`
  (project creation), `frontend/src/lib/appProjectControl.js` (launcher API),
  `backend/app/app_compile_contract.py` and `backend/scripts/validate-app.py`
  (JSX compiler and app validator).

## Product rules

- Six dimensions, ids in `desk/vocabulary.py`: task, data, validation,
  results, runtime, limitations.
- Statuses: `reported` (page + exact quote), `not_reported` (checked and
  absent, with what was checked), `not_assessed` (not checked; the default).
- The agent must never invent findings. Page = 1-based page index in the PDF.
- Guidance (`evidence-desk.md`), starter files (`templates/`) and
  `desk/vocabulary.py` must agree; `tests/test_manifest.py` enforces it.

## Architecture and security rules

- Tools never accept `project_id` or paths chosen for authority. The project
  comes only from the trusted `call.chat_id` via `desk/binding.py`, which
  reads the platform SQLite DB read-only (`chats`, `delegations`, `projects`;
  internal schema, verified before use) and fails closed. Requires
  `source_app_id == APP_ID` and `root_path == projects/<id>`.
- All project file access goes through `desk/project_fs.py` (no symlink
  following, Linux only; refuses with `unsupported_platform` elsewhere).
- Ownership by path: `inbox/` owner uploads (read only for service and
  agent); `sources/` and `exports/` service only; `evidence/`, `desk.json`,
  `synthesis.md` agent. Service writers hold `project_lock` (lock file lives
  in app storage, outside the project).
- Tool calls run concurrently and each request is a fresh process.
- Responses never contain stack traces or other projects' data; unexpected
  errors go to stderr only.
- Every `desk/*.py` must be listed in `mobius.json` `source_files` (tested).
- `mobius.json` `tools` must equal `desk.service.HANDLERS` (tested). Add a
  tool by: spec in `desk/tool_args.py`, handler, `HANDLERS`, manifest entry,
  guidance, tests.
- Starter files are copied once and never updated: keep version-specific
  statements out of `templates/`; put them in `evidence-desk.md`.
- No credentials, secrets, real papers or personal data anywhere in the repo,
  templates, tests or outputs. Test PDFs are generated in memory
  (`tests/pdf_fixtures.py`).

## Status (2026-10-02)

Done: binding, safe file layer, strict tool args, manifest, launcher
`index.jsx`, Paper comparison template, agent skill, service entry, and the
P0 tools: `project_status`, `add_source` (inbox PDF ->
`sources/S<n>/source.json` + `pages.json`, pypdf), `check_evidence`
(deterministic quote check), `export_comparison` (`exports/comparison.csv`).
The manifest passes the platform's `validate_manifest_contract`, and
`validate-app.py` passes with the exact Möbius compiler (`index.jsx`
compiles with Rolldown 1.2.11).

Not done (next stages): `add_source` by arXiv id / DOI (network), the
comparison viewer Creation, Semantic Scholar suggestions.

Never verified in a live Möbius instance: install and Apply (environment
build from the lock, which needs PyPI access), live binding against the real
database, tool calls from a real agent, launcher rendering, and pypdf quality
on real two-column papers.

## Platform direction

A Möbius MCP connector is **not** required (organizer decision, 2026-10-02).
The goal is a Möbius Project app inspired by Claude for Science (Anthropic's
Claude Science workbench and Claude for Life Sciences connectors). External
scholarly APIs may be called directly from the app's own service.

Design consequences:

- Prefer keyless APIs with fixed, allowlisted hosts (arXiv, Crossref; maybe
  Semantic Scholar). Never fetch a model-supplied URL. OpenAlex now needs an
  API key and is billed by usage; app services cannot read app secrets with
  their app token, so keyed APIs are out of the MVP.
- Respect provider terms: arXiv allows one request every 3 s on a single
  connection for all our machines together.
- Paywalled full text is never fetched; the owner uploads those PDFs.
- Paper text is untrusted input: guidance must treat it as data, never as
  instructions.
- Tests stay offline: HTTP is injected and fed recorded fixtures.

MVP order: P0 `add_source` from `inbox/` PDFs, deterministic
`check_evidence`, `export_comparison` CSV (done); P1 arXiv/DOI metadata
lookup and a comparison viewer Creation (Project `artifact_types` HTML
builder); P2 Semantic Scholar suggestions, reviewer helper pass.

## Python dependencies

`requirements.in` lists top-level packages; `requirements.lock` is the
`pip-compile --generate-hashes` output Möbius installs (wheels only, hash
checked, no platform packages). Regenerate for the Möbius image's Python:

```bash
docker run --rm -v "$PWD":/lock -w /lock python:3.12-slim-trixie sh -c \
  "pip install -q pip-tools && pip-compile -q --generate-hashes --strip-extras \
   --no-emit-index-url --output-file=requirements.lock requirements.in"
```

`desk/pdf_text.py` imports pypdf at module level so the Apply smoke run
(imports `service.py` without `__main__`) catches a broken environment.

## Tests

Tests import pypdf: run them in a virtual environment built from the lock
(`pip install --only-binary=:all: --require-hashes --no-deps -r requirements.lock`).
On Windows the Linux-only tests are skipped. Full run, same Python as the
Möbius image (from Git Bash on Windows, use `"$(pwd -W)"` for the mount and
set `MSYS_NO_PATHCONV=1`):

```bash
docker run --rm -v "$PWD":/work:ro -w /work -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.12-slim-trixie sh -c "pip install -q --only-binary=:all: \
  --require-hashes --no-deps -r requirements.lock && \
  python -m unittest discover -s tests -t . -v"
```

## Validating the app with the Möbius compiler

Reproduces the production image layout (Node from `node:24-trixie-slim`,
`npm ci --ignore-scripts` into `/app/shell-src`) in a throwaway volume; both
repositories stay read-only. Run from the directory containing both checkouts:

```bash
docker volume create ed-compile-check
docker run --rm -v "$PWD/mobius":/mobius:ro -v ed-compile-check:/app \
  node:24-trixie-slim sh -c "mkdir -p /app/shell-src /app/bin && \
  cp /mobius/frontend/package.json /mobius/frontend/package-lock.json /app/shell-src/ && \
  cd /app/shell-src && npm ci --ignore-scripts && cp /usr/local/bin/node /app/bin/node"
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 \
  -e PATH=/app/bin:/usr/local/bin:/usr/bin:/bin -v ed-compile-check:/app \
  -v "$PWD/mobius":/mobius:ro -v "$PWD/evidence-desk":/desk:ro \
  python:3.12-slim-trixie python /mobius/backend/scripts/validate-app.py /desk
docker volume rm ed-compile-check
```

## Conventions

- Report test results exactly, including skipped tests and why they skip.
- Code, identifiers and repository documentation are in English.
