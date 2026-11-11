"""Historical read-only audits cannot invalidate later paid chapter production."""
import json

import pytest

from artifacts import ArtifactStore
from pipeline_models import RunManifest, RunSpec, StageResult
from pipeline_runner import PipelineRunner
from state_manager import StoryState, initialize_project
from story_foundation import apply_story_foundation
from test_long_form_quality import _contract, _foundation, _write_chapter_contract


def project_with_contracts(tmp_path):
    format_contract = _contract(60)
    initialize_project(str(tmp_path), 'The Four Doors', "Women's Fiction")
    state = StoryState(str(tmp_path))
    state.metadata['narrative_format'] = format_contract.to_dict()
    apply_story_foundation(state, _foundation(format_contract), format_contract.target_words)
    state.save_state()
    store = ArtifactStore(tmp_path)
    for number in range(1, 7):
        _write_chapter_contract(store, number)
    return state, store


def test_audit_inputs_ignore_future_state_and_track_actual_contract_heads(tmp_path):
    state, artifacts = project_with_contracts(tmp_path)
    before = PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', 5)
    state.get_chapter(6).status = 'complete'
    state.get_chapter(7).title = 'A future change'
    state.save_state()
    assert PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', 5) == before
    head = artifacts.get_head(1, 'chapter_contract')
    value = json.loads(artifacts.read_text(head.revision_id))
    value['local_payoff'] = 'A changed planned payoff'
    revision = artifacts.put_json(chapter=1, kind='chapter_contract', value=value, source='fixture')
    artifacts.set_head(1, 'chapter_contract', revision.revision_id, expected_revision_id=head.revision_id)
    assert PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', 5) != before


@pytest.mark.parametrize('previous_status', ['done', 'failed'])
def test_old_audit_recomputation_never_restores_state_or_rewrites_paid_later_stage(tmp_path, previous_status):
    state, _ = project_with_contracts(tmp_path)
    state_path = tmp_path / 'outputs/state/story_state.json'
    snapshot = tmp_path / 'outputs/runs/resume/older.state.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(state_path.read_bytes())
    state.get_chapter(6).status = 'complete'
    state.save_state()
    current = state_path.read_bytes()
    outline = tmp_path / 'outputs/chapter_007_outline.md'
    outline.write_text('Frozen chapter seven plan')
    draft = tmp_path / 'outputs/manuscript/chapter_007_draft.md'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('Already paid chapter seven prose')
    spec = RunSpec(project_path=str(tmp_path), prompt_path='-', num_chapters=60, target_words=54000,
                   quality_policy='legacy', max_retries=0)
    manifest = RunManifest.new(spec, 'resume')
    audit = StageResult(phase='long_form.audit', chapter=5, status=previous_status,
                        input_hashes={'outputs/state/story_state.json': 'legacy-whole-state-hash'},
                        state_snapshot_path=str(snapshot.relative_to(tmp_path)),
                        state_snapshot_hash=PipelineRunner._sha256(snapshot))
    manifest.record(audit)
    paid = StageResult(phase='chapter.write', chapter=7, status='done',
                      input_hashes=PipelineRunner._stage_input_hashes(tmp_path, 'chapter.write', 7, manifest),
                      artifact_paths=[str(draft.relative_to(tmp_path))],
                      artifact_hashes={str(draft.relative_to(tmp_path)): PipelineRunner._sha256(draft)})
    manifest.record(paid)
    runner = PipelineRunner()
    runner._rerun_started = False
    runner._last_valid_state_snapshot = str(snapshot.relative_to(tmp_path))
    runner._active_orchestrator = None
    store = runner._store(tmp_path, manifest.run_id)
    audit_relative = 'outputs/quality/long-form/chapter_005_audit.json'
    result = runner._stage(manifest, tmp_path, store, 'long_form.audit', 5,
        lambda: runner._evaluate_and_write_long_form(tmp_path, 5), runner._validate_long_form_result, [audit_relative])
    assert result.status == 'done'
    assert state_path.read_bytes() == current
    assert not runner._rerun_started
    assert runner._last_valid_state_snapshot == str(snapshot.relative_to(tmp_path))
    assert not result.state_snapshot_path

    def forbidden_rewrite():
        pytest.fail('A read-only historical audit caused a paid chapter to be regenerated')
    assert runner._stage(manifest, tmp_path, store, 'chapter.write', 7,
                         forbidden_rewrite, lambda _: None, paid.artifact_paths) is paid
    assert draft.read_text() == 'Already paid chapter seven prose'


def test_volume_close_fingerprint_detects_missing_or_modified_finals(tmp_path):
    state, _ = project_with_contracts(tmp_path)
    end = _contract(60).volumes[0].chapter_end
    for number in range(7, end + 1):
        _write_chapter_contract(ArtifactStore(tmp_path), number)
    before = PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', end)
    final = tmp_path / 'outputs/manuscript/chapter_001_final.md'
    final.parent.mkdir(exist_ok=True)
    final.write_text('Approved final prose')
    after = PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', end)
    assert before != after
    final.write_text('Changed approved final prose')
    assert PipelineRunner._stage_input_hashes(tmp_path, 'long_form.audit', end) != after
