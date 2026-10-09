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
      "Create or open a comparison below",
      "open the inbox/ folder in the file list and choose Upload to add your PDFs",
      "Read synthesis.md (the research summary)",
      "paste it into the project chat, review it and send it",
      "or open the comparison view",
    ):
      self.assertIn(step, flat)
    self.assertIn("created from this version", flat)  # older projects keep their own template actions

  def test_prompt_editors_are_collapsed_until_the_owner_opens_them(self):
    self.assertIn("const [open, setOpen] = useState(false)", self.source)
    self.assertIn("<details open={open}", self.source)
    self.assertLess(self.source.index("<details open={open}"), self.source.index("<textarea"))
    self.assertIn("<summary>Edit prompt text</summary>", self.source)
    self.assertIn("setOpen(true)", self.source)  # a failed copy opens the editor so its text can be selected
    self.assertIn("Reset text", self.source)
    for prompt in self.prompts.values():
      self.assertLessEqual(len(prompt["use"]), 110, "keep each explanation to one short line")

  def test_every_prompt_has_a_distinctly_named_copy_button(self):
    self.assertIn("aria-label={`Copy prompt: ${prompt.title}`}", self.source)
    self.assertIn(">Copy prompt</button>", self.source)

  def test_styling_uses_only_mobius_theme_variables(self):
    css = re.search(r"const CSS = `(.*?)`", self.source, re.S).group(1)
    self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
    self.assertNotRegex(css, r"\b(?:rgb|rgba|hsl|hsla)\(")
    # The names the Möbius app frame defines for every theme (frontend/public/app-frame.html).
    theme = {"text", "muted", "border", "surface", "surface-2", "bg", "accent", "accent-hover", "accent-dim", "accent-fg", "danger", "font"}
    self.assertEqual(set(re.findall(r"var\(--([a-z0-9-]+)\)", css)) - theme, set())
    self.assertNotIn("@import", css)
    self.assertNotIn("url(", css)

  def test_there_is_no_upload_control_only_instructions_for_the_host_file_list(self):
    # A frame cannot write project files (window.mobius.projects has list/create/open only).
    for forbidden in ('type="file"', "type='file'", "FileReader", "arrayBuffer", "FormData", "webkitdirectory"):
      self.assertNotIn(forbidden, self.source)
    flat = " ".join(self.source.split())
    self.assertIn("open the inbox/ folder in the file list and choose Upload", flat)
    self.assertIn("this page cannot upload files for you", flat)

  def test_the_launcher_explains_the_project_files(self):
    flat = " ".join(self.source.split())
    self.assertIn("What are the files in a project?", flat)
    self.assertIn("<strong>synthesis.md</strong>: the readable research summary and comparison", flat)
    self.assertIn("<strong>README.md</strong> and <strong>desk.json</strong>: support files", flat)
    self.assertIn("<strong>inbox/</strong>: your papers", flat)
    self.assertIn("<details className=\"ed-files\">", self.source)  # collapsed: the page stays compact

  def test_quick_prompts_carry_a_purple_accent_from_the_theme(self):
    css = re.search(r"const CSS = `(.*?)`", self.source, re.S).group(1)
    quick = re.search(r"\.ed-quick \{([^}]*)\}", css).group(1)
    prompt = re.search(r"\.ed-prompt \{([^}]*)\}", css).group(1)
    self.assertIn("border-left: 3px solid var(--accent)", quick)
    self.assertIn("background: var(--accent-dim)", quick)
    self.assertIn("var(--accent)", quick)
    self.assertIn("color-mix(in srgb, var(--accent)", prompt)
    # A plain border comes first as a fallback for browsers without color-mix.
    self.assertLess(quick.index("border: 1px solid var(--border)"), quick.index("color-mix"))

  def test_copying_uses_the_documented_clipboard_and_falls_back_to_manual_copy(self):
    self.assertIn("window.mobius?.clipboard?.writeText?.(text)", self.source)
    self.assertIn("=== true", self.source)  # the runtime resolves to a boolean; anything else is a failure
    self.assertIn("area.current?.select()", self.source)
    self.assertIn("area.current?.focus()", self.source)
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

  def test_owner_readme_starts_with_how_to_add_papers_and_where_to_read_results(self):
    text = (REPO_ROOT / "templates" / "project-README.md").read_text(encoding="utf-8")
    start = text.index("## Start here")
    self.assertLess(start, text.index("## What you will see in the file list"))
    self.assertLess(start, text.index("## What the statuses mean"))
    steps = text[start:text.index("## What you will see")]
    self.assertIn("Open the `inbox/` folder in this project's file list", steps)
    self.assertIn("**Upload**", steps)
    self.assertIn("Read the result in `synthesis.md`", steps)
    self.assertIn("research summary", steps)

  def test_owner_readme_marks_support_files_and_where_uploads_go(self):
    text = (REPO_ROOT / "templates" / "project-README.md").read_text(encoding="utf-8")
    table = {line.split("|")[1].strip(" `"): line for line in text.splitlines() if line.startswith("| `")}
    self.assertIn("Upload them here", table["inbox/"])
    self.assertIn("research summary and comparison", table["synthesis.md"])
    for support in ("README.md", "desk.json"):
      self.assertIn("Support file", table[support])
    # Every folder and file the project can hold is explained, including the service-written ones.
    self.assertTrue({"inbox/", "synthesis.md", "exports/", "evidence/", "sources/", "library/", "README.md", "desk.json"} <= set(table))
    # Starter files are copied once and never updated: no version-specific statements.
    self.assertNotRegex(text, r"\b0\.\d+\.\d+\b")

  def test_inbox_readme_says_how_to_upload(self):
    text = (REPO_ROOT / "templates" / "inbox-README.md").read_text(encoding="utf-8")
    self.assertIn("**Upload**", text)
    self.assertIn("file list toolbar", text)
    self.assertIn("keep this `inbox/` folder open", text)
    self.assertIn("never change or delete them", text)
    self.assertNotRegex(text, r"\b0\.\d+\.\d+\b")

  def test_starter_brief_presents_itself_as_the_readable_summary(self):
    lines = (REPO_ROOT / "templates" / "synthesis.md").read_text(encoding="utf-8").splitlines()
    self.assertEqual(lines[0], "# Research brief")
    self.assertIn("readable research summary and comparison", " ".join(lines[1:4]))

  def test_agent_guidance_explains_uploads_and_support_files(self):
    skill = " ".join((REPO_ROOT / "evidence-desk.md").read_text(encoding="utf-8").split())
    self.assertIn("## Adding papers", skill)
    self.assertIn("you cannot upload files", skill)
    self.assertIn("chooses Upload in the file list toolbar", skill)
    self.assertIn("no PDFs in `inbox/`, tell the owner this instead of searching for papers", skill)
    self.assertIn("Call `synthesis.md` the research summary, and `desk.json` and `README.md` support files", skill)
    guidance = template(load_manifest())["guidance"]
    self.assertIn("opening inbox/ in the project file list and choosing Upload", guidance)
    self.assertIn("you cannot upload", guidance)
    self.assertIn("synthesis.md the research summary", guidance)
    self.assertIn("desk.json and README.md are support files", guidance)

  def test_owner_readme_explains_every_status(self):
    text = (REPO_ROOT / "templates" / "project-README.md").read_text(encoding="utf-8")
    for _status, label in STATUSES:
      self.assertIn(f"**{label}**", text)


