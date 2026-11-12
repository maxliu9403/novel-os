"""Naming consumes its scoped basis in one call without changing frozen guidance."""
from copy import deepcopy
import json

import pytest

from api import workshop_titles


@pytest.mark.parametrize('kind', ['reference_route', 'story_overview', 'legacy_skeleton'])
def test_scoped_naming_uses_one_request_and_preserves_author_prompt(monkeypatch, kind):
    from core import llm_client, provider_settings
    context = {
        'basis_kind': kind,
        'sources': [{'id': 'story', 'purpose': 'story_facts',
                     'text': 'A former partner returns; the detective must decide whether to trust him.'}],
        'story_updates': [{'text': 'They separate permanently; do not promise reconciliation.'}],
        'author_requests': [{'instructions': 'Use plain English.'}],
    }
    frozen = {'version': 'author-custom', 'sha256': 'a' * 64,
              'markdown': 'AUTHOR CUSTOM GUIDANCE: prioritize her difficult choice.'}
    session = {'turn_timeout_seconds': 900, 'prompt_md': 'Unsent full skeleton.',
               'naming': {'requests': [{'system_prompt': frozen}]}}
    before = deepcopy(session)
    projection = {'context': context, 'context_bytes': len(json.dumps(context).encode()),
                  'skeleton_bytes': 250000}
    monkeypatch.setattr(workshop_titles, 'request_context', lambda _: projection)
    monkeypatch.setattr(provider_settings, 'resolve_text_route', lambda _: {
        'provider': 'stub', 'model': 'naming', 'base_url': '', 'api_key': '', 'max_tokens': 4096})
    calls = []
    candidates = [{'title': f'The Choice She Made {index}', 'angle': '决定与代价',
                   'reason': '以人物选择承接作者要求，不承诺复合。', 'source_ids': ['story']}
                  for index in range(5)]

    class Client:
        def __init__(self, **_):
            pass

        def complete(self, system, user):
            calls.append((system, json.loads(user)))
            return json.dumps({'candidates': candidates})

    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    result, metrics = workshop_titles.complete_titles(session, frozen['markdown'])
    assert result == candidates and len(calls) == 1
    system, supplied = calls[0]
    assert supplied == context
    assert frozen['markdown'] in system
    assert 'Unsent full skeleton.' not in system + json.dumps(supplied)
    assert 'story_updates override conflicting historical route details' in system
    assert 'Do not request the full skeleton' in system
    assert 'a private spoiler' in system
    assert session == before
    assert metrics['prompt_version'] == frozen['version']
    assert metrics['attempts'] == 1
