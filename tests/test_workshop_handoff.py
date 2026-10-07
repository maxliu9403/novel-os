"""Exercise the Skill helper at the real intake seam, without any model call."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from narrative_format import infer_narrative_format, parse_narrative_format_block
from novel_classification import infer_classification


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/novel-brainstorm-workshop/scripts/handoff.py"
SPEC = importlib.util.spec_from_file_location("workshop_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


def make_prompt(tmp_path, *, mode="short_novel", title="A Mother's Choice.", author="Terry",
                mutate=None, chapters=21, target_words=11550):
    classification = infer_classification(genre="Family Suspense", audience="Adult readers")
    contract = infer_narrative_format(classification, chapters=chapters, target_words=target_words,
        explicit_length=True, mode=mode,
        **({"series_id": "family", "series_title": "The Family", "series_book_number": 2,
            "planned_books": 3} if mode == "series_installment" else {})).to_dict()
    if mutate:
        contract.pop("format_id", None)
        mutate(contract)
    body = f"""# Novel skeleton
Title: {title}
Author: {author}
Genre: Family Suspense
Audience: Adult readers
Language: English
Tone: Intimate
POV: Third person limited
Chapters: {chapters}
Words: {target_words}

A mother discovers a betrayal and chooses her own future.

## Engine contracts
[NOVEL_CLASSIFICATION_JSON]
{json.dumps(classification.to_dict())}
[/NOVEL_CLASSIFICATION_JSON]
[NARRATIVE_FORMAT_JSON]
{json.dumps(contract)}
[/NARRATIVE_FORMAT_JSON]

