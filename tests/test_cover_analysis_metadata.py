from __future__ import annotations

import json

import pytest

from core.cover_analysis import CoverStoryAnalyzer
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture


def metadata_source(tmp_path, *, header=None, handoff=None):
    project = tmp_path / 'book'
    (project / 'outputs/input').mkdir(parents=True)
    publication = {'title': 'The Door Is Mine', 'author': '', 'language': 'English',
                   'genre': 'contemporary romance', 'target_audience': 'Women aged 50–65',
                   'market_scope': 'United States English-language market',
                   'genre_emotion_profile': {'primary_genre': 'contemporary romance'},
                   'commercial_visual_goal': {'market': 'United States', 'audience_segment': 'Women aged 50–65'}}
    text = header if header is not None else ('# The Door Is Mine\r\n\r\n'
        'Title: The Door Is Mine\r\nAuthor: \r\nLanguage: English\r\n'
        'Genre: contemporary romance\r\nAudience: Women aged 50–65\r\n')
    text += '\r\n## Story\r\nMara refuses Oren the key.\r\n\r\n'
    text += 'COVER_HANDOFF_BEGIN\n' + json.dumps(publication if handoff is None else handoff, ensure_ascii=False) + '\nCOVER_HANDOFF_END\n'
    (project / 'outputs/input/prompt.md').write_bytes(text.encode())
    context = CoverSourceContext(tmp_path / 'covers', 'default:project:book', lambda _: project)
    return context, capture_source(context, CoverSourceSelector('project_input', project_id='book'))


def test_global_uses_frozen_publication_metadata_even_when_story_extraction_excludes_it(tmp_path):
    context, snapshot = metadata_source(tmp_path)
    model = ModelFixture()
    def omit_metadata(request, response):
        if request['phase'] == 'segment' and ('Title:' in request['segment']['text'] or 'COVER_HANDOFF_BEGIN' in request['segment']['text']):
            return {'classification': 'technical', 'reason': 'publication metadata is not a story event', 'claims': [], 'unresolved': []}
        if request['phase'] == 'global':
            for field in ('title', 'author', 'language', 'genre', 'target_audience', 'market_scope'):
                response['brief'][field] = ''
            response['brief']['title'] = 'Title not supplied'
            response['brief']['genre_emotion_profile']['primary_genre'] = ''
            response['brief']['commercial_visual_goal']['market'] = ''
            response['brief']['commercial_visual_goal']['audience_segment'] = ''
        return response
    model.mutate = omit_metadata
    # This fixture intentionally marks isolated metadata blocks technical;
    # preserve its old partition while the packed mixed-source suite covers v2.
    result = CoverStoryAnalyzer(complete=model, model='fixture', max_repair_attempts=0,
                               segmenter_version='cover-segments.v1').analyze(context, snapshot)
    brief = result.brief_payload
    assert brief['language'] == 'English'
    assert brief['title'] == 'The Door Is Mine'
    assert brief['target_audience'] == 'Women aged 50–65'
    assert brief['market_scope'] == 'United States English-language market'
    assert brief['author'] == ''
    assert brief['genre_emotion_profile']['primary_genre'] == 'contemporary romance'
    assert brief['commercial_visual_goal']['market'] == 'United States'
    assert brief['commercial_visual_goal']['audience_segment'] == 'Women aged 50–65'
    global_call = next(call for call in model.calls if call['phase'] == 'global')
    assert global_call['publication_context']['fields']['language'] == 'English'
    assert all(call['publication_context'] == global_call['publication_context']
               for call in model.calls if call['phase'] == 'verify')


def test_publication_spans_are_exact_and_header_overrides_compressed_handoff(tmp_path):
    from core.cover_analysis_metadata import publication_context
    _, snapshot = metadata_source(tmp_path, handoff={'title': 'Older title', 'language': 'English', 'author': 'Old author', 'market_scope': 'US'})
    metadata = publication_context(snapshot)
    assert metadata['fields']['title'] == 'The Door Is Mine'
    assert metadata['fields']['author'] == ''
    assert metadata['fields']['market_scope'] == 'US'
    for item in metadata['sources']:
        assert snapshot.document_bytes(item['document_id'])[item['byte_start']:item['byte_end']] == item['exact_quote'].encode()
    assert any(item['value'] == 'Older title' for item in metadata['sources'])


