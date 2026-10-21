"""Create synthetic compile/package fixtures in a NEW directory; no models."""
import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from compile_book import gather, render_markdown
from compile_epub import render_epub
from delivery_package import build_delivery_package
from h5_acceptance import build_acceptance_plan, inspect_package
from h5_import import digest, object_digest
from narrative_format import infer_narrative_format, serialization_payload, volume_contract_template
from novel_classification import infer_classification
from scripts.build_h5_handoff_samples import create_cover_set
from styles import StyleSheet
from method_experiments.store import assert_isolated_destination


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def _book(project, count, volume_count):
    classification = replace(infer_classification(genre="Women's Fiction / Family / Revenge", audience='Western women',
                                                  tone='angst', chapters=count),
                             length_form='long' if volume_count > 1 else 'short')
    form = infer_narrative_format(classification, chapters=count, target_words=count * 850, explicit_length=True,
                                 mode='multi_volume' if volume_count > 1 else 'short_novel',
                                 volume_count=volume_count).with_confirmation('author_selected')
    volumes = volume_contract_template(form)
    for volume in volumes:
        n = volume['volume_number']
        volume.update(title=f'Volume {n}: Fixture', central_conflict=f'Fixture conflict {n}', volume_promise=f'Fixture promise {n}',
                      protagonist_shift=f'Fixture choice {n}', payoff=f'Fixture payoff {n}',
                      carryover_hook=f'Fixture carryover {n}' if n < volume_count else '')
    serial = serialization_payload(form, volumes)
    chapters = [{'number': n, 'title': f'Chapter {n}', 'text': (
        '## STORY_LEAD: Introduction\n\nTechnical fixture only. A woman finds a letter and chooses to answer it herself.\n\n'
        if n == 1 else '') + f'# Chapter {n}\n\nFixture chapter {n}: Ada answers the letter. Her decision has a consequence. 🌿'
                } for n in range(1, count + 1)]
    cover = create_cover_set(project, 'Method Validation: Technical Fixture')
    selected = cover.candidates[0]
    cover = replace(cover, status='selected', selected_candidate_id=selected.candidate_id,
                    candidates=tuple(replace(c, status='selected' if c == selected else 'ready') for c in cover.candidates))
    shutil.copyfile(project / selected.relative_path, project / 'outputs/deliverables/covers/selected-cover.png')
    return classification, form, serial, chapters, cover


def _compile(project, classification, serial, chapters, cover):
    # Technical source fixtures, not a claim of production promotion approval.
    sources = project / 'outputs/manuscript'
    sources.mkdir(parents=True, exist_ok=True)
    for chapter in chapters:
        (sources / f"chapter_{chapter['number']:03d}_final.md").write_text(chapter['text'], encoding='utf-8')
    gathered = [{**c, 'text': (sources / f"chapter_{c['number']:03d}_final.md").read_text(encoding='utf-8')} for c in chapters]
    book = gather(title='Method Validation: Technical Fixture', author='Novel OS integration fixture',
                  genre="Women's Fiction", chapters=gathered, classification=classification.to_dict(), serialization=serial)
    deliverables = project / 'outputs/deliverables'
    deliverables.mkdir(parents=True, exist_ok=True)
    (deliverables / 'book.epub').write_bytes(render_epub(book, StyleSheet()))
    (deliverables / 'book.md').write_text(render_markdown(book, StyleSheet()), encoding='utf-8')
    _json(project / 'outputs/publication/novel-classification.json', classification.to_dict())
    _json(project / 'outputs/publication/novel-serialization.json', serial)
    return build_delivery_package(project, cover_set=cover).archive_path


