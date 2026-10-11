"""Source-level checks for the in-app comparison screens (no browser needed)."""

from __future__ import annotations

import re
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
    # Projects appear only as a quiet, separate way to reach Möbius Projects.
    self.assertEqual(set(re.findall(r"window\.mobius\??\.(\w+)", self.index)), {"storage", "projects"})
    self.assertEqual(re.findall(r"projects\.(\w+)\(", self.index), ["browse"])
    manage = self.index.index('className="ed-manage"')
    self.assertGreater(manage, self.index.index('aria-labelledby="comparisons-title"'))
    self.assertIn("Manage Projects", self.index[manage:])
    self.assertNotRegex(self.index.lower(), r"delete\(|remove\(|fetch\(")

  def test_each_comparison_has_its_own_folder_and_chat(self):
    self.assertIn("workspaces/${row.id}/README.md", self.index)
    self.assertIn("persist: `chat_${wid}.json`", self.workspace)
    self.assertIn("workspaces/${wid}/inbox/", self.workspace)
    self.assertIn("key={open.id}", self.index)  # a fresh chat and file view per comparison

  def test_an_earlier_single_workspace_is_kept_as_the_first_comparison(self):
    self.assertIn("workspace.json", self.index)
    self.assertIn("First comparison", self.index)

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


if __name__ == "__main__":
    unittest.main()
