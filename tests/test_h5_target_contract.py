"""Optional real-importer contract suite; required by the H5 contract CI job.

Only synthetic content is used. No model, DB, business import or network call.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from compile_book import gather
from core.delivery_package import build_delivery_package
from scripts.build_method_validation_samples import build_samples
from scripts.verify_h5_target import verify
from tests.h5_fixtures import compiled_book


@pytest.mark.skipif(not os.environ.get('H5_ZIPIMPORT_SOURCE'), reason='pinned H5 source checkout required')
def test_generated_packages_against_pinned_go_parser(tmp_path):
    root = tmp_path.resolve()
    suite = root / 'suite'
    plan = build_samples(suite)
    cases = [{'package': c['package'], 'accept': c['action'] != 'reject_invalid'} for c in plan['cases']]
    for number, intro in ((512, False), (511, True)):
        book = gather(title='Spine boundary fixture', author='Fixture', genre='', chapters=[
            {'number': i, 'title': f'Chapter {i}', 'text': (
                '## STORY_LEAD: Introduction\n\nIntroduction.\n\n' if intro and i == 1 else '')
                + f'# Chapter {i}\n\nOne choice changes the next choice.'}
            for i in range(1, number + 1)])
        project = root / f'boundary-{number}'
        compiled_book(project, book=book)
        package = build_delivery_package(project)
        cases.append({'package': str(package.archive_path), 'accept': True})
    # Actual poster embedding must not add a cover/title page to the spine.
    from tests.test_delivery_package import _payloads, _cover_set
    poster = root / 'poster'
    _payloads(poster, selected=True)
    package = build_delivery_package(poster, cover_set=replace(_cover_set(selected=True), compiler_version='skeleton-poster.v1'))
    cases.append({'package': str(package.archive_path), 'accept': True})
    casefile = root / 'target-cases.json'
    casefile.write_text(json.dumps(cases))
    results = verify(Path(os.environ['H5_ZIPIMPORT_SOURCE']), casefile)
    (root / 'target-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    assert all(r['matched'] for r in results), [r for r in results if not r['matched']]
