"""The app package: manifest, starter files and guidance stay consistent.

These mirror the Möbius manifest rules this app relies on (checked against
``backend/app/manifest_contract.py``) plus Evidence Desk's own invariants. They
are not a substitute for the platform's validator at install time.
"""

from __future__ import annotations

import json
import re
import unittest

from desk import SCHEMA_VERSION
from desk.project_fs import SERVICE_OWNED_ROOTS
from desk.service import HANDLERS
from desk.tool_args import TOOL_SPECS
from desk.vocabulary import DIMENSIONS, STATUSES
from tests.support import REPO_ROOT, TEMPLATE_ID, load_manifest, template


SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _keys(value):
  """Every object key anywhere in a JSON value."""
  if isinstance(value, dict):
    for key, item in value.items():
      yield key
      yield from _keys(item)
  elif isinstance(value, list):
    for item in value:
      yield from _keys(item)


class ManifestTest(unittest.TestCase):

  def setUp(self):
    self.manifest = load_manifest()
    self.sources = self.manifest["source_files"]

  def test_required_fields_and_entry(self):
    for field in ("id", "name", "version", "description", "entry"):
      self.assertTrue(self.manifest[field].strip(), field)
    self.assertRegex(self.manifest["id"], SLUG)
    self.assertEqual(self.manifest["entry"], "index.jsx")
    self.assertTrue((REPO_ROOT / "index.jsx").is_file())

  def test_every_source_file_exists_and_the_service_ships_whole(self):
    self.assertEqual(len(self.sources), len(set(self.sources)))
    for path in self.sources:
      with self.subTest(path=path):
        self.assertTrue((REPO_ROOT / path).is_file())
        self.assertNotIn(path, {"index.jsx", ".gitignore", "init-cron.sh"})
    shipped = {path for path in self.sources if path.startswith("desk/")}
    package = {f"desk/{path.name}" for path in (REPO_ROOT / "desk").glob("*.py")}
    self.assertEqual(shipped, package, "every desk module must be packaged")
    entry = self.manifest["service"]["entry"]
    self.assertEqual(entry, "service.py")
    self.assertIn(entry, self.sources)
    self.assertEqual(set(self.manifest["service"]), {"entry"})

  def test_python_dependencies_are_a_packaged_hash_pinned_lock(self):
    self.assertEqual(self.manifest["python"], {"lock": "requirements.lock"})
    self.assertIn("requirements.lock", self.sources)
    lock = (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+) \\$", lock, flags=re.MULTILINE)
    self.assertIn("pypdf", dict(pins))
    requirements = [
      line.split("#")[0].strip() for line in lock.splitlines()
      if line and not line.startswith((" ", "#"))
    ]
    self.assertEqual(len(requirements), len(pins), "every requirement is pinned with ==")
    for name, _version in pins:
      block = lock.split(f"{name}==", 1)[1].split("\n\n", 1)[0]
      self.assertRegex(block, r"--hash=sha256:[0-9a-f]{64}", name)
    wanted = {
      line.strip() for line in (REPO_ROOT / "requirements.in").read_text(encoding="utf-8").splitlines()
      if line.strip() and not line.startswith("#")
    }
    self.assertLessEqual(wanted, {name for name, _version in pins})

  def test_skills_are_packaged_root_markdown(self):
    for skill in self.manifest["skills"]:
      self.assertRegex(skill, r"^[a-z0-9][a-z0-9._-]*\.md$")
      self.assertIn(skill, self.sources)
    self.assertLessEqual(len(self.manifest["skills"]), 5)

  def test_declared_tools_are_exactly_the_implemented_ones(self):
    tools = self.manifest["tools"]
    names = [tool["name"] for tool in tools]
    self.assertEqual(set(names), set(HANDLERS))
    for tool in tools:
      with self.subTest(tool=tool["name"]):
        self.assertRegex(tool["name"], TOOL_NAME)
        self.assertLessEqual(set(tool), {"name", "description", "input_schema", "always_load", "result_independent"})
        self.assertLessEqual(len(tool["description"]), 2000)
        schema = tool["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertEqual(set(schema.get("properties", {})), TOOL_SPECS[tool["name"]].fields)
        self.assertNotIn("project_id", set(_keys(schema)))
        self.assertLessEqual(len(json.dumps(schema).encode()), 8 * 1024)

  def test_template_files_are_packaged_and_stay_out_of_service_areas(self):
    paper = template(self.manifest)
    self.assertRegex(paper["id"], SLUG)
    self.assertLessEqual(set(paper["skills"]), set(self.manifest["skills"]))
    for destination, source in paper["files"].items():
      with self.subTest(destination=destination):
        self.assertIn(source, self.sources)
        first = destination.split("/")[0]
        self.assertNotIn(first, SERVICE_OWNED_ROOTS)
        self.assertFalse(any(part.startswith(".") for part in destination.split("/")))
    self.assertIn("inbox/README.md", paper["files"])
    for action in paper["actions"]:
      self.assertRegex(action["id"], SLUG)
      self.assertLessEqual(len(action["prompt"]), 4000)

  def test_comparison_creation_is_declared_and_packaged(self):
    paper = template(self.manifest)
    (artifact_type,) = paper["artifact_types"]
    self.assertEqual(artifact_type, {
      "id": "comparison", "name": "Evidence comparison", "extensions": ["json"],
      "preview": "html", "script": "build.sh", "output": "index.html",
    })
    # Möbius runs the script from the installed source, so the builder and
    # everything it imports must be packaged.
    for path in ("build.sh", "build_view.py", "desk/viewer.py", "desk/export.py"):
      self.assertIn(path, self.sources)
    (preview,) = paper["previews"]
    self.assertEqual(preview["builder"], artifact_type["id"])
    # The preview registers at project creation only if its source exists then.
    self.assertIn(preview["source"], paper["files"])
    self.assertTrue(preview["source"].endswith(tuple(f".{ext}" for ext in artifact_type["extensions"])))

  def test_launcher_resolves_the_template_by_its_local_id(self):
    source = (REPO_ROOT / "index.jsx").read_text(encoding="utf-8")
    self.assertIn(f"const TEMPLATE_ID = '{TEMPLATE_ID}'", source)
    self.assertNotIn("evidence-desk:", source)


LAUNCHER_PROMPT = re.compile(
  r"\{\s*id: '(?P<id>[a-z-]+)',\s*title: '(?P<title>[^'\n]+)',\s*use: '(?P<use>[^'\n]+)',\s*text: '(?P<text>[^'\n]+)',\s*\}"
)


class LauncherTest(unittest.TestCase):
  """The launcher's copyable prompts and the template actions say the same, safely."""

  def setUp(self):
    self.source = (REPO_ROOT / "index.jsx").read_text(encoding="utf-8")
    self.prompts = {match["id"]: match for match in LAUNCHER_PROMPT.finditer(self.source)}
    self.actions = {action["id"]: action for action in template(load_manifest())["actions"]}

  def test_the_three_workflow_prompts_are_present(self):
    self.assertEqual(set(self.prompts), {"find-papers", "register-and-check", "export-review"})
    self.assertEqual(self.source.count("id: '"), len(self.prompts), "a prompt does not match the parsed shape")

  def test_each_prompt_is_the_template_action_with_the_same_id(self):
    for prompt_id, match in self.prompts.items():
      with self.subTest(prompt=prompt_id):
        self.assertIn(prompt_id, self.actions)
        self.assertEqual(match["text"], self.actions[prompt_id]["prompt"])
        self.assertEqual(match["title"], self.actions[prompt_id]["name"])

  def test_actions_stay_within_the_platform_limits(self):
    # manifest_contract.py: at most 8 actions, each prompt 1-4000 characters.
    self.assertLessEqual(len(self.actions), 8)
    for action_id, action in self.actions.items():
      with self.subTest(action=action_id):
        self.assertTrue(1 <= len(action["prompt"]) <= 4000)
        self.assertRegex(action_id, SLUG)
    for match in self.prompts.values():
      self.assertLessEqual(len(match["text"]), 700, "keep prompts concise")

  def test_prompts_keep_the_evidence_rules_and_name_only_real_tools(self):
    texts = {prompt_id: match["text"] for prompt_id, match in self.prompts.items()}
    for prompt_id, text in texts.items():
      with self.subTest(prompt=prompt_id):
        for tool in re.findall(r"\b(?:search_literature|save_reference|add_source|check_evidence|export_comparison|lookup_reference)\b", text):
          self.assertIn(tool, HANDLERS)
        for word in re.findall(r"\b[a-z]+_[a-z_]+\b", text):
          self.assertIn(word, HANDLERS, f"{word} is not an Evidence Desk tool")
    find, register, export = texts["find-papers"], texts["register-and-check"], texts["export-review"]
    self.assertIn("search_literature", find)
    self.assertIn("save_reference only for the papers I choose", find)
    self.assertIn("metadata, not evidence", find)
    self.assertIn("do not download PDFs", find)
    self.assertIn("[describe the topic here]", find)
    self.assertIn("add_source", register)
    self.assertIn("check_evidence", register)
    self.assertIn("Never invent findings", register)
    self.assertIn("never loosen a quotation", register)
    self.assertIn("run export_comparison", export)
    self.assertIn("only when every comparison requirement is met", export)
    # None of them asks the agent to weaken a rule or to chart or invent anything on its own.
    for text in texts.values():
      for forbidden in ("ignore", "skip the check", "estimate", "approximate", "assume"):
        self.assertNotIn(forbidden, text.lower())

  def test_the_launcher_says_it_does_not_run_tools_or_show_project_files(self):
    flat = " ".join(self.source.split())
    self.assertIn("does not run Evidence Desk tools, search for papers, read your PDFs or show project files", flat)
    self.assertIn("after you review and send a prompt", flat)
    self.assertIn("Nothing is sent from this page", flat)
    self.assertIn("paste it into the project chat, review it and send it", flat)

  def test_the_guide_covers_the_whole_workflow(self):
    flat = " ".join(self.source.split())
    for step in (
      "Create a comparison below, or open one you already have",
      "upload your PDFs to the inbox/ folder",
      "paste it into the project chat",
      "Evidence comparison view, or exports/comparison.html",
    ):
      self.assertIn(step, flat)
    self.assertIn("created from this version", flat)  # older projects keep their own template actions

  def test_copying_uses_the_documented_clipboard_and_falls_back_to_manual_copy(self):
    self.assertIn("window.mobius?.clipboard?.writeText?.(text)", self.source)
    self.assertIn("=== true", self.source)  # the runtime resolves to a boolean; anything else is a failure
    self.assertIn("area.current?.select()", self.source)
    self.assertIn("copy it manually", self.source)
    # The prompt is selectable text in every case, not only after a failed copy.
    self.assertRegex(self.source, r"<textarea\b")

  def test_the_launcher_uses_only_documented_platform_apis(self):
    # It must not reach tools, services or files: only the Projects runtime and the clipboard.
    for forbidden in ("fetch(", "XMLHttpRequest", "/api/", "/tools/", "window.mobius.chat", "mobius.storage", "postMessage", "dangerouslySetInnerHTML", "localStorage"):
      self.assertNotIn(forbidden, self.source)
    self.assertEqual(set(re.findall(r"window\.mobius\??\.(\w+)", self.source)), {"projects", "clipboard"})


class GuidanceTest(unittest.TestCase):
  """Starter files and guidance use the vocabulary the code uses."""

  def test_skill_measurement_example_is_valid_and_passes_every_check(self):
    from desk.measurements import compare
    from tests.reports import report_for
    from tests.test_measurements import PAPER_A

    text = (REPO_ROOT / "evidence-desk.md").read_text(encoding="utf-8")
    block = re.search(r'```json\n("measurements": \[\{\n  "dimension".*?)```', text, flags=re.DOTALL)
    self.assertIsNotNone(block)
    measurements = json.loads("{" + block.group(1) + "}")["measurements"]
    report = report_for({}, pages=PAPER_A, measurements=measurements)
    (row,) = compare([report]).rows
    self.assertEqual(row.assessed.problems, ())
    self.assertEqual(row.assessed.unknown, ())
    # Alone, it is reported but cannot be plotted (P1).
    self.assertIn("no other source", row.reason)

  def test_starter_desk_json_matches_the_data_format(self):
    data = json.loads((REPO_ROOT / "templates" / "desk.json").read_text(encoding="utf-8"))
    self.assertEqual(data, {"schema": SCHEMA_VERSION, "research_question": None, "compare_sources": []})

  def test_starter_brief_has_one_unassessed_section_per_dimension(self):
    text = (REPO_ROOT / "templates" / "synthesis.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    self.assertEqual(headings, [label for _id, label in DIMENSIONS])
    self.assertEqual(text.count("\nNot assessed.\n"), len(DIMENSIONS))

  def test_skill_defines_every_dimension_and_status(self):
    text = (REPO_ROOT / "evidence-desk.md").read_text(encoding="utf-8")
    for dimension, label in DIMENSIONS:
      self.assertIn(f"- `{dimension}`: {label}\n", text)
    for status, label in STATUSES:
      self.assertIn(f"- `{status}`: {label}.", text)
    self.assertIn("never try to pass one", text)
    for tool in load_manifest()["tools"]:
      self.assertIn(f"- `{tool['name']}`", text)

  def test_owner_readme_explains_every_status(self):
    text = (REPO_ROOT / "templates" / "project-README.md").read_text(encoding="utf-8")
    for _status, label in STATUSES:
      self.assertIn(f"**{label}**", text)


if __name__ == "__main__":
  unittest.main()
