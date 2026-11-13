"""Cross-seam recovery of legacy model-design quotations, without repurchase."""
from copy import deepcopy
import json

import pytest

from core.outline_planning import OutlinePlanningError, _json, _parse_object, content_hash
from core.planning_roadmap import ROADMAP_PROMPT, _Roadmap
from test_outline_planning import make_planner
from test_planning_roadmap import STORIES, StoryModel


NAME = 'roadmap-v1-001-008'
NUMBERS = list(range(1, 9))
DESIGN_RULES = [
    'Preserve the witness uncertainty until the planned final confrontation.',
    'The early act must establish attachment without claiming prior absolution.',
]


class NoNewModel:
    provider = 'fake'
    model = 'roadmap-fixture'

    def __init__(self):
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError('A saved complete roadmap must not be regenerated')


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def setup_roadmap(tmp_path):
    story = STORIES[0]
    model = NoNewModel()
    planner = make_planner(tmp_path, model, count=60, source=story.source(60))
    context = {**planner._context(), 'volume_contract_template': planner.volume_template}
    fixture_model = StoryModel(story)
    seed = planner._global_seed(_parse_object(fixture_model.complete('',
        planner.global_seed_prompt + '\n\n' + _json(context))))
    seed['opening_investment'] = {'protected_long_range_discoveries': list(DESIGN_RULES)}
    roadmap = _Roadmap(planner, deepcopy(seed), provisional=False)
    context = roadmap._context(NUMBERS)
    valid = _parse_object(fixture_model.complete('', ROADMAP_PROMPT + '\n\n' + _json(context)))
    return planner, model, seed, roadmap, context, valid


def derived_quotes(candidate):
    value = deepcopy(candidate)
    for index, text in enumerate(DESIGN_RULES):
        identifier = f'legacy_design_rule_{index + 1}'
        value['source_anchors'].append({'id': identifier, 'quote': text})
        # One derived plan is cited from several chapters. All uses must move
        # together; silently dropping one use would change the chapter plan.
        for row in value['chapter_map'][index:index + 4]:
            row['source_anchor_ids'].append(identifier)
    return value


def legacy_correction_prompt(context, raw, error):
    # Frozen pre-feature transport, intentionally independent of new v2 rules.
    return (
        'STAGE: format_correction\nCorrect only the reported JSON/schema defect in the completed response. '
        'Preserve every already valid story choice, source detail, scene, emotion, character, and obligation. '
        'Do not restart or redesign the book, recalculate immutable contracts, or omit sections to make JSON shorter. '
        'Return one complete corrected object for this SAME bounded stage. Use the original source context only '
        'to supply a missing required field; do not invent a contradictory fact. This is the sole automatic format correction.\n\n'
        + _json({'original_instruction': ROADMAP_PROMPT, 'original_context': context,
                 'completed_response': raw, 'local_error': error})
    )


def save_legacy_responses(planner, roadmap, context, original, correction, *, local_error=None):
    raw = json.dumps(original, ensure_ascii=False, indent=2)
    prompt = ROADMAP_PROMPT + '\n\n' + _json(context)
    if local_error is None:
        try:
            roadmap._validate(deepcopy(original), NUMBERS)
        except (ValueError, KeyError, TypeError) as exc:
            local_error = str(exc)
        else:
            local_error = 'Historical validation requested a bounded correction'
    correction_raw = json.dumps(correction, ensure_ascii=False, indent=2)
    correction_prompt = legacy_correction_prompt(context, raw, local_error)
    for name, request, response in ((NAME, prompt, raw),
                                     (NAME + '-format-correction', correction_prompt, correction_raw)):
        planner._save(name + '-response', {'prompt_hash': content_hash(request)}, {'raw': response})
        directory = planner.cache / 'raw'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + '.legacy.prompt.txt')).write_text(request)
        (directory / (name + '.legacy.response.txt')).write_text(response)


def child_record(planner):
    return json.loads((planner.cache / (NAME + '-design-reference-recovery-v1.json')).read_text())


def assert_preserved(root, before):
    assert all((root / relative).read_bytes() == raw for relative, raw in before.items())


