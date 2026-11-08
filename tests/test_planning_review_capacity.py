"""Capacity preflight must agree with the authenticated text transport."""
import json

import pytest

from core.gemini_gateway import input_bytes
from outline_planning import OutlinePlanningError
from test_outline_planning import make_planner, review


class CapacityClient:
    provider = 'gemini_openai'
    model = 'fixture'

    def __init__(self, limit=156_000):
        self.limit = limit
        self.queries = 0
        self.sent = []

    def text_input_limit_bytes(self):
        self.queries += 1
        return self.limit

    def complete(self, *, system, user):
        assert input_bytes(system, user) <= self.limit
        self.sent.append((system, user))
        return json.dumps(review())


def test_negotiated_capacity_preserves_entire_review_without_splitting(tmp_path):
    client = CapacityClient()
    planner = make_planner(tmp_path, client, count=1)
    candidate = {'chapters': [{'number': 1, 'summary': 'Unique story detail. ' * 6400}]}
    result = planner._semantic_review(candidate, 'review')
    assert result['status'] == 'passed'
    assert client.queries == len(client.sent) == 1
    system, user = client.sent[0]
    assert 128_000 < input_bytes(system, user) <= 156_000
    context = json.JSONDecoder().raw_decode(user[user.index('{"'):])[0]
    assert context['candidate'] == candidate
    assert context['complete_adopted_source'] == planner.source
    # A valid checkpoint costs neither another generation nor a capability call.
    assert planner._semantic_review(candidate, 'review') == result
    assert client.queries == len(client.sent) == 1


def test_old_gateway_keeps_bound_and_does_not_spend_model_calls(tmp_path):
    client = CapacityClient(128_000)
    planner = make_planner(tmp_path, client, count=1)
    with pytest.raises(OutlinePlanningError, match='no review calls'):
        planner._semantic_review({'chapters': [{'number': 1,
            'summary': 'Unique story detail. ' * 6400}]}, 'review')
    assert client.queries == 1
    assert client.sent == []


def test_capacity_is_queried_once_for_preflight_and_again_for_new_review(tmp_path):
    client = CapacityClient()
    planner = make_planner(tmp_path, client, count=8)
    candidate = {'chapters': [{'number': n, 'summary': 'Scene action and consequence. ' * 1500}
                              for n in range(1, 9)]}
    result = planner._semantic_review(candidate, 'review')
    assert result['status'] == 'passed'
    assert client.queries == 1
    windows = result['review_scope']['windows']
    assert sorted(n for w in windows for n in w['requested_chapters']) == list(range(1, 9))
    assert all(any(n in w['included_chapters'] and n + 1 in w['included_chapters']
                   for w in windows) for n in range(1, 8))
    assert len(client.sent) == len(windows)
    # Changed gateway capability is not cached across a new review.
    client.limit = 128_000
    with pytest.raises(OutlinePlanningError, match='no review calls'):
        planner._semantic_review(candidate, 'review-after-configuration-change')
    assert client.queries == 2
    assert len(client.sent) == len(windows)


def test_preflight_counts_frozen_creative_context_and_system_without_changing_them(tmp_path):
    from core.creative_execution import CreativeExecution, encoded, text_hash
    from core.outline_planning import _REVIEW_PROMPT, _json
    from core.planning_context import review_context

    source = 'Author-approved choice and consequence. ' + 's' * 45_000
    guidance = {'documents': [{'id': 'tag.fixture',
                              'markdown': 'Preserve agency and earned trust. ' + 'g' * 43_000}]}
    source_context = {'mode': 'source_development',
                      'facts': 'The older sister witnessed the original refusal.',
                      'unknown': 'The source ending has not been provided.'}
    handoff = {'source_sha256': text_hash(source), 'origin': {},
               'adopted_proposals': ['Keep the refusal and its real cost.'],
               'legacy_guidance': {}, 'stage_contexts': {'review': guidance},
               'story_engine': 'story-first.v1', 'source_context': source_context}
    system = 'Architect: check source fidelity and emotional payoff. ' + 'r' * 2_000
    lock = {'handoff': handoff, 'rules': '', 'system_prompts': {'outline_planner': system}}
    project = tmp_path / 'book'
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(source)
    directory = project / 'outputs/runs/test/creative-design'
    directory.mkdir(parents=True)
    (directory / 'lock.json').write_bytes(encoded(lock))
    original_lock = (directory / 'lock.json').read_bytes()
    runtime = CreativeExecution(project, directory, lock)
    client = CapacityClient()
    planner = make_planner(tmp_path, client, count=1, source=source, creative_execution=runtime)
    candidate = {'chapters': [{'number': 1,
                              'summary': 'A costly refusal changes trust. ' + 'c' * 45_000}]}
    context = review_context(planner._context(), candidate, complete_source=source)
    assert input_bytes('', _REVIEW_PROMPT + '\n\n' + _json(context)) < 128_000

    assert planner._semantic_review(candidate, 'review')['status'] == 'passed'
    assert client.queries == len(client.sent) == 1
    sent_system, user = client.sent[0]
    assert 128_000 < input_bytes(sent_system, user) <= 156_000
    assert sent_system == system
    actual_context = json.JSONDecoder().raw_decode(user[user.index('{"'):])[0]
    assert actual_context['candidate'] == candidate
    assert actual_context['complete_adopted_source'] == source
    creative = json.loads(user.split('[CREATIVE_EXECUTION_CONTEXT]\n')[1]
                          .split('\n[/CREATIVE_EXECUTION_CONTEXT]')[0])
    assert creative['genre_context'] == guidance
    assert creative['source_context'] == source_context
    assert creative['adopted_proposals'] == handoff['adopted_proposals']
    assert (directory / 'lock.json').read_bytes() == original_lock
    assert (inputs / 'prompt.md').read_text() == source


def test_cancelled_capacity_query_cannot_dispatch_or_save_a_pass(tmp_path):
    from core.llm_client import LLMCancelledError

    class CancelledClient(CapacityClient):
        def text_input_limit_bytes(self):
            self.queries += 1
            raise LLMCancelledError('Cancelled during capacity discovery')

    client = CancelledClient()
    planner = make_planner(tmp_path, client, count=1)
    with pytest.raises(LLMCancelledError):
        planner._semantic_review({'chapters': [{'number': 1,
            'summary': 'Preserve the complete scene. ' * 5_000}]}, 'review')
    assert client.queries == 1
    assert client.sent == []
    assert not (planner.cache / 'review.json').exists()
