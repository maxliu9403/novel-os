"""New editorial recommendations do not change old route identity or adoption."""
import json
import pytest
from core.source_routes import RouteContent, LengthRecommendation, parse_response
from core.creative_types import digest
from test_source_route_handoff import route


def test_legacy_route_identity_survives_optional_length_extension():
    old = route('one', 'evidence-1')
    assert parse_response(json.dumps(old), RouteContent) == old
    assert digest(parse_response(json.dumps(old), RouteContent)) == digest(old)


def test_route_length_is_full_preserved_advisory_data():
    content = route('one', 'evidence-1')
    content['length_recommendation'] = dict(total_words=82000, words_per_chapter=4500,
        chapters=21, language='English', count_unit='words',
        reason='Leave space for public consequences and gradual rebuilding of trust.')
    assert parse_response(json.dumps(content), RouteContent) == content
    assert 'confirmed' not in str(content['length_recommendation'])


@pytest.mark.parametrize('change', [dict(total_words=100000), dict(words_per_chapter=0),
    dict(chapters=0), dict(count_unit='characters')])
def test_invalid_lengths_do_not_silently_rewrite_or_reduce_story(change):
    data = dict(total_words=82000, words_per_chapter=4500, chapters=21,
                language='English', count_unit='words', reason='Give concrete turning points room.')
    with pytest.raises(ValueError):
        LengthRecommendation.model_validate({**data, **change})
