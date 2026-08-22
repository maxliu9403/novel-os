import json
from pathlib import Path

from orchestrator import main


def test_run_dry_run_persists_intake_without_llm(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# CLI Book\n\nA complete premise.", encoding="utf-8")
    project = tmp_path / "project"

    code = main([
        "run",
        "--project", str(project),
        "--prompt", str(prompt),
        "--chapters", "2",
        "--words", "5000",
        "--dry-run",
    ])

    assert code == 0
    assert (project / "outputs/input/prompt.md").exists()
    manifests = list((project / "outputs/runs").glob("*/run.json"))
    assert len(manifests) == 1


def test_run_rejects_missing_prompt(tmp_path: Path):
    code = main([
        "run",
        "--project", str(tmp_path / "project"),
        "--prompt", str(tmp_path / "missing.md"),
        "--dry-run",
    ])

    assert code == 2


def test_plan_outline_dry_run_saves_prompt_without_provider(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code = main(["plan", "outline", "--chapters", "2", "--words", "5000", "--dry-run"])

    assert code is None
    assert (tmp_path / "outputs/outline_prompt.md").exists()
    assert not (tmp_path / "outputs/outline.md").exists()


def test_run_infers_length_from_prompt_when_cli_overrides_are_absent(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "Use **20-22 chapters total**.\nTarget **1,200-1,800 English words per chapter**.\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"

    code = main([
        "run", "--project", str(project), "--prompt", str(prompt), "--dry-run",
    ])

    assert code == 0
    manifest_path = next((project / "outputs/runs").glob("*/run.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["spec"]["num_chapters"] == 22
    assert manifest["spec"]["target_words"] == 33000