def test_legacy_exact_seed_quotes_move_all_references_and_replay_without_model(tmp_path):
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    original = derived_quotes(valid)
    latest = deepcopy(original)
    latest['chapter_map'][0]['purpose'] = 'The latest issued correction preserves the complete scene choice.'
    save_legacy_responses(planner, roadmap, context, original, latest)
    before = snapshot(planner.cache)

    roadmap.add(NUMBERS)

    assert model.calls == []
    assert_preserved(planner.cache, before)
    recovered = child_record(planner)['payload']
    child = recovered['candidate']
    assert child['source_anchors'] == valid['source_anchors']
    by_text = {entry['text']: entry['id'] for entry in recovered['design_references']['entries']}
    for index, row in enumerate(child['chapter_map']):
        expected = [by_text[text] for start, text in enumerate(DESIGN_RULES) if start <= index < start + 4]
        assert row.get('design_reference_ids', []) == expected
        assert row['source_anchor_ids'] == valid['chapter_map'][index]['source_anchor_ids']
        protected = {key: value for key, value in row.items()
                     if key not in {'source_anchor_ids', 'design_reference_ids'}}
        assert protected == {key: value for key, value in latest['chapter_map'][index].items()
                             if key != 'source_anchor_ids'}
    assert len(recovered['mappings']) == 2
    after = snapshot(planner.cache)
    resumed = _Roadmap(planner, deepcopy(seed), provisional=False)
    resumed.add(NUMBERS)
    assert resumed.rows == roadmap.rows
    assert model.calls == []
    assert_preserved(planner.cache, after)


@pytest.mark.parametrize('bad_kind', ['unrelated', 'near_match', 'ambiguous_seed_leaf'])
def test_unprovable_quote_is_not_reclassified_or_reissued(tmp_path, bad_kind):
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    if bad_kind == 'ambiguous_seed_leaf':
        seed['opening_investment']['separate_generated_rule'] = DESIGN_RULES[0]
        roadmap = _Roadmap(planner, deepcopy(seed), provisional=False)
        context = roadmap._context(NUMBERS)
    invalid = derived_quotes(valid)
    if bad_kind == 'unrelated':
        invalid['source_anchors'][-2]['quote'] = 'A claim absent from both original source and frozen design.'
    elif bad_kind == 'near_match':
        invalid['source_anchors'][-2]['quote'] += ' '
    save_legacy_responses(planner, roadmap, context, invalid, invalid)
    before = snapshot(planner.cache)
    with pytest.raises(OutlinePlanningError, match='exact quotation'):
        roadmap.add(NUMBERS)
    assert model.calls == []
    assert_preserved(planner.cache, before)
    assert not (planner.cache / (NAME + '-design-reference-recovery-v1.json')).exists()


def test_later_issued_correction_wins_even_if_original_is_now_valid(tmp_path):
    planner, model, seed, roadmap, context, original = setup_roadmap(tmp_path)
    latest = derived_quotes(original)
    latest['chapter_map'][0]['purpose'] = 'Only the latest issued correction is authoritative.'
    save_legacy_responses(planner, roadmap, context, original, latest,
                          local_error='An earlier validator required one bounded correction')
    before = snapshot(planner.cache)
    roadmap.add(NUMBERS)
    assert roadmap.rows[0]['purpose'] == latest['chapter_map'][0]['purpose']
    assert model.calls == []
    assert_preserved(planner.cache, before)


def test_corrupt_later_correction_cannot_fall_back_to_valid_original(tmp_path):
    planner, model, seed, roadmap, context, original = setup_roadmap(tmp_path)
    save_legacy_responses(planner, roadmap, context, original, derived_quotes(original),
                          local_error='An earlier validator required one bounded correction')
    path = planner.cache / (NAME + '-format-correction-response.json')
    record = json.loads(path.read_text())
    record['payload']['raw'] += 'corruption without a new payload hash'
    path.write_text(json.dumps(record))
    before = snapshot(planner.cache)
    with pytest.raises(OutlinePlanningError, match='invalid|hash'):
        roadmap.add(NUMBERS)
    assert model.calls == []
    assert_preserved(planner.cache, before)