def test_missing_language_never_defaults_to_english_or_story_text_language(tmp_path):
    from core.cover_analysis_metadata import publication_context, apply_publication_metadata
    _, snapshot = metadata_source(tmp_path, header='# An English title\nAudience: American readers\n', handoff={})
    metadata = publication_context(snapshot)
    assert 'language' not in metadata['fields']
    assert 'language' in metadata['missing_fields']
    for proposed in ('', 'English'):
        with pytest.raises(ValueError, match='Missing source-bound publication metadata:.*language'):
            apply_publication_metadata({'brief': {'language': proposed}}, metadata)


def test_conflicting_primary_headers_fail_instead_of_guessing(tmp_path):
    from core.cover_analysis_metadata import publication_context
    _, snapshot = metadata_source(tmp_path, header='Title: The Door Is Mine\nLanguage: English\nLanguage: French\n', handoff={})
    with pytest.raises(ValueError, match='Conflicting publication metadata.*language'):
        publication_context(snapshot)


def test_metadata_binding_copies_and_supports_normalized_checkpoint(tmp_path):
    from core.cover_analysis_metadata import publication_context, apply_publication_metadata
    _, snapshot = metadata_source(tmp_path)
    original = {'brief_payload': {'language': '', 'core_conflict': 'The key'}, 'characters': [{'name': 'Mara'}]}
    bound = apply_publication_metadata(original, publication_context(snapshot))
    assert bound['brief_payload']['language'] == 'English'
    assert original['brief_payload']['language'] == ''
    assert bound['characters'] == original['characters']


def test_unrelated_nested_titles_and_character_ages_are_not_publication_fields(tmp_path):
    from core.cover_analysis_metadata import publication_context
    _, snapshot = metadata_source(tmp_path, header='# Novel\n', handoff={'series': {'title': 'Series title'}, 'principal_characters': [{'age': 65, 'language': 'French'}]})
    metadata = publication_context(snapshot)
    assert not metadata['fields']


@pytest.mark.parametrize('style', ['json', 'yaml'])
def test_older_explicit_audience_settings_supply_scope_without_reading_research_citations(tmp_path, style):
    from core.cover_analysis_metadata import publication_context
    import yaml
    research = {'audience_research': {'research_status': 'partial',
        'target_audience': {'primary_reader_segment': 'Adult readers'},
        'market_scope': {'release_scope': 'United States only', 'language': 'English'},
        'source_records': [{'title': 'Another book', 'market': 'France', 'language': 'French'}]}}
    body = json.dumps(research) if style == 'json' else yaml.safe_dump(research)
    _, snapshot = metadata_source(tmp_path, header=f'# Skeleton\n\n```{style}\n{body}\n```\n', handoff={})
    result = publication_context(snapshot)
    assert result['fields'] == {'language': 'English', 'target_audience': 'Adult readers', 'market_scope': 'United States only'}
    for item in result['sources']:
        assert snapshot.primary_bytes[item['byte_start']:item['byte_end']] == item['exact_quote'].encode()


def test_missing_author_cannot_be_invented_by_model(tmp_path):
    from core.cover_analysis_metadata import publication_context, apply_publication_metadata
    from tests.test_cover_models_v2 import two_character_fixture
    handoff = two_character_fixture()
    del handoff['author']
    _, snapshot = metadata_source(tmp_path, header='# Skeleton\n', handoff=handoff)
    assert apply_publication_metadata({'brief': {'author': 'Invented Name'}}, publication_context(snapshot))['brief']['author'] == ''


def test_missing_publication_data_fails_before_any_paid_model_request(tmp_path):
    context, snapshot = metadata_source(tmp_path, header='# Skeleton\n', handoff={})
    model = ModelFixture()
    with pytest.raises(ValueError, match='Missing source-bound publication metadata'):
        CoverStoryAnalyzer(complete=model, model='fixture').analyze(context, snapshot)
    assert model.calls == []