def _corrupt(source, target, kind):
    with zipfile.ZipFile(source) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(files['package-manifest.json'])
    if kind == 'epub_media_type':
        next(e for e in manifest['files'] if e['path'] == 'book.epub')['media_type'] = 'application/octet-stream'
    elif kind == 'file_digest':
        files['book.md'] += b'corrupt'
    elif kind == 'chapter_mapping':
        data = json.loads(files['meta/h5-import.json'])
        data['chapters'][0]['number'] = 2
        files['meta/h5-import.json'] = json.dumps(data).encode()
        entry = next(e for e in manifest['files'] if e['path'] == 'meta/h5-import.json')
        entry.update(sha256=digest(files[entry['path']]), size=len(files[entry['path']]))
    manifest['package_revision_sha256'] = object_digest(manifest['files'])
    files['package-manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        if kind == 'duplicate_zip_path':
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr('book.md', b'deliberate duplicate fixture')


def build_samples(destination: Path):
    destination = Path(destination).absolute()
    assert_isolated_destination(destination)
    if destination.exists() or any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('sample destination must be a new, non-symlink directory')
    destination.mkdir(parents=True)
    cases = []
    def add(case_id, action, package, baseline=None, free=3, error=None):
        cases.append({'id': case_id, 'action': action, 'package': package, 'baseline': baseline,
                      'free_trial_end': free, 'expected_error': error})
    # Copy but never regenerate/modify the three established 80-chapter baselines.
    original = ROOT / 'docs/examples/h5-import'
    baseline_names = ['01-introduction-80-chapters-4-volumes.zip', '02-same-book-cover-only-update.zip',
                      '03-different-book-same-structure.zip']
    for name in baseline_names:
        shutil.copyfile(original / name, destination / name)
    add('baseline-80', 'initial', baseline_names[0])
    add('cover-only', 'cover_only', baseline_names[1], baseline_names[0])
    add('repeat-cover', 'repeat', baseline_names[1], baseline_names[1])
    add('different-book', 'different_book', baseline_names[2], baseline_names[0])
    add('history-replay', 'history_replay', baseline_names[0], baseline_names[1])
    with tempfile.TemporaryDirectory(prefix='novel-method-h5-fixtures-') as temporary:
        for count, volumes, name in [(22, 1, 'short-22.zip'), (48, 4, 'long-48.zip')]:
            project = Path(temporary) / name.removesuffix('.zip')
            classification, form, serial, chapters, cover = _book(project, count, volumes)
            archive = _compile(project, classification, serial, chapters, cover)
            shutil.copyfile(archive, destination / name)
            add('short-free-3' if count == 22 else 'long-48', 'initial', name)
            if count == 22:
                add('short-free-4', 'initial', name, free=4)
                continue
            base_chapters = [dict(c) for c in chapters]
            for change, target in [('chapter_update', 'chapter-update.zip'), ('introduction_update', 'intro-update.zip'),
                                   ('classification_update', 'classification-update.zip'), ('volume_title_update', 'volume-title-update.zip')]:
                changed = [dict(c) for c in base_chapters]
                cls, serialization = classification, serial
                if change == 'chapter_update':
                    changed[12]['text'] += '\n\nAda places the answered letter beside the green cup.'
                elif change == 'introduction_update':
                    changed[0]['text'] = changed[0]['text'].replace('Technical fixture only.', 'Revised technical introduction only.')
                elif change == 'classification_update':
                    cls = replace(classification, tone_ids=())
                else:
                    new_volumes = [dict(v) for v in serial['volumes']]
                    new_volumes[0]['title'] += ' Updated'
                    serialization = serialization_payload(form, new_volumes)
                shutil.copyfile(_compile(project, cls, serialization, changed, cover), destination / target)
                add(change.replace('_', '-'), change, target, name)
            # Only private files change; public members and manifest must not move.
            clean = _compile(project, classification, serial, base_chapters, cover).read_bytes()
            _json(project / 'outputs/quality/methods/fixture-report.json', {'private': 'not exported'})
            with_sidecar = build_delivery_package(project, cover_set=cover).archive_path.read_bytes()
            if clean != with_sidecar:
                raise AssertionError('private sidecar changed the public package')
    for error in ('epub_media_type', 'file_digest', 'duplicate_zip_path', 'chapter_mapping'):
        name = f'invalid-{error}.zip'
        _corrupt(destination / 'long-48.zip', destination / name, error)
        add(error, 'reject_invalid', name, 'long-48.zip', error=error)
    _json(destination / 'cases.json', cases)
    absolute = [{**c, 'package': str(destination / c['package']),
                 'baseline': str(destination / c['baseline']) if c['baseline'] else None} for c in cases]
    plan = build_acceptance_plan(absolute)
    _json(destination / 'acceptance-plan.json', plan)
    _json(destination / 'importer-receipt-template.json', {'schema_version': 'novel-h5-acceptance-receipt.v1',
                                                        'plan_sha256': plan['plan_sha256'], 'importer': None, 'results': []})
    _json(destination / 'verification.json', {'engine_contract_status': 'passed', 'external_import_status': 'pending',
        'fixture_scope': 'synthetic_Final_files_through_real_compile_and_package_not_full_pipeline',
        'private_sidecar_package_bytes_unchanged': True, 'case_count': len(cases),
        'archives': [{'name': p.name, 'sha256': digest(p.read_bytes())} for p in sorted(destination.glob('*.zip'))]})
    (destination / 'README.md').write_text(
        '# H5 method-validation fixtures\n\nTechnical fixtures, not publishable fiction. EPUB is the body source; MD is ignored. '
        'The four `invalid-*` ZIPs are intentionally rejected cases. `cases.json` paths are relative to this directory. '
        'Run scripts/h5_acceptance.py plan again after moving the directory, then use the CURRENT H5 importer in a test database. '
        'No external importer has been executed by this builder. Seed reading progress, paid unlocks and a collection before every update case. '
        'Preserve the original three 80-chapter ZIPs; their copies here are byte-identical.\n', encoding='utf-8')
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build_samples(args.output)
    print(json.dumps({'directory': str(args.output.resolve()), 'case_count': len(result['cases']),
                      'external_import_status': 'pending'}, indent=2))
