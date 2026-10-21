"""Regression coverage for continuous chapter context across production paths."""

import pytest

from artifacts import ArtifactStore
from commercial_fixtures import chapter_contract_v2, commercial_story_fixture
from orchestrator import NovelOrchestrator
from state_manager import Character


@pytest.fixture
def book(tmp_path):
    orch = NovelOrchestrator(str(tmp_path / "book"))
    orch.state.metadata["target_chapters"] = 8
    orch.state.add_character(Character(
        id="lin", full_name="林岚", role="protagonist",
        gender="female", pronouns="她 / she/her", aliases=["阿岚"],
    ))
    for number in range(1, 9):
        chapter = orch.state.create_chapter(number)
        chapter.pov_character = "林岚"
    return orch


def approve_prior(book, text):
    artifacts = ArtifactStore(book.project_path)
    revision = artifacts.put_text(chapter=1, kind="final", text=text, source="test")
    prior = book.state.get_chapter(1)
    prior.status = "complete"
    prior.canonical_revision_id = revision.revision_id
    return artifacts


def test_all_manuscript_stages_carry_the_promoted_ending_and_identity(book):
    ending = "林岚把信封收回口袋，决定在明早的家宴上追问那笔转账。"
    artifacts = approve_prior(book, ending)
    stale = artifacts.put_text(
        chapter=1, kind="final", text="UNAPPROVED alternate ending", source="test",
    )
    artifacts.set_head(1, "final", stale.revision_id, expected_revision_id=None)
    (book.manuscript_dir / "chapter_001_final.md").write_text("STALE legacy projection")
    chapter = book.state.get_chapter(2)

    prompts = [
        book._generate_chapter_outline_prompt(chapter),
        book._generate_chapter_prompt(chapter),
        book._generate_edit_prompt(chapter, "林岚攥着信封。", "line"),
        book._generate_validation_prompt(2, "林岚攥着信封。"),
        book._generate_style_prompt(2, "林岚攥着信封。"),
    ]
    for prompt in prompts:
        assert ending in prompt
        assert "UNAPPROVED" not in prompt
        assert "STALE" not in prompt
        assert "她 / she/her" in prompt
        assert "阿岚" in prompt
        assert "personal interpretation" in prompt


def test_final_commercial_review_includes_identity_and_carryover_outside_candidate(book):
    ending = "林岚决定带着信封去见姐姐。"
    approve_prior(book, ending)
    book.state.story_bible["commercial_story_contract"] = commercial_story_fixture().to_dict()
    candidate = "林岚在门外停下，仍舍不得敲门。"

    prompt = book._generate_commercial_review_prompt(2, candidate, chapter_contract_v2())

    assert ending in prompt.split("## Candidate Manuscript")[0]
    assert "她 / she/her" in prompt
    assert f"```markdown\n{candidate}\n```" in prompt
    assert "exact candidate evidence, not new JSON fields" in prompt


def test_character_creation_after_sparse_import_does_not_reuse_an_existing_id(book):
    book.state.add_character(Character(id="char_003", full_name="沈舟", role="supporting"))

    created = book.add_character("陈默", "supporting")

    assert created == "char_004"
    assert book.state.get_character("char_003").full_name == "沈舟"


def test_expanded_outline_drafting_path_keeps_carryover_and_craft(book):
    ending = "她已经签了字；失去的工作不能再当作威胁。"
    approve_prior(book, ending)
    (book.outputs_dir / "chapter_002_outline.md").write_text("# Approved chapter plan")

    book.write_chapter(2, dry_run=True)

    prompt = (book.outputs_dir / "chapter_002_scribe_prompt.md").read_text()
    assert "# Approved chapter plan" in prompt
    assert ending in prompt
    assert "Opening Chapters: Priority" in prompt
    assert "Serial Craft Obligations" in prompt


def test_unapproved_previous_draft_is_not_treated_as_a_delivered_hook(book):
    book.state.get_chapter(1).status = "drafted"
    (book.manuscript_dir / "chapter_001_draft.md").write_text("UNREVIEWED_EVENT")

    prompt = book._generate_chapter_prompt(book.state.get_chapter(2))

    assert "UNREVIEWED_EVENT" not in prompt
    assert "Approved prose is unavailable" in prompt


def test_legacy_approved_chapter_can_supply_its_ending(book):
    book.state.get_chapter(1).status = "complete"
    (book.manuscript_dir / "chapter_001_revised.md").write_text("旧项目已批准的章末承诺。")

    assert "旧项目已批准的章末承诺。" in book._serial_craft_context(2)
    book.quality_policy = "evidence_v1"
    assert "旧项目已批准的章末承诺。" not in book._serial_craft_context(2)


def test_full_guardian_review_keeps_middle_passages(book):
    body = "开场。" * 3000 + "林岚拿起信封，他决定离开。" + "结尾。" * 3000

    prompt = book._generate_validation_prompt(2, body)

    assert body in prompt


def test_opening_emphasis_is_limited_and_finale_still_closes(book):
    assert "Opening Chapters: Priority" in book._serial_craft_context(3)
    assert "Opening Chapters: Priority" not in book._serial_craft_context(4)
    assert "Final Chapter: Closure" in book._serial_craft_context(8)
    book.state.metadata["target_chapters"] = 2
    short_finale = book._serial_craft_context(2)
    assert "Opening Chapters: Priority" in short_finale
    assert "Final Chapter: Closure" in short_finale


def test_wrong_chapter_canonical_reference_fails_instead_of_using_stale_prose(book):
    artifacts = ArtifactStore(book.project_path)
    wrong = artifacts.put_text(chapter=3, kind="final", text="Wrong chapter", source="test")
    book.state.get_chapter(1).status = "complete"
    book.state.get_chapter(1).canonical_revision_id = wrong.revision_id

    with pytest.raises(ValueError, match="previous chapter canonical revision"):
        book._serial_craft_context(2)


def test_blueprint_requests_identity_and_distributed_payoffs(book):
    book.plan_outline(8, 20000, dry_run=True)

    prompt = (book.outputs_dir / "outline_prompt.md").read_text()
    assert '"gender":' in prompt and '"pronouns":' in prompt and '"aliases":' in prompt
    assert "short-term, arc-level, and book-level" in prompt
    assert "emotional cause of the key choice" in prompt