if __name__ == "__main__":
  unittest.main()


class PlainSummaryGuidanceTest(unittest.TestCase):
  """The summary is asked to be short and plain, with citations and statuses kept."""

  def test_skill_asks_for_a_brief_plain_english_summary_that_keeps_the_evidence_rules(self):
    skill = " ".join((REPO_ROOT / "evidence-desk.md").read_text(encoding="utf-8").split())
    self.assertIn("Start with a short answer", skill)
    self.assertIn("Explain each technical term, abbreviation and metric in a few plain words the first time it appears", skill)
    self.assertIn("Never add a finding in an explanation", skill)
    self.assertIn("Brevity must not drop a qualification", skill)
    self.assertIn("`[S1 p.5]` citation", skill)
    self.assertIn("State Not reported and Not assessed explicitly", skill)

  def test_project_guidance_and_starter_brief_say_the_same(self):
    guidance = template(load_manifest())["guidance"]
    self.assertIn("explain each technical term in a few words the first time you use it", guidance)
    self.assertIn("keep every [S1 p.5] citation", guidance)
    brief = (REPO_ROOT / "templates" / "synthesis.md").read_text(encoding="utf-8")
    self.assertIn("plain-English", brief)
    self.assertIn("first time it appears", brief)
    self.assertNotRegex(brief, r"\b0\.\d+\.\d+\b")

  def test_the_export_tool_describes_the_narrow_csv(self):
    tool = next(tool for tool in load_manifest()["tools"] if tool["name"] == "export_comparison")
    self.assertIn("exports/comparison-by-dimension.csv", tool["description"])
    self.assertIn("comparison-by-dimension.csv", (REPO_ROOT / "evidence-desk.md").read_text(encoding="utf-8"))


