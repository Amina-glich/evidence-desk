"""Source-level checks for the in-app comparison screens (no browser needed)."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from tests.support import REPO_ROOT


class ComparisonScreensTest(unittest.TestCase):

  def setUp(self):
    self.index = (REPO_ROOT / "index.jsx").read_text(encoding="utf-8")
    self.workspace = (REPO_ROOT / "workspace.jsx").read_text(encoding="utf-8")

  def test_the_home_screen_lists_comparisons_and_creates_new_ones_first(self):
    self.assertIn("'New comparison'", self.index)
    self.assertIn("Open comparison", self.index)
    self.assertLess(self.index.index("New comparison"), self.index.index("Open comparison"))

  def test_the_project_launcher_is_not_part_of_the_screen(self):
    for gone in ("Existing Project workflow", "projects.list", "projects.create", "projects.open", "Open project"):
      self.assertNotIn(gone, self.index)
    # The screen uses app storage only: no Projects API and no Manage Projects button.
    self.assertEqual(set(re.findall(r"window\.mobius\??\.(\w+)", self.index)), {"storage"})
    self.assertNotIn("projects.", self.index)
    self.assertNotIn("Manage Projects", self.index)
    self.assertNotIn("Manage projects", self.index)
    self.assertNotIn("ed-manage", self.index)
    # The screen never calls storage.remove or fetch itself; deletion lives in comparisons.js.
    self.assertNotRegex(self.index.lower(), r"storage\.remove\(|fetch\(")

  def test_each_comparison_has_its_own_folder_and_chat(self):
    self.assertIn("workspaces/${row.id}/README.md", self.index)
    self.assertIn("persist: `chat_${wid}.json`", self.workspace)
    self.assertIn("workspaces/${wid}/inbox/", self.workspace)
    self.assertIn("key={open.id}", self.index)  # a fresh chat and file view per comparison

  def test_an_earlier_single_workspace_is_kept_as_the_first_comparison(self):
    logic = (REPO_ROOT / "comparisons.js").read_text(encoding="utf-8")
    self.assertIn("workspace.json", logic)
    self.assertIn("First comparison", logic)

  def test_the_run_step_is_short_and_the_full_request_stays_behind_copy(self):
    self.assertNotIn("{ANALYSIS_PROMPT}</p>", self.workspace)  # the long request is not printed on the page
    self.assertIn("writeText(ANALYSIS_PROMPT)", self.workspace)
    self.assertIn("Copy request", self.workspace)

  def test_the_page_is_a_centred_readable_column(self):
    for source in (self.index, self.workspace):
      self.assertRegex(source, r"max-width: 1000px")
    self.assertIn("@media (max-width: 560px)", self.workspace)
    self.assertNotRegex(self.workspace + self.index, r"overflow-x: ?(scroll|auto)|(?<!max-)(?<!min-)width: ?\d{4,}px")
    self.assertRegex(self.workspace, r"\.edw-chat \{ height: clamp\(240px")

  def test_the_summary_renders_markdown_not_raw_text(self):
    self.assertIn("renderMarkdown(synthesis)", self.workspace)
    self.assertNotIn("<pre", self.workspace)


class DeleteComparisonTest(unittest.TestCase):
  """Deleting a comparison: behaviour runs in Node against a fake in-memory storage."""

  def setUp(self):
    self.index = (REPO_ROOT / "index.jsx").read_text(encoding="utf-8")
    self.logic = (REPO_ROOT / "comparisons.js").read_text(encoding="utf-8")

  @unittest.skipUnless(shutil.which("node"), "needs Node to run the fake-storage tests")
  def test_deleting_one_comparison_preserves_the_others(self):
    result = subprocess.run(
      ["node", "--test", str(REPO_ROOT / "tests" / "js" / "delete_comparison.test.mjs")],
      capture_output=True, text=True, timeout=60,
    )
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("pass 5", result.stdout)

  def test_the_card_has_a_clearly_labelled_delete_and_a_confirmation_naming_it(self):
    self.assertIn("Delete comparison", self.index)
    self.assertIn("Delete “{row.name", self.index)
    self.assertIn("role=\"alertdialog\"", self.index)
    self.assertIn("Cancel", self.index)
    self.assertNotIn("Manage Projects", self.index)

  def test_deletion_only_uses_supported_storage_calls_on_this_comparisons_paths(self):
    self.assertEqual(sorted(set(re.findall(r"storage\.(\w+)\(", self.logic))),
                     ["get", "listWithStatus", "remove", "set"])
    self.assertNotIn("window.mobius", self.logic)
    self.assertNotIn("projects", self.logic)
    self.assertIn("`workspaces/${id}`", self.logic)
    self.assertIn("`chat_${id}.json`", self.logic)
    self.assertIn("complete !== true", self.logic)
    # The list entry goes last, so a failed delete can be retried.
    self.assertLess(self.logic.index("storage.remove(`chat_"), self.logic.rindex("storage.set(REGISTRY"))


if __name__ == "__main__":
    unittest.main()
