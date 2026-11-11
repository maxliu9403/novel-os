"""An optional context failure cannot remove mandatory role uncertainty."""
from types import SimpleNamespace

from api.services import ProjectService
from core.identity_constraints import RULES
from state_manager import StoryState
from story_foundation import apply_story_foundation
from test_identity_constraints import foundation


def test_continue_paragraph_fallback_retains_unknown_identity_evidence(tmp_path, monkeypatch):
    state = StoryState(str(tmp_path / 'book'))
    apply_story_foundation(state, foundation(), 2000)
    captured = []
    def broken(*args, **kwargs):
        raise RuntimeError('Optional context unavailable')
    monkeypatch.setattr('context_pack.build_context_pack', broken)
    client = SimpleNamespace(run_agent=lambda role, prompt: captured.append((role, prompt)) or 'The guard waited.')
    monkeypatch.setattr('api.services.build_orchestrator', lambda path: SimpleNamespace(_get_llm=lambda: client))
    service = SimpleNamespace(
        ensure_chapter=lambda *args: None,
        _stage_paths=lambda *args: {'revised': tmp_path / 'missing-revised.md', 'draft': tmp_path / 'missing-draft.md'},
        get_final_text=lambda *args: '', _load=lambda *args: state,
        _project_dir=lambda *args: tmp_path / 'book', project_path=lambda *args: tmp_path / 'book')
    result = ProjectService.continue_paragraph(service, 'book', 1, 'Continue the conversation.')
    assert result['paragraph'] == 'The guard waited.'
    assert len(captured) == 1 and captured[0][0] == 'scribe'
    assert RULES in captured[0][1]
    for item in foundation()['identity_constraints'][0]['source_evidence']:
        assert item['quote'] in captured[0][1]
