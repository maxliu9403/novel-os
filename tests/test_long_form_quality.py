from pathlib import Path
import hashlib
import json

import pytest

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


def test_source_planned_early_climax_is_a_pacing_warning_not_a_format_failure():
    contract = _contract()
    foundation = _foundation(contract)
    volume = foundation['volume_contracts'][1]
    volume['climax_chapter'] = contract.volumes[1].chapter_start + 3
    bound = bind_foundation_serialization(foundation, contract)
    report = evaluate_foundation_plan(bound)
    assert report.status == 'passed'
    finding = next(f for f in report.findings if f.code == 'early_volume_climax')
    assert finding.severity == 'warning'
    assert finding.chapter == volume['climax_chapter']
    assert finding.volume_id == volume['volume_id']
    obligation = next(o for o in bound['volume_contracts'][1]['obligations'] if o['kind'] == 'climax')
    assert obligation['due_chapter'] == volume['climax_chapter']


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


def test_interval_audit_derives_plan_binding_without_changing_legacy_contracts(tmp_path: Path):
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
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}

    report = evaluate_progress(tmp_path, 5)

    assert report.status == "passed", [f.message for f in report.findings]
    assert report.checks['assessment_scope'] == 'plan_integrity'
    assert report.checks['semantic_delivery'] == 'not_assessed'
    bindings = report.checks['volume_obligation_bindings']
    assert bindings[0]['added_event_ids'] == ['volume_01_promise']
    assert all(p.read_bytes() == body for p, body in before.items())
    assert evaluate_progress(tmp_path, 5).report_id == report.report_id


@pytest.mark.parametrize('chapters', [60, 100])
def test_story_first_projection_reaches_first_long_form_audit(tmp_path, chapters):
    """Exercise the real producer/consumer combination, not seeded event IDs."""
    import re
    from core.chapter_review import project_outline
    from commercial_fixtures import legacy_chapter_contract_payload
    format_contract = _contract(chapters)
    foundation = _foundation(format_contract)
    for chapter in foundation['chapters']:
        number = chapter['number']
        chapter['execution_contract'] = {
            **legacy_chapter_contract_payload(), 'chapter': number,
            'active_choice': f'Choice {number}',
            'irreversible_change': f'Change {number}',
            'local_payoff': f'Payoff {number}',
        }
        chapter['execution_contract'].pop('world_event_ids', None)
    assert evaluate_foundation_plan(foundation).status == 'passed'
    initialize_project(str(tmp_path), 'The Four Doors', "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.set_metadata('narrative_format', format_contract.to_dict())
    apply_story_foundation(state, foundation, format_contract.target_words)
    state.save_state()
    store = ArtifactStore(tmp_path)
    for number in range(1, 6):
        outline = project_outline(foundation, number)
        value = json.loads(re.search(r'\[CHAPTER_CONTRACT\]\s*(.*?)\s*\[/CHAPTER_CONTRACT\]', outline, re.S)[1])
        revision = store.put_json(chapter=number, kind='chapter_contract', value=value, source='architect')
        store.set_head(number, 'chapter_contract', revision.revision_id, expected_revision_id=None)
    report = evaluate_progress(tmp_path, 5)
    assert report.status == 'passed', [f.message for f in report.findings]
    assert all(not b['added_event_ids'] for b in report.checks['volume_obligation_bindings'])
    assert 'world_event_ids' not in foundation['chapters'][0]['execution_contract']


@pytest.mark.parametrize('defect', ['absent', 'wrong_chapter', 'corrupt'])
def test_binding_does_not_conceal_missing_or_invalid_contracts(tmp_path, defect):
    format_contract = _contract()
    initialize_project(str(tmp_path), 'The Four Doors', "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.set_metadata('narrative_format', format_contract.to_dict())
    apply_story_foundation(state, _foundation(format_contract), format_contract.target_words)
    state.save_state()
    store = ArtifactStore(tmp_path)
    for number in range(2, 6):
        _write_chapter_contract(store, number)
    if defect != 'absent':
        _write_chapter_contract(store, 1)
        head = store.get_head(1, 'chapter_contract')
        if defect == 'wrong_chapter':
            value = json.loads(store.read_text(head.revision_id))
            value['chapter'] = 2
            revision = store.put_json(chapter=1, kind='chapter_contract', value=value, source='fixture')
            store.set_head(1, 'chapter_contract', revision.revision_id, expected_revision_id=head.revision_id)
        else:
            revision = store.get_revision(head.revision_id)
            (store.blob_root / revision.sha256).write_text('{}')
    report = evaluate_progress(tmp_path, 5)
    assert report.status == 'blocked'
    code = 'missing_chapter_contract' if defect == 'absent' else 'invalid_chapter_contract'
    assert any(f.code == code and f.chapter == 1 for f in report.findings)


def test_replacing_audit_preserves_previous_exact_report(tmp_path):
    from core.long_form_quality import LongFormReport, report_path, write_report
    old = b'{"schema_version":"long-form-quality.v1","status":"blocked"}\n'
    path = report_path(tmp_path, as_of_chapter=5)
    path.parent.mkdir(parents=True)
    path.write_bytes(old)
    report = LongFormReport(audit_type='interval', status='passed', format_id='fixture', as_of_chapter=5)
    write_report(tmp_path, report, as_of_chapter=5)
    archive = path.parent / 'history' / (hashlib.sha256(old).hexdigest() + '.json')
    assert archive.read_bytes() == old
    after = path.read_bytes()
    write_report(tmp_path, report, as_of_chapter=5)
    assert path.read_bytes() == after


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