def test_existing_exact_author_quotation_keeps_source_provenance(tmp_path):
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    seed['author_text_repeated_as_model_plan'] = valid['source_anchors'][0]['quote']
    roadmap = _Roadmap(planner, deepcopy(seed), provisional=False)
    context = roadmap._context(NUMBERS)
    mixed = derived_quotes(valid)
    save_legacy_responses(planner, roadmap, context, mixed, mixed)
    before = snapshot(planner.cache)
    roadmap.add(NUMBERS)
    child = child_record(planner)['payload']['candidate']
    assert child['source_anchors'] == valid['source_anchors']
    assert all(anchor['quote'] in planner.source for anchor in child['source_anchors'])
    assert_preserved(planner.cache, before)
    assert model.calls == []


class DesignPathModel(StoryModel):
    def complete(self, system, user):
        stage = user.splitlines()[0].removeprefix('STAGE: ')
        value = _parse_object(super().complete(system, user))
        if stage == 'global_design_seed':
            value['opening_investment'] = {'protected_long_range_discoveries': list(DESIGN_RULES)}
        elif stage == 'chapter_roadmap':
            context = json.loads(user.rsplit('\n\n', 1)[1])
            assert 'opening_investment' in context['design_reference_catalog']['eligible_roots']
            assert context['frozen_global_design']['opening_investment']['protected_long_range_discoveries'] == DESIGN_RULES
            # Full design strings are supplied only in their existing story
            # fields, not a duplicate whole-book catalog sent to small models.
            assert 'entries' not in context['design_reference_catalog']
            for row in value['chapter_map']:
                row['design_reference_paths'] = [['opening_investment', 'protected_long_range_discoveries', 0]]
            if context['requested_chapters'][0] > 1:
                assert context['prior_design_references']['entries'][0]['text'] == DESIGN_RULES[0]
        return json.dumps(value)


def test_new_reference_paths_survive_full_planning_review_and_publication(tmp_path):
    story = STORIES[0]
    model = DesignPathModel(story)
    planner = make_planner(tmp_path, model, count=60, source=story.source(60))
    result = planner.run()
    entries = result['design_references']['entries']
    assert len(entries) == 1 and entries[0]['origin'] == 'model_design'
    assert entries[0]['text'] == DESIGN_RULES[0] and entries[0]['text'] not in planner.source
    identifier = entries[0]['id']
    for row in result['chapter_map'] + result['chapters']:
        assert row['design_reference_ids'] == [identifier]
        assert 'design_reference_paths' not in row
        assert identifier not in row['source_anchor_ids']
    assert all(anchor['quote'] in planner.source for anchor in result['source_anchors'])
    assert not any(stage == 'format_correction' for stage, _ in model.calls)
    assert planner._global(deepcopy(result))['design_references'] == result['design_references']
    resumed = make_planner(tmp_path, NoNewModel(), count=60, source=planner.source).run()
    assert resumed == result


@pytest.mark.parametrize('bad', [[['opening_investment', 'missing']], [['source_anchors', 0, 'quote']],
                                [['opening_investment', 'protected_long_range_discoveries', True]],
                                [['opening_investment', 'protected_long_range_discoveries', 0]] * 2])
def test_unknown_metadata_or_duplicate_design_paths_rejected(tmp_path, bad):
    planner, model, seed, roadmap, context, value = setup_roadmap(tmp_path)
    value['chapter_map'][0]['design_reference_paths'] = bad
    with pytest.raises(ValueError, match='unique existing frozen field'):
        roadmap._validate(value, NUMBERS)
    assert model.calls == []


def test_forged_self_consistent_catalog_cannot_replace_frozen_planning_basis(tmp_path):
    from core.planning_design_references import build_catalog, select_catalog
    import hashlib
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    impostor = deepcopy(seed)
    impostor['opening_investment']['protected_long_range_discoveries'][0] = 'A new unsupported author claim'
    catalog = build_catalog(impostor, hashlib.sha256(planner.source.encode()).hexdigest())
    fake = next(e for e in catalog['entries'] if e['text'] == 'A new unsupported author claim')
    value = {'design_references': select_catalog(catalog, [fake['id']]), 'chapter_map': valid['chapter_map']}
    value['chapter_map'][0]['design_reference_ids'] = [fake['id']]
    with pytest.raises(OutlinePlanningError, match='basis is missing'):
        planner._validate_design_references(value)
    assert model.calls == []


