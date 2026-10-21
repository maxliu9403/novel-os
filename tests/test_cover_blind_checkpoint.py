"""Blind evidence is durable before facts-aware review can start or interrupt."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.cover_quality import CoverVisualEvaluator
from tests.test_cover_quality import _blind_observation, _scores
from tests.test_cover_service import _actual_jpeg, _bound_inputs, _service


def records(project):
    return list((project / 'outputs/covers/evaluations').glob('*/blind-read.json'))


def test_blind_checkpoint_exists_before_b_and_is_bound_to_exact_candidate(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, _, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    project = tmp_path / 'project'
    before_b = []
    def verify(*args):
        paths = records(project)
        assert len(paths) == len(before_b) + 1
        before_b.append({p: p.read_bytes() for p in paths})
        return _scores()
    service.visual_evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(),
        blind_complete=_blind_observation, verify_complete=verify)
    source = {'analysis_id': 'verified-analysis', 'direction_id': 'approved-direction'}
    result = service.generate('project-one', project, brief, concepts,
        compiled_recipes=recipes, source_binding=source)
    for candidate in result.candidates:
        evaluation = candidate.quality_report['evaluation']
        checkpoint = evaluation['blind_checkpoint']
        path = project / checkpoint['relative_path']
        data = path.read_bytes()
        record = json.loads(data)
        assert hashlib.sha256(data).hexdigest() == checkpoint['sha256']
        assert record['cover_set_id'] == result.cover_set_id
        assert record['candidate_id'] == candidate.candidate_id
        assert record['concept_id'] == candidate.concept_id
        assert record['source_binding'] == source
        assert record['recipe_sha256'] == recipes[candidate.concept_id]['recipe_sha256']
        assert record['image_sha256'] == candidate.sha256
        assert record['artwork_sha256'] == candidate.quality_report['assets']['artwork_sha256']
        assert record['blind_evaluation']['blind_image_sha256'] == record['artwork_sha256']
        assert record['blind_evaluation']['blind_read_sha256'] == evaluation['blind_read_sha256']
        assert record['blind_evaluation']['blind_read'] == evaluation['blind_read']
        assert checkpoint['evaluation_id'] == evaluation['evaluation_id']
        assert Path(evaluation['relative_path']).parent == Path(checkpoint['relative_path']).parent
        assert any(snapshot.get(path) == data for snapshot in before_b)
    saved = {p: p.read_bytes() for p in records(project)}
    first = result.candidates[0]
    updated = service.reevaluate_candidate('project-one', project, result.cover_set_id,
        first.candidate_id, expected_revision=result.revision)
    assert len(records(project)) == 5
    assert all(p.read_bytes() == content for p, content in saved.items())
    new_evaluation = updated.candidates[0].quality_report['evaluation']
    assert new_evaluation['evaluation_id'] != first.quality_report['evaluation']['evaluation_id']
    assert new_evaluation['previous_evaluation_id'] == first.quality_report['evaluation']['evaluation_id']


def test_abrupt_process_exit_during_b_preserves_bound_a_without_final_report(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from core.cover_quality import CoverVisualEvaluator
from tests.test_cover_quality import _blind_observation, _scores
from tests.test_cover_service import _actual_jpeg, _bound_inputs, _service
root = Path(sys.argv[1])
brief, concepts, recipes = _bound_inputs()
service, _, _ = _service(root, [_actual_jpeg()] * 4)
def stop(*args):
    os._exit(73)
service.visual_evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(),
    blind_complete=_blind_observation, verify_complete=stop)
service.generate('project-one', root / 'project', brief, concepts, compiled_recipes=recipes,
    source_binding={'analysis_id': 'verified-analysis'})
'''
    process = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert process.returncode == 73, process.stderr
    project = tmp_path / 'project'
    paths = records(project)
    assert len(paths) == 1
    record = json.loads(paths[0].read_text())
    assert record['blind_evaluation']['blind_read'] == _blind_observation()
    assert record['image_sha256'] and record['artwork_sha256']
    assert not (paths[0].parent / 'report.json').exists()
    from core.cover_store import CoverStore
    stored = CoverStore(project).load(record['cover_set_id'])
    assert record['candidate_id'] in {candidate.candidate_id for candidate in stored.candidates}
    assert not stored.candidates[0].attempt_history  # Kill interrupted ordinary final persistence.


def test_checkpoint_failure_prevents_b_without_fabricating_a_success(tmp_path, monkeypatch):
    brief, concepts, recipes = _bound_inputs()
    service, _, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    calls = []
    def fail(*args, **kwargs):
        raise OSError('checkpoint disk unavailable')
    monkeypatch.setattr(service, '_persist_blind_checkpoint', fail)
    service.visual_evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(),
        blind_complete=_blind_observation, verify_complete=lambda *args: calls.append(args) or _scores())
    result = service.generate('project-one', tmp_path / 'project', brief, concepts, compiled_recipes=recipes)
    assert calls == []
    for candidate in result.candidates:
        assert candidate.quality_report['evaluation']['evaluation_status'] == 'protocol_error'
        assert 'blind_checkpoint' not in candidate.quality_report['evaluation']
        assert candidate.quality_report['status'] != 'recommended_for_human_review'


@pytest.mark.parametrize('workshop', [False, True])
def test_checkpoint_rejects_owner_escaping_symlink_before_write_or_b(tmp_path, workshop):
    brief, concepts, recipes = _bound_inputs()
    service, _, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    project = tmp_path / 'project'
    cover_root = project / 'session-covers' if workshop else None
    evaluations = (cover_root or project / 'outputs/covers') / 'evaluations'
    evaluations.parent.mkdir(parents=True)
    other_owner = tmp_path / 'other-owner'
    other_owner.mkdir()
    evaluations.symlink_to(other_owner, target_is_directory=True)
    calls = []
    service.visual_evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(),
        blind_complete=_blind_observation, verify_complete=lambda *args: calls.append(args) or _scores())

    result = service.generate('project-one', project, brief, concepts,
        compiled_recipes=recipes, cover_root=cover_root)

    assert calls == []
    assert list(other_owner.iterdir()) == []
    assert all(candidate.status == 'failed' for candidate in result.candidates)


def test_immutable_checkpoint_cannot_be_replaced_or_attached_to_another_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import cover_service
    from core.cover_models_v2 import CoverQualityReport
    from tests.test_cover_service import _independent_evaluator
    brief, concepts, recipes = _bound_inputs()
    service, _, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    project = tmp_path / 'project'
    result = service.generate('project-one', project, brief, concepts, compiled_recipes=recipes)
    first, second = result.candidates[:2]
    evaluation = first.quality_report['evaluation']
    checkpoint = evaluation['blind_checkpoint']
    path = project / checkpoint['relative_path']
    original = path.read_bytes()
    monkeypatch.setattr(cover_service.uuid, 'uuid4', lambda: SimpleNamespace(hex=checkpoint['evaluation_id'].removeprefix('evaluation-')))
    with pytest.raises(FileExistsError):
        service._persist_blind_checkpoint(project, result, first, evaluation,
            image_sha256=first.sha256, artwork_sha256=first.quality_report['assets']['artwork_sha256'])
    assert path.read_bytes() == original
    with pytest.raises(cover_service.CoverServiceError, match='binding failed'):
        service._persist_evaluation(project, result, second, CoverQualityReport.from_dict(_scores()),
            {'evaluation': dict(evaluation)})
    path.write_bytes(original + b' ')
    with pytest.raises(cover_service.CoverServiceError, match='binding failed'):
        service._persist_evaluation(project, result, first, CoverQualityReport.from_dict(_scores()),
            {'evaluation': dict(evaluation)})
