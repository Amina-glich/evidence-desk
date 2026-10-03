"""Project builder: render the evidence comparison into PROJECT_OUTPUT_DIR.

Möbius invokes ``build.sh`` for the project's "Evidence comparison"
Creation. The project is opened with the same symlink-safe file layer as the
service, the same scope and refusal rules as the CSV export
(``export.select_reports``), and every quotation is checked against the
registered page text at build time. On a refusal the build exits non-zero
with the reason in the build log, and Möbius keeps the last good Creation.

The platform runs builders on its own Python without the app's environment,
so nothing imported here may need pypdf (see ``desk.sources``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desk.errors import DeskError  # noqa: E402
from desk.export import select_reports  # noqa: E402
from desk.project_fs import UUID_RE, open_project  # noqa: E402
from desk.viewer import render_html  # noqa: E402


OUTPUT_ENTRY = "index.html"


def project_location(project_root: str) -> tuple[Path, str]:
  """(data root, project id) from PROJECT_ROOT, which must be <data>/projects/<id>."""
  if not project_root or not os.path.isabs(project_root):
    raise DeskError("invalid_project", "PROJECT_ROOT is not an absolute path.")
  path = Path(os.path.normpath(project_root))
  if path.parent.name != "projects" or not UUID_RE.match(path.name):
    raise DeskError("invalid_project", "PROJECT_ROOT is not a Möbius project folder (<data>/projects/<id>).")
  return path.parent.parent, path.name


def build(env: Mapping[str, str]) -> tuple[str, int]:
  """The page and the number of sources it shows."""
  data_root, project_id = project_location(env.get("PROJECT_ROOT", ""))
  with open_project(data_root, project_id) as project:
    selection = select_reports(project)
  page = render_html(selection.reports, research_question=selection.research_question)
  return page, len(selection.reports)


def main(env: Mapping[str, str] | None = None) -> int:
  env = os.environ if env is None else env
  output = env.get("PROJECT_OUTPUT_DIR")
  if not output:
    print("PROJECT_OUTPUT_DIR is not set; Möbius runs this builder.", file=sys.stderr)
    return 2
  try:
    page, count = build(env)
  except DeskError as exc:
    print(f"Evidence Desk could not build the comparison view: {exc.message}", file=sys.stderr)
    return 1
  target = Path(output)
  target.mkdir(parents=True, exist_ok=True)
  (target / OUTPUT_ENTRY).write_bytes(page.encode("utf-8"))
  print(f"Built the evidence comparison for {count} source(s).")
  return 0


if __name__ == "__main__":
  sys.exit(main())
