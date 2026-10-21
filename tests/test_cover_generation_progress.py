"""Real CoverService stages drive durable task feedback without extra calls."""
from copy import deepcopy

import pytest

from api import cover_generation_tasks as tasks
from core.cover_store import CoverStore
from core.image_client import ImageClientError
from tests.test_cover_service import _brief, _concepts, _jpeg, _service


def test_batch_progress_uses_persisted_exact_set_and_counts_processed_not_quality_passes(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    service, client, _ = _service(tmp_path, [
        _jpeg(marker=b'a'), ImageClientError('fixture unavailable'),
        _jpeg(marker=b'c'), _jpeg(marker=b'd')])
    events = []

    def persist(event):
        stored = CoverStore(project).load(event['cover_set_id'])
        assert stored.cover_set_id == event['cover_set_id']
        assert event['total'] == len(stored.candidates) == 4
        # A stage checkpoint never projects counts from an unsaved replacement.
        assert event['completed'] == sum(c.status != 'pending' for c in stored.candidates)
        assert event['ready'] == sum(c.status in {'ready', 'selected'} for c in stored.candidates)
        assert event['failed'] == sum(c.status == 'failed' for c in stored.candidates)
        events.append(deepcopy(event))

    monkeypatch.setattr(tasks, 'current_progress_sink', lambda: persist)
    result = service.generate('project', project, _brief(), _concepts())
    assert len(client.prompts) == 4
    assert events and events[0]['phase'] == 'preparing'
    assert {e['cover_set_id'] for e in events} == {result.cover_set_id}
    generating = [e for e in events if e['phase'] == 'generating']
    assert [e['current_candidate_index'] for e in generating] == [1, 2, 3, 4]
    assert [e['current_candidate_id'] for e in generating] == [c.candidate_id for c in result.candidates]
    assert len([e for e in events if e['phase'] == 'reviewing']) == 3
    assert events[-1]['phase'] == 'saving'
    assert (events[-1]['completed'], events[-1]['ready'], events[-1]['failed']) == (4, 3, 1)
    # Asset readiness is not an assertion that its independent quality review passed.
    assert any(c.quality_report['status'] != 'recommended_for_human_review' for c in result.candidates if c.status == 'ready')


@pytest.mark.parametrize('fail_phase,image_calls', [('preparing', 0), ('generating', 0), ('reviewing', 1)])
@pytest.mark.parametrize('error_type', [OSError, ValueError])
def test_failed_progress_checkpoint_stops_before_next_paid_call(tmp_path, monkeypatch, fail_phase, image_calls, error_type):
    service, client, _ = _service(tmp_path, [_jpeg(marker=bytes([n])) for n in range(4)])
    reviews = []
    original = service._quality_report

    def observed_review(*args, **kwargs):
        reviews.append(1)
        return original(*args, **kwargs)

    def broken_sink(event):
        if event['phase'] == fail_phase:
            raise error_type('fixture checkpoint unavailable')

    monkeypatch.setattr(tasks, 'current_progress_sink', lambda: broken_sink)
    monkeypatch.setattr(service, '_quality_report', observed_review)
    with pytest.raises(tasks.GenerationProgressError, match='checkpoint'):
        service.generate('project', tmp_path / 'project', _brief(), _concepts())
    assert len(client.prompts) == image_calls
    assert reviews == []


def test_absent_task_sink_preserves_direct_service_generation(tmp_path, monkeypatch):
    service, client, _ = _service(tmp_path, [_jpeg(marker=bytes([n])) for n in range(4)])
    monkeypatch.setattr(tasks, 'current_progress_sink', lambda: None)
    result = service.generate('project', tmp_path / 'project', _brief(), _concepts())
    assert len(client.prompts) == len(result.candidates) == 4


def test_composition_checkpoint_failure_keeps_reserved_attempt_and_stops_following_work(tmp_path, monkeypatch):
    from tests.test_cover_service import _actual_jpeg, _bound_inputs
    brief, concepts, recipes = _bound_inputs()
    project = tmp_path / 'project'
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    seen = []

    def broken_sink(event):
        seen.append(event)
        if event['phase'] == 'composing':
            raise OSError('fixture task disk failure')

    monkeypatch.setattr(tasks, 'current_progress_sink', lambda: broken_sink)
    with pytest.raises(tasks.GenerationProgressError):
        service.generate('project', project, brief, concepts, compiled_recipes=recipes,
                         source_binding={'direction_id':'approved-direction'})
    assert len(client.prompts) == 1
    assert [event['phase'] for event in seen] == ['preparing', 'generating', 'composing']
    saved = CoverStore(project).load(seen[0]['cover_set_id'])
    assert saved.concepts[0].scene_plan['_generation_authorization']['image_attempts_reserved'] == 1
    assert all(candidate.status == 'pending' for candidate in saved.candidates)
