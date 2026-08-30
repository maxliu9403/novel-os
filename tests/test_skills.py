from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAINSTORM = ROOT / "skills" / "novel-brainstorm-workshop"
COVER = ROOT / "skills" / "novel-cover-studio"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_brainstorm_skill_emits_machine_readable_cover_handoff() -> None:
    skill = _read(BRAINSTORM / "SKILL.md")
    contract = _read(BRAINSTORM / "references" / "prompt-contract.md")
    body = skill + contract

    assert "COVER_HANDOFF_BEGIN" in body
    assert "COVER_HANDOFF_END" in body
    for field in (
        '"title"', '"language"', '"genre"', '"target_audience"',
        '"core_task"', '"core_conflict"', '"emotional_promise"',
        '"protagonist"', '"decisive_story_node"', '"secondary_task"',
        '"world_signals"', '"title_direction"', '"forbidden_elements"',
    ):
        assert field in contract
    assert "Sections A-E" in skill
    assert "novel-cover-studio" in skill


def test_cover_skill_requires_confirmed_design_and_distinct_concepts() -> None:
    skill = _read(COVER / "SKILL.md")
    handoff = _read(COVER / "references" / "cover-handoff.md")
    direction = _read(COVER / "references" / "commercial-direction.md")
    body = skill + handoff + direction

    assert "3-5" in body
    assert "COVER_HANDOFF_BEGIN" in body
    assert "COVER_HANDOFF_END" in body
    assert "Sections A-E" in body
    assert "before" in body.lower() and "generation" in body.lower()
    assert "distinct" in body.lower()
    assert "2048x3072" in body
    assert "exact title" in body.lower()
    assert "real place" in body.lower()
    assert "watermark" in body.lower()
    assert "Do not infer or invent ethnicity" in body
    assert "American white casting" not in body
    assert "premium US commercial fiction magazine cover" in body
    assert "visible action, reaction, and stakes" in body
    assert "Western editorial typography" in body


def test_cover_skill_detects_launcher_and_documents_host_sync() -> None:
    skill = _read(COVER / "SKILL.md")

    assert "./deploy.sh novel-cover --help" in skill
    assert "./deploy.sh novel-cover" in skill
    assert "python core/orchestrator.py cover generate" in skill
    assert "$HOME/.codex/skills/novel-cover-studio" in skill
    assert "docker" in skill.lower()
    assert "restart" in skill.lower()


def test_cover_skill_has_valid_discovery_metadata() -> None:
    skill = _read(COVER / "SKILL.md")
    metadata = _read(COVER / "agents" / "openai.yaml")

    assert skill.startswith("---\nname: novel-cover-studio\n")
    assert "description: Use when" in skill
    assert "display_name:" in metadata
    assert "short_description:" in metadata
    assert "default_prompt:" in metadata
