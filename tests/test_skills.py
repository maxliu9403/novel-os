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
        '"principal_characters"', '"relationship_map"', '"lived_environment"',
        '"decisive_story_nodes"', '"secondary_signals"', '"genre_emotion_profile"',
        '"commercial_visual_goal"', '"title_direction"', '"forbidden_elements"',
    ):
        assert field in contract
    assert "Sections A-E" in skill
    assert "novel-cover-studio" in skill


def test_brainstorm_skill_uses_local_commercial_profile_without_default_web_research() -> None:
    skill = _read(BRAINSTORM / "SKILL.md")
    commercial = _read(BRAINSTORM / "references" / "commercial-story-design.md")
    originality = _read(BRAINSTORM / "references" / "originality-isolation.md")
    prompt_contract = _read(BRAINSTORM / "references" / "prompt-contract.md")
    quality = _read(BRAINSTORM / "references" / "quality-gates.md")
    body = skill + prompt_contract + quality

    assert "commercial-story-design.md" in skill
    assert "originality-isolation.md" in skill
    assert "[COMMERCIAL_STORY_JSON]" in commercial
    assert "a5458ce1332e5b74c52889e4a5aed5b9809f69e6e1a8f09cde25c5ab974ee49f" in commercial
    assert "run online audience research" not in body.casefold()
    assert "audience_research" not in body
    assert "research_status" not in body
    assert "source_records" not in body
    assert "research_queries" not in body
    assert "never retrieve the nearest matching story" in originality.casefold()
    assert "raw corpus prose" in originality.casefold()


def test_commercial_agent_handoff_defines_role_specific_checks_and_boundaries() -> None:
    prompt_contract = _read(BRAINSTORM / "references" / "prompt-contract.md")
    quality = _read(BRAINSTORM / "references" / "quality-gates.md")
    body = prompt_contract + quality

    assert "approved story contract" in body.casefold()
    assert "verified and promoted reader-value" in body.casefold()
    assert "repeated humiliation" in body.casefold()
    assert "passive turns" in body.casefold()
    assert "unsupported rescue" in body.casefold()
    assert "repeated hook mechanics" in body.casefold()
    assert "evidence provenance" in body.casefold()
    assert "child knowledge and voice" in body.casefold()
    assert "institutional plausibility" in body.casefold()
    assert "contract-to-prose" in body.casefold()
    assert "character-specific attention" in body.casefold()
    assert "work knowledge" in body.casefold()
    assert "speech strategy" in body.casefold()
    assert "shame trigger" in body.casefold()
    assert "body response" in body.casefold()
    assert "template phrase repetition" in body.casefold()
    assert "i did not cry" in body.casefold()
    assert "never receives raw corpus" in body.casefold()


def test_commercial_prompt_dry_run_has_no_retrieval_or_market_payload(
    tmp_path: Path,
) -> None:
    from commercial_fixtures import commercial_story_lifecycle_fixture
    from commercial_story import commercial_story_block, parse_commercial_story_block
    from orchestrator import NovelOrchestrator
    from prompt_intake import ingest_prompt

    contract = commercial_story_lifecycle_fixture()
    prompt_text = (
        "# The Last Signed Measure\n\n"
        "Audience: women ages 35-60.\n"
        "A community choir treasurer protects a memorial scholarship record.\n\n"
        + commercial_story_block(contract)
    )
    prompt = tmp_path / "fresh-commercial-prompt.md"
    prompt.write_text(prompt_text, encoding="utf-8")
    project = tmp_path / "validation-project"

    ingest_prompt(
        project,
        str(prompt),
        {"title": "The Last Signed Measure", "genre": "Domestic drama"},
    )
    parsed = parse_commercial_story_block(prompt_text)
    assert parsed is not None and parsed.contract_id == contract.contract_id

    orchestrator = NovelOrchestrator(str(project))
    orchestrator.plan_outline(4, 240, dry_run=True)
    architect_prompt = (project / "outputs/outline_prompt.md").read_text(
        encoding="utf-8"
    )
    combined = (prompt_text + "\n" + architect_prompt).casefold()
    for forbidden in (
        "telegram desktop",
        "/批次-",
        "source_records",
        "research_queries",
        "audience_research",
        "click-through rate",
        "conversion rate",
        "vector-search payload",
        "nearest-match payload",
    ):
        assert forbidden not in combined
    assert prompt_text.count("[COMMERCIAL_STORY_JSON]") == 1
    assert prompt_text.count("[/COMMERCIAL_STORY_JSON]") == 1


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
    assert "emotional consequence" in body
    assert "causal relationship action" in body
    assert "flat group portrait" in body


def test_brainstorm_cover_handoff_includes_optional_cover_relevant_conflict_cast() -> None:
    contract = _read(BRAINSTORM / "references" / "prompt-contract.md")

    assert "cover-relevant conflict participants" in contract
    assert "must_appear: false" in contract
    assert "causal conflict tableau" in contract


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
