from pathlib import Path

from core.artifacts import ArtifactStore
from core.contracts import ChapterContract
from core.long_form_quality import (
    audit_due_chapters,
    evaluate_foundation_plan,
    evaluate_progress,
)
from core.narrative_format import (
    bind_foundation_serialization,
    infer_narrative_format,
    volume_contract_template,
)
from core.novel_classification import infer_classification
from core.state_manager import StoryState, initialize_project
from core.story_foundation import apply_story_foundation


def _contract(chapters: int = 60):
    classification = infer_classification(
        genre="Women's Fiction / Mystery",
        premise="A family secret divides three generations.",
        chapters=chapters,
    )
    return infer_narrative_format(
        classification,
        chapters=chapters,
        target_words=chapters * 900,
        premise="A family secret divides three generations.",
        explicit_length=True,
    )


def _foundation(contract):
    volumes = volume_contract_template(contract)
    for item in volumes:
        number = item["volume_number"]
        item.update({
            "title": f"Movement {number}",
            "central_conflict": f"Distinct central conflict {number}",
            "volume_promise": f"Distinct promise {number}",
            "protagonist_shift": f"Durable shift {number}",
            "payoff": f"Visible payoff {number}",
            "carryover_hook": (
                f"Consequence {number + 1}" if number < len(volumes) else ""
            ),
        })
    value = {
        "title": "The Four Doors",
        "premise": "A family secret divides three generations.",
        "characters": [{"id": "char_001", "name": "Mara", "role": "protagonist"}],
        "plot_threads": [{"id": "plot_001", "name": "Truth", "type": "main"}],
        "volume_contracts": volumes,
        "chapters": [
            {
                "number": number,
                "title": f"Chapter {number}",
                "summary": f"Unique movement {number}",
                "target_words": 900,
            }
            for number in range(1, contract.total_chapters + 1)
        ],
    }
    return bind_foundation_serialization(value, contract)


def test_long_form_plan_gate_verifies_complete_volume_binding():
    foundation = _foundation(_contract())

    report = evaluate_foundation_plan(foundation)

    assert report.status == "passed"
    assert report.checks["volume_count"] == 3
    assert report.checks["complete_chapter_binding"] is True


def test_long_form_audits_every_five_chapters_and_at_volume_boundaries():
    contract = _contract(62)

    due = audit_due_chapters(contract)

    assert 5 in due
    assert contract.volumes[0].chapter_end in due
    assert due[-1] == 62


def _write_chapter_contract(
    store: ArtifactStore,
    chapter: int,
    *,
    world_event_ids=(),
    repeated: bool = False,
):
    suffix = "same" if repeated else str(chapter)
    value = ChapterContract(
        chapter=chapter,
        goal=f"Goal {chapter}",
        obstacle=f"Obstacle {chapter}",
        active_choice=f"Choice {suffix}",
        cost=f"Cost {chapter}",
        irreversible_change=f"Change {suffix}",
        local_payoff=f"Payoff {suffix}",
        ending_pressure=f"Pressure {chapter}",
        world_event_ids=tuple(world_event_ids),
    )
    revision = store.put_json(
        chapter=chapter,
        kind="chapter_contract",
        value=value.to_dict(),
        source="architect",
    )
    store.set_head(
        chapter,
        "chapter_contract",
        revision.revision_id,
        expected_revision_id=None,
    )


def test_interval_audit_blocks_missing_volume_milestone(tmp_path: Path):
    contract = _contract()
    foundation = _foundation(contract)
    initialize_project(str(tmp_path), "The Four Doors", "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.set_metadata("narrative_format", contract.to_dict())
    apply_story_foundation(state, foundation, contract.target_words)
    assert state.chapters[23].volume_id == "volume_02"
    assert state.chapters[23].chapter_in_volume == 3
    state.save_state()
    store = ArtifactStore(tmp_path)
    for chapter in range(1, 6):
        _write_chapter_contract(store, chapter)

    report = evaluate_progress(tmp_path, 5)

    assert report.status == "blocked"
    assert any(item.code == "missing_volume_milestone" for item in report.findings)


def test_interval_audit_accepts_due_milestone_and_distinct_changes(tmp_path: Path):
    contract = _contract()
    foundation = _foundation(contract)
    initialize_project(str(tmp_path), "The Four Doors", "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.set_metadata("narrative_format", contract.to_dict())
    apply_story_foundation(state, foundation, contract.target_words)
    state.save_state()
    store = ArtifactStore(tmp_path)
    for chapter in range(1, 6):
        events = ("volume_01_promise",) if chapter == 1 else ()
        _write_chapter_contract(store, chapter, world_event_ids=events)

    report = evaluate_progress(tmp_path, 5)

    assert report.status == "passed"


def test_interval_audit_blocks_repeated_state_changes(tmp_path: Path):
    contract = _contract()
    foundation = _foundation(contract)
    initialize_project(str(tmp_path), "The Four Doors", "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.set_metadata("narrative_format", contract.to_dict())
    apply_story_foundation(state, foundation, contract.target_words)
    state.save_state()
    store = ArtifactStore(tmp_path)
    for chapter in range(1, 6):
        _write_chapter_contract(
            store,
            chapter,
            world_event_ids=("volume_01_promise",) if chapter == 1 else (),
            repeated=chapter in {4, 5},
        )

    report = evaluate_progress(tmp_path, 5)

    assert report.status == "blocked"
    assert any(item.code == "repeated_irreversible_change" for item in report.findings)