def test_corrupt_derived_child_never_replaces_original_or_reissues(tmp_path):
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    candidate = derived_quotes(valid)
    save_legacy_responses(planner, roadmap, context, candidate, candidate)
    roadmap.add(NUMBERS)
    child = planner.cache / (NAME + '-design-reference-recovery-v1.json')
    record = json.loads(child.read_text())
    record['payload']['candidate']['chapter_map'][0]['purpose'] = 'Altered story'
    # An updated checksum alone cannot grant authority to a changed child.
    record['payload_hash'] = content_hash(record['payload'])
    child.write_text(json.dumps(record))
    (planner.cache / (NAME + '.json')).unlink()  # crash before chunk promotion
    before = snapshot(planner.cache)
    with pytest.raises(OutlinePlanningError, match='differs from the original candidate'):
        _Roadmap(planner, seed, provisional=False).add(NUMBERS)
    assert model.calls == []
    assert_preserved(planner.cache, before)


def test_failed_provenance_persistence_does_not_promote_or_repurchase(tmp_path, monkeypatch):
    planner, model, seed, roadmap, context, valid = setup_roadmap(tmp_path)
    candidate = derived_quotes(valid)
    save_legacy_responses(planner, roadmap, context, candidate, candidate)
    save = planner._save
    def fail_child(name, *args, **kwargs):
        if name.endswith('-design-reference-recovery-v1'):
            raise OSError('Offline simulated disk failure')
        return save(name, *args, **kwargs)
    monkeypatch.setattr(planner, '_save', fail_child)
    with pytest.raises(OSError, match='disk failure'):
        roadmap.add(NUMBERS)
    assert not (planner.cache / (NAME + '.json')).exists()
    assert model.calls == []


@pytest.mark.parametrize('state', ['complete', 'pending', 'recovered_on_new_model'])
def test_model_switch_keeps_latest_correction_and_never_skips_pending_correction(tmp_path, state, monkeypatch):
    from core.planning_model_continuation import open_predecessors
    planner, model, seed, roadmap, context, original = setup_roadmap(tmp_path)
    latest = derived_quotes(original)
    latest['chapter_map'][0]['purpose'] = 'Preserve the later correction across model routes.'
    save_legacy_responses(planner, roadmap, context, original, latest)
    planner._write_json(planner.cache / 'identity.json', planner.identity)
    planner._progress('global_design', 'running', 'Saved legacy response')
    if state != 'complete':
        (planner.cache / (NAME + '-format-correction-response.json')).unlink()
        (planner.cache / 'raw' / (NAME + '-format-correction.legacy.response.txt')).unlink()
    before = snapshot(planner.cache)
    switched_model = NoNewModel()
    switched_model.model = 'another-model'
    switched = make_planner(tmp_path, switched_model, count=60, source=planner.source)
    switched.predecessors = open_predecessors(switched)
    switched._write_json(switched.cache / 'identity.json', switched.identity)
    switched_roadmap = _Roadmap(switched, seed, provisional=False)
    # The shared response session has recovered the complete original request;
    # the later correction remains archived under the previous model namespace.
    prompt = ROADMAP_PROMPT + '\n\n' + _json(context)
    raw = json.dumps(original, ensure_ascii=False, indent=2)
    switched._save(NAME + '-response', {'prompt_hash': content_hash(prompt)}, {'raw': raw})
    if state == 'recovered_on_new_model':
        # A received correction can close the same pending request in an older
        # namespace; it does not authorize unrelated or divergent corrections.
        import shutil
        saved_prompt = planner.cache / 'raw' / (NAME + '-format-correction.legacy.prompt.txt')
        target = switched.cache / 'raw' / saved_prompt.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(saved_prompt, target)
        switched._save(NAME + '-format-correction-response',
                       {'prompt_hash': content_hash(saved_prompt.read_text())},
                       {'raw': json.dumps(latest, ensure_ascii=False, indent=2)})
    if state == 'pending':
        response = switched._response
        def pending_request(name, saved_prompt, *args, **kwargs):
            if name.endswith('-format-correction'):
                expected = (planner.cache / 'raw' / (NAME + '-format-correction.legacy.prompt.txt')).read_text()
                assert saved_prompt == expected
                assert kwargs['strict_dependencies'] and kwargs['recover_archived']
                raise TimeoutError('Original correction outcome is still unknown')
            return response(name, saved_prompt, *args, **kwargs)
        monkeypatch.setattr(switched, '_response', pending_request)
        with pytest.raises(TimeoutError, match='outcome is still unknown'):
            switched_roadmap.add(NUMBERS)
        assert not (switched.cache / (NAME + '.json')).exists()
    else:
        switched_roadmap.add(NUMBERS)
        assert switched_roadmap.rows[0]['purpose'] == latest['chapter_map'][0]['purpose']
        assert len(child_record(switched)['payload']['mappings']) == 2
    assert not model.calls and not switched_model.calls
    assert_preserved(planner.cache, before)


