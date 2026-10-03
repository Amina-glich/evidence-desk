"""The comparison Creation's builder, as far as it runs on every platform.

The end-to-end build against a real project folder is in test_workflow
(Linux only, like all project file access).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr

import build_view
from desk.errors import DeskError
from tests.support import REPO_ROOT, new_id


class BuilderEnvironmentTest(unittest.TestCase):

  def test_builder_needs_no_pypdf(self):
    # Möbius runs builders on its own Python, without the app's environment.
    code = (
      "import sys; sys.modules['pypdf'] = None; "
      "import runpy; runpy.run_path('build_view.py', run_name='imported_only'); "
      "print('ok')"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
      [sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
      capture_output=True, text=True, timeout=60, check=False,
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    self.assertEqual(completed.stdout.strip(), "ok")

  def test_build_script_runs_the_builder_without_writing_bytecode(self):
    script = (REPO_ROOT / "build.sh").read_text(encoding="utf-8")
    self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
    self.assertIn("set -euo pipefail", script)
    self.assertIn('export PYTHONDONTWRITEBYTECODE=1', script)
    self.assertIn('exec python3 "$(dirname "$0")/build_view.py"', script)


class ProjectLocationTest(unittest.TestCase):

  def test_project_root_must_be_a_project_folder(self):
    project_id = new_id()
    root = os.path.join(os.path.abspath(os.sep), "data", "projects", project_id)
    data_root, found = build_view.project_location(root)
    self.assertEqual((data_root.name, found), ("data", project_id))
    for bad in ("", "projects/" + project_id, os.path.join(os.path.abspath(os.sep), "data", "apps", project_id),
                os.path.join(os.path.abspath(os.sep), "data", "projects", "not-a-uuid")):
      with self.subTest(root=bad):
        with self.assertRaises(DeskError) as caught:
          build_view.project_location(bad)
        self.assertEqual(caught.exception.code, "invalid_project")

  def test_main_reports_problems_instead_of_writing(self):
    stderr = io.StringIO()
    with redirect_stderr(stderr):
      self.assertEqual(build_view.main({"PROJECT_ROOT": "/data/projects/x"}), 2)
      self.assertEqual(build_view.main({"PROJECT_ROOT": "relative", "PROJECT_OUTPUT_DIR": "unused"}), 1)
    self.assertIn("PROJECT_OUTPUT_DIR is not set", stderr.getvalue())
    self.assertIn("could not build the comparison view", stderr.getvalue())
    self.assertFalse(os.path.exists("unused"))


if __name__ == "__main__":
  unittest.main()