class LauncherDeleteTest(unittest.TestCase):
  """Möbius gives apps no way to delete a project, so the launcher must not pretend to."""

  def setUp(self):
    self.source = (REPO_ROOT / "index.jsx").read_text(encoding="utf-8")

  def test_the_launcher_calls_only_the_documented_project_actions(self):
    import re
    calls = set(re.findall(r"runtime\.(\w+)\(", self.source))
    self.assertEqual(calls, {"list", "templates", "create", "open", "browse"})

  def test_there_is_no_delete_control_or_undocumented_request(self):
    lowered = self.source.lower()
    for forbidden in ("delete(", "remove(", "fetch(", "xmlhttprequest", "postmessage", "/api/", "localstorage", "sqlite"):
      self.assertNotIn(forbidden, lowered)
    self.assertNotRegex(self.source, r">\s*Delete")

  def test_the_page_explains_where_to_delete_and_opens_the_projects_directory(self):
    flat = " ".join(self.source.split())
    self.assertIn("Projects are deleted from Möbius Projects, where Möbius asks you to confirm before anything is removed", flat)
    self.assertIn("await runtime.browse()", self.source)
    self.assertIn("Open Möbius Projects", self.source)

  def test_the_guidance_is_a_separate_quiet_section_below_the_comparisons_card(self):
    source = self.source
    panel_end = source.index("</section>", source.index('className="ed-panel"'))
    manage = source.index('className="ed-manage"')
    self.assertGreater(manage, panel_end, "the guidance must not sit inside the comparisons card")
    self.assertNotIn("Open Möbius Projects", source[:panel_end])
    section = source[manage:source.index("</section>", manage)]
    self.assertIn('aria-labelledby="manage-title"', source[manage - 60:manage + 80])
    self.assertIn("<h2 id=\"manage-title\">Manage projects</h2>", section)
    self.assertIn("onClick={browse}", section)
    self.assertNotRegex(section, r"onClick=\{\s*(delete|remove)")

  def test_the_section_is_spaced_themed_and_stacks_on_narrow_screens(self):
    css = self.source[self.source.index("const CSS = `"):self.source.index("`", self.source.index("const CSS = `") + 14)]
    rule = next(line for line in css.splitlines() if line.startswith(".ed-manage {"))
    for part in ("margin-top: 28px", "border-top: 1px solid var(--border)", "flex-wrap: wrap", "justify-content: space-between"):
      self.assertIn(part, rule)
    quiet = [line for line in css.splitlines() if line.startswith((".ed-manage h2", ".ed-manage p"))]
    self.assertTrue(quiet and all("var(--muted)" in line for line in quiet))
    self.assertNotRegex(" ".join(line for line in css.splitlines() if "ed-manage" in line), r"#[0-9a-fA-F]{3,8}\b|rgb\(")
    self.assertRegex(css, r"@media \(max-width: 480px\)[^\n]*\.ed-manage \.ed-btn \{ flex: 1 1 100%; \}")