def test_design_registry_survives_model_cycle_after_partial_roadmap(tmp_path):
    story = STORIES[0]
    initial_model = DesignPathModel(story, fail_at=9)
    initial_model.model = 'A'
    initial = make_planner(tmp_path, initial_model, count=60, source=story.source(60))
    with pytest.raises(TimeoutError):
        initial.run()
    switched_model = DesignPathModel(story)
    switched_model.model = 'B'
    switched = make_planner(tmp_path, switched_model, count=60, source=initial.source)
    result = switched.run()
    assert not any(stage == 'global_design_seed' or (stage == 'chapter_roadmap' and ctx['requested_chapters'][0] == 1)
                   for stage, ctx in switched_model.calls)
    basis = next(initial.cache.glob('design-reference-basis-*.json'))
    assert json.loads((switched.cache / basis.name).read_text())['inherited_from']['identity_hash'] == initial.identity_hash
    cycled_model = DesignPathModel(story)
    cycled_model.model = 'A'
    cycled = make_planner(tmp_path, cycled_model, count=60, source=initial.source)
    assert cycled.run() == result
    assert not any(stage in {'global_design_seed', 'chapter_roadmap', 'batch'} for stage, _ in cycled_model.calls)


def test_pending_correction_recovers_exact_request_instead_of_approving_old_reply(tmp_path, monkeypatch):
    planner, model, seed, roadmap, context, original = setup_roadmap(tmp_path)
    latest = derived_quotes(original)
    latest['chapter_map'][0]['purpose'] = 'The resumed correction controls the retained story.'
    save_legacy_responses(planner, roadmap, context, original, latest)
    latest_raw = (planner.cache / 'raw' / (NAME + '-format-correction.legacy.response.txt')).read_text()
    (planner.cache / (NAME + '-format-correction-response.json')).unlink()
    (planner.cache / 'raw' / (NAME + '-format-correction.legacy.response.txt')).unlink()
    original_response = planner._response
    recovered = []
    def recover(name, prompt, *args, **kwargs):
        if name.endswith('-format-correction'):
            assert prompt == (planner.cache / 'raw' / (name + '.legacy.prompt.txt')).read_text()
            recovered.append(name)
            return latest_raw  # Shared request session returns a verified completed reply.
        return original_response(name, prompt, *args, **kwargs)
    monkeypatch.setattr(planner, '_response', recover)
    roadmap.add(NUMBERS)
    assert recovered == [NAME + '-format-correction']
    assert roadmap.rows[0]['purpose'] == latest['chapter_map'][0]['purpose']
    assert model.calls == []


def test_publication_cannot_drop_design_provenance(tmp_path):
    story = STORIES[0]
    model = DesignPathModel(story)
    def drop_registry(candidate):
        candidate.pop('design_references')
        return candidate
    planner = make_planner(tmp_path, model, count=60, source=story.source(60), final_validator=drop_registry)
    with pytest.raises(OutlinePlanningError, match='changed generated design provenance'):
        planner.run()