## Chapters
Chapter 1 — Absence. Aim for roughly 530–590 words; allow natural variation.
"""
    path = tmp_path / "prompt's file.md"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["short_novel", "standalone_long", "multi_volume", "series_installment"])
def test_real_intake_supports_each_structure_and_all_formats(tmp_path, mode):
    prompt = make_prompt(tmp_path, mode=mode)
    rendered, result = handoff.command(prompt, REPO, "family-book")
    assert result["mode"] == mode
    assert result["chapters"] == 21
    assert result["target_words"] == 11550
    assert "NOVEL_OS_OUTPUT='markdown html docx epub pdf'" in rendered
    assert "NOVEL_OS_DRY_RUN=0" in rendered
    assert "NOVEL_OS_TITLE='A Mother'\"'\"'s Choice.'" in rendered
    assert "NOVEL_OS_SERIES" not in rendered
    if mode == "multi_volume":
        assert len(result["volumes"]) >= 2
    if mode == "series_installment":
        assert result["series"]["book_number"] == 2
        assert any("only the current" in note for note in result["warnings"])


@pytest.mark.parametrize("mode", ["off", "advisory"])
def test_optional_review_mode_matches_supported_launcher(tmp_path, mode):
    prompt = make_prompt(tmp_path)
    command, _ = handoff.command(prompt, REPO, "review-policy", method_mode=mode)
    assert f"NOVEL_OS_METHOD_MODE={mode}" in command
    default, _ = handoff.command(prompt, REPO, "review-policy")
    assert "NOVEL_OS_METHOD_MODE" not in default
    with pytest.raises(ValueError, match="method review mode"):
        handoff.command(prompt, REPO, "review-policy", method_mode="write")


@pytest.mark.parametrize("old,new", [("Chapters: 21", "Chapters: 20"),
    ("Words: 11550", "Words: 11549"), ("Words: 11550", "Words: 11,550"),
    ("POV: Third person limited", ""), ("Language: English", "Language: English\nLanguage: Chinese")])
def test_missing_fields_or_target_disagreement_fail(tmp_path, old, new):
    prompt = make_prompt(tmp_path)
    prompt.write_text(prompt.read_text().replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.validate(prompt, REPO)


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(confirmation_status="pending_confirmation"),
    lambda value: value["volumes"][0].update(chapter_start=2),
    lambda value: value.update(mode="multi_series"),
])
def test_real_parser_rejects_unconfirmed_or_invalid_structures(tmp_path, mutate):
    with pytest.raises(ValueError):
        handoff.validate(make_prompt(tmp_path, mutate=mutate), REPO)


def test_duplicate_or_unclosed_engine_blocks_fail(tmp_path):
    prompt = make_prompt(tmp_path)
    prompt.write_text(prompt.read_text() + "\n[NARRATIVE_FORMAT_JSON]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Exactly one"):
        handoff.validate(prompt, REPO)


@pytest.mark.parametrize("alias", ["Primary audience: Children", "Point of view: First person",
                                    "Primary category: Comedy"])
def test_intake_aliases_cannot_override_final_metadata(tmp_path, alias):
    prompt = make_prompt(tmp_path)
    prompt.write_text(prompt.read_text() + "\n" + alias + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metadata"):
        handoff.validate(prompt, REPO)


def test_chinese_audience_and_title_are_valid_metadata(tmp_path):
    prompt = make_prompt(tmp_path, title="她的未来。")
    prompt.write_text(prompt.read_text().replace("Audience: Adult readers",
        "Audience: 35–60 岁、喜爱情感成长故事的中文读者"), encoding="utf-8")
    result = handoff.validate(prompt, REPO)
    assert result["title"] == "她的未来。"
    assert result["status"] == "metadata_and_contracts_valid"


def test_nested_json_contract_fields_do_not_override_prompt_header(tmp_path):
    prompt = make_prompt(tmp_path)
    contract = {"story_lead_contract": {"language": "Chinese", "pov": "First person",
        "title": "Lead heading", "audience": "Different optional sample audience"}}
    prompt.write_text(prompt.read_text() + "\n## Story lead\n```json\n" +
        json.dumps(contract, indent=2) + "\n```\n", encoding="utf-8")
    result = handoff.validate(prompt, REPO)
    import prompt_intake
    brief = prompt_intake.build_brief(prompt.read_text())
    assert result["status"] == "metadata_and_contracts_valid"
    assert result["title"] == "A Mother's Choice."
    assert brief["title"] == "A Mother's Choice"  # Existing intake strips final punctuation.
    assert brief["language"] == "English"
    assert brief["pov"] == "Third person limited"
    assert brief["audience"] == "Adult readers"


@pytest.mark.parametrize("field", ["language: Chinese", "pov: First person", "title: Lead heading"])
def test_yaml_contract_metadata_lines_remain_rejected(tmp_path, field):
    prompt = make_prompt(tmp_path)
    prompt.write_text(prompt.read_text() + "\n## Story lead\n```yaml\nstory_lead_contract:\n  "
        + field + "\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metadata"):
        handoff.validate(prompt, REPO)


@pytest.mark.parametrize("block", ["NOVEL_CLASSIFICATION_JSON", "NARRATIVE_FORMAT_JSON"])
def test_reversed_blocks_cannot_silently_fall_back_to_inference(tmp_path, block):
    prompt = make_prompt(tmp_path, mode="series_installment")
    text = prompt.read_text().replace(f"[{block}]", "__OPEN__")
    text = text.replace(f"[/{block}]", f"[{block}]").replace("__OPEN__", f"[/{block}]")
    prompt.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.validate(prompt, REPO)


def test_nested_blocks_are_rejected(tmp_path):
    prompt = make_prompt(tmp_path, mode="series_installment")
    text = prompt.read_text().replace("[/NOVEL_CLASSIFICATION_JSON]\n", "")
    text = text.replace("[/NARRATIVE_FORMAT_JSON]", "[/NARRATIVE_FORMAT_JSON]\n[/NOVEL_CLASSIFICATION_JSON]")
    prompt.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.validate(prompt, REPO)


def test_word_budget_is_advisory_and_no_author_or_project_model_is_called(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only helper must not call a model or initialize a project")
    import prompt_intake
    import book_author
    from llm_client import LLMClient
    monkeypatch.setattr(prompt_intake, "initialize_project", forbidden)
    monkeypatch.setattr(book_author, "_default_complete", forbidden)
    monkeypatch.setattr(LLMClient, "complete", forbidden)
    prompt = make_prompt(tmp_path, author="", mutate=lambda value:
        value["volumes"][0].update(target_words=11570))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    result = handoff.validate(prompt, REPO)
    assert any("flexible planning" in note for note in result["warnings"])
    assert any("Author is intentionally blank" in note for note in result["warnings"])
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))


@pytest.mark.parametrize("project", ["", "   ", ".", "..", "../../escape", "book/other", "book.md", "book\nother"])
def test_ambiguous_project_paths_fail(tmp_path, project):
    with pytest.raises(ValueError):
        handoff.command(make_prompt(tmp_path), REPO, project)


@pytest.mark.parametrize("options", [{"output": ["zip"]}, {"output": []}, {"approval": "yes"},
    {"edit_mode": "unknown"}, {"quality_policy": "unknown"}, {"max_retries": -1}])
def test_launcher_values_are_checked_against_actual_repository(tmp_path, options):
    with pytest.raises(ValueError):
        handoff.command(make_prompt(tmp_path), REPO, "my-book", **options)


def test_missing_or_incompatible_launcher_cannot_claim_validation(tmp_path):
    prompt = make_prompt(tmp_path)
    repo = tmp_path / "incomplete-repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="launcher support is unverified"):
        handoff.validate(prompt, repo)
    (repo / "core").mkdir()
    (repo / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "core/orchestrator.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="does not expose"):
        handoff.validate(prompt, repo)


def test_shell_quoting_preserves_title_and_cannot_execute_injections(tmp_path):
    # Execute only a harmless recorder, never the real deployment launcher.
    marker = tmp_path / "should-not-exist"
    title = f"A Mother's $HOME\n$(touch {marker}) `touch {marker}`"
    recorder = tmp_path / "record launch.sh"
    recorder.write_text("#!/bin/sh\nprintf '%s' \"$NOVEL_OS_TITLE\"\n", encoding="utf-8")
    recorder.chmod(0o755)
    shell = handoff.shell_command({"NOVEL_OS_TITLE": title}, recorder, tmp_path / "novel's prompt.md")
    result = subprocess.run(["/bin/sh", "-c", shell], capture_output=True, text=True, check=True)
    assert result.stdout == title
    assert not marker.exists()


def test_installed_skill_runs_from_unrelated_directory_without_writes(tmp_path):
    installed = tmp_path / "installed-skill/scripts"
    installed.mkdir(parents=True)
    helper = installed / "handoff.py"
    shutil.copyfile(SCRIPT, helper)
    prompt = make_prompt(tmp_path)
    result = subprocess.run([sys.executable, str(helper), "command", str(prompt),
        "--repo", str(REPO), "--project", "my-book"], cwd=installed,
        capture_output=True, text=True, check=True)
    assert json.loads(result.stderr)["mode"] == "short_novel"
    assert str(REPO / "deploy.sh") in result.stdout
    assert sorted(p.name for p in installed.iterdir()) == ["handoff.py"]


def test_cli_rejects_invented_series_parameter(tmp_path):
    prompt = make_prompt(tmp_path)
    result = subprocess.run([sys.executable, str(SCRIPT), "command", str(prompt),
        "--repo", str(REPO), "--project", "my-book", "--series", "3"],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("example", ["continuous", "four_volumes", "series", "unequal_volumes"])
def test_documented_generation_examples_survive_real_intake_and_command(tmp_path, example):
    """Execute the shipped instructions, so docs/contract drift cannot pass silently."""
    reference = REPO / "skills/novel-brainstorm-workshop/references/narrative-format.md"
    blocks = re.findall(r"```python\n(.*?)\n```", reference.read_text(), re.DOTALL)
    program = blocks[0]
    expected_spans = [(1, 80)]
    expected_mode = "standalone_long"
    chapters, words = 80, 72000
    if example == "four_volumes":
        program = program.replace('mode="standalone_long"', 'mode="multi_volume"')
        program = program.replace("volume_count=1", "volume_count=4")
        expected_mode = "multi_volume"
        expected_spans = [(1, 20), (21, 40), (41, 60), (61, 80)]
    elif example == "series":
        program = program.replace('mode="standalone_long",', 'mode="series_installment",\n' + blocks[1])
        program = program.replace("chapters=80", "chapters=21").replace("target_words=72000", "target_words=11550")
        chapters, words = 21, 11550
        expected_mode = "series_installment"
        expected_spans = [(1, 21)]
    elif example == "unequal_volumes":
        setup, printing = program.split('print("[NARRATIVE_FORMAT_JSON]")', 1)
        program = setup + blocks[2] + '\nprint("[NARRATIVE_FORMAT_JSON]")' + printing
        expected_mode = "multi_volume"
        expected_spans = [(1, 15), (16, 45), (46, 80)]
    run = subprocess.run([sys.executable, "-c", program], cwd=REPO,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, check=True)
    parsed = parse_narrative_format_block(run.stdout)
    assert parsed.mode == expected_mode
    assert [(v.chapter_start, v.chapter_end) for v in parsed.volumes] == expected_spans
    assert parsed.confirmation_status == "confirmed"
    assert parsed.selection_source == "author_selected"
    # Parsing verifies the recomputed format_id too, including unequal spans.
    serialized = json.loads(run.stdout.split("[NARRATIVE_FORMAT_JSON]", 1)[1].split("[/NARRATIVE_FORMAT_JSON]", 1)[0])
    assert serialized["format_id"] == parsed.format_id
    if example == "series":
        assert serialized["series"] == {"series_id": "memory-archive", "title": "The Memory Archive",
                                        "book_number": 2, "planned_books": 3}
    prompt = make_prompt(tmp_path, chapters=chapters, target_words=words)
    prompt.write_text(re.sub(r"\[NARRATIVE_FORMAT_JSON\].*?\[/NARRATIVE_FORMAT_JSON\]",
        lambda _match: run.stdout.strip(), prompt.read_text(), flags=re.DOTALL), encoding="utf-8")
    rendered, result = handoff.command(prompt, REPO, "example-book")
    assert result["mode"] == expected_mode
    assert result["volumes"] == serialized["volumes"]
    assert result["series"] == serialized["series"]
    assert f"NOVEL_OS_CHAPTERS={chapters}" in rendered
    assert f"NOVEL_OS_WORDS={words}" in rendered
    assert "NOVEL_OS_SERIES" not in rendered
    assert "NOVEL_OS_VOLUMES" not in rendered
