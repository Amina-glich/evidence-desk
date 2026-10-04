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
