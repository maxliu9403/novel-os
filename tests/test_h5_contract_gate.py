"""Boundary, corruption and publication-transaction tests against real bytes."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image
import pytest

from core.h5_contract import (PROFILE, ContractError, checked_archive, strict_json,
                              xml_document, verify_image, safe_path)
from core.h5_acceptance import inspect_package
from core.delivery_package import build_delivery_package
from core.cover_delivery import read_current_package, DeliveryUnavailable, delivery_status
from core.h5_import import epub_index
from core.novel_classification import catalog_payload
from tests.h5_fixtures import compiled_book
from compile_book import gather
from compile_epub import render_epub
from styles import StyleSheet


def zipped(files):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return output.getvalue()


def test_target_dictionary_and_limits_are_pinned_to_the_handoff():
    raw = (Path(__file__).parents[1] / 'docs/examples/h5-import/novel-classification-catalog.json').read_bytes()
    assert sha256(raw).hexdigest() == PROFILE.catalog_sha256
    assert json.loads(raw) == catalog_payload()
    lock = json.loads((Path(__file__).parents[1] / 'resources/h5-target/validator.lock.json').read_text())
    assert lock['revision'] == PROFILE.validator_revision
    assert lock['files']['classification-catalog.json'] == PROFILE.catalog_sha256
    assert PROFILE.zip_bytes == 67_108_864 and PROFILE.epub_bytes == 16_777_216
    assert PROFILE.spine_items == 512 and PROFILE.cover_candidates == 24


@pytest.mark.parametrize('path', ['/book.epub', '../book.epub', 'a/../b', 'a//b', 'C:a', 'a%2fb', 'a?b', 'a#b', 'a\\b', './a', 'a\u0085b'])
def test_reject_ambiguous_paths(path):
    assert not safe_path(path)
    with pytest.raises(ContractError, match='unsafe_zip_path'):
        with checked_archive(zipped({path: b'text'})):
            pytest.fail('unsafe path accepted')


def test_archive_compressed_expanded_and_member_limits_are_independent():
    raw = zipped({'one': b'x' * 20, 'two': b'y' * 20})
    exact = replace(PROFILE, zip_bytes=len(raw), zip_expanded=40, zip_entries=2)
    with checked_archive(raw, profile=exact):
        pass
    for changed, code in [(replace(exact, zip_bytes=len(raw) - 1), 'archive_bytes'),
                          (replace(exact, zip_expanded=39), 'archive_expanded'),
                          (replace(exact, zip_entries=1), 'archive_entries')]:
        with pytest.raises(ContractError, match=code):
            with checked_archive(raw, profile=changed):
                pass
    with pytest.raises(ContractError, match='epub_expanded'):
        with checked_archive(raw, epub=True, profile=replace(exact, epub_expanded=39)):
            pass


def test_unsupported_zip_compression_and_file_parent_collision():
    from zipfile import ZIP_BZIP2
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_BZIP2) as archive:
        archive.writestr('member', b'contents')
    with pytest.raises(ContractError, match='zip_compression'):
        with checked_archive(output.getvalue()):
            pass
    with pytest.raises(ContractError, match='zip_path_collision'):
        with checked_archive(zipped({'book': b'file', 'book/chapter': b'collision'})):
            pass


@pytest.mark.parametrize('compression', [0, ZIP_DEFLATED])
def test_actual_decoded_size_is_checked_even_when_declared_crc_matches_prefix(compression):
    from struct import pack_into
    from zlib import crc32
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=compression) as archive:
        archive.writestr('member', b'abcdef')
    raw = bytearray(stream.getvalue())
    central = raw.index(b'PK\x01\x02')
    for position, value in ((14, crc32(b'abc')), (22, 3), (central + 16, crc32(b'abc')), (central + 24, 3)):
        pack_into('<I', raw, position, value)
    # Python's ordinary reader returns the prefix and reports its CRC as valid.
    with ZipFile(BytesIO(raw)) as archive:
        assert archive.read('member') == b'abc'
        assert archive.testzip() is None
    with pytest.raises(ContractError, match='zip_member_size'):
        with checked_archive(bytes(raw)):
            pass


def test_json_byte_depth_duplicate_and_nonfinite_checks():
    assert strict_json('[' * 64 + '0' + ']' * 64)
    for raw, code in [('[' * 65 + '0' + ']' * 65, 'json_depth'),
                      ('{"id":1,"id":2}', 'duplicate_json_key'),
                      ('{"n":1e9999}', 'nonfinite_json'), ('{"n":NaN}', 'nonfinite_json')]:
        with pytest.raises(ContractError, match=code):
            strict_json(raw)
    with pytest.raises(ContractError, match='json_bytes'):
        strict_json(b'"123"', profile=replace(PROFILE, document_bytes=4))
    assert strict_json('"[[[\\\"{]}"') == '[[["{]}'


def test_xml_depth_and_dtd_before_expansion():
    assert xml_document(('<x>' * 128 + '</x>' * 128).encode()) is not None
    for raw, code in [(('<x>' * 129 + '</x>' * 129).encode(), 'xml_depth'),
                      (b'<!DOCTYPE x [<!ENTITY y "expansion">]><x>&y;</x>', 'xml_dtd')]:
        with pytest.raises(ContractError, match=code):
            xml_document(raw)


def test_image_real_decode_mime_dimensions_and_limits():
    stream = BytesIO()
    Image.new('RGB', (20, 30), 'navy').save(stream, 'PNG')
    raw = stream.getvalue()
    exact = replace(PROFILE, cover_bytes=len(raw), cover_dimension=30, cover_pixels=600)
    assert verify_image(raw, 'image/png', 'cover.png', profile=exact) == (20, 30)
    for profile, code in [(replace(exact, cover_bytes=len(raw)-1), 'cover_bytes'),
                          (replace(exact, cover_dimension=29), 'cover_dimensions'),
                          (replace(exact, cover_pixels=599), 'cover_pixels')]:
        with pytest.raises(ContractError, match=code):
            verify_image(raw, 'image/png', 'cover.png', profile=profile)
    for data, mime, code in [(raw, 'image/jpeg', 'cover_media_type'),
                             (raw, 'image/webp', 'cover_media_type'),
                             (raw[:50], 'image/png', 'invalid_cover')]:
        with pytest.raises(ContractError, match=code):
            verify_image(data, mime, 'cover.png')


@pytest.mark.parametrize('count,introduction,accepted', [(512, False, True), (511, True, True), (512, True, False)])
def test_real_spine_boundary_includes_introduction(count, introduction, accepted):
    book = gather(title='Boundary', author='Fixture', genre='', chapters=[
        {'number': n, 'title': f'Chapter {n}', 'text': (
            '## STORY_LEAD: Introduction\n\nA choice matters.\n\n' if introduction and n == 1 else '')
            + f'# Chapter {n}\n\nA decision changes the plan.'}
        for n in range(1, count + 1)])
    if not accepted:
        with pytest.raises(ValueError, match='epub_spine'):
            render_epub(book, StyleSheet())
    else:
        index = epub_index(render_epub(book, StyleSheet()), None, None)
        assert index['chapter_count'] == count
        assert len(index['non_chapter_items']) == int(introduction)


def test_intro_outside_first_spine_position_is_rejected():
    book = gather(title='Intro test', author='', genre='', chapters=[
        {'number': 1, 'title': 'One', 'text': '## STORY_LEAD: Introduction\n\nIntro.\n\n# Chapter 1\n\nStory.'}])
    with ZipFile(BytesIO(render_epub(book, StyleSheet()))) as epub:
        files = {n: epub.read(n) for n in epub.namelist()}
    opf = ET.fromstring(files['OEBPS/content.opf'])
    spine = opf.find('{*}spine')
    spine[:] = list(reversed(spine))
    files['OEBPS/content.opf'] = ET.tostring(opf)
    with pytest.raises(ValueError, match='introduction_position'):
        epub_index(zipped(files), None, None)


def test_invalid_rebuild_keeps_all_published_metadata_and_archive(tmp_path):
    compiled_book(tmp_path)
    first = build_delivery_package(tmp_path)
    before = {p: p.read_bytes() for p in [first.archive_path, first.manifest_path,
              tmp_path / 'outputs/deliverables/meta/h5-import.json']}
    epub = tmp_path / 'outputs/deliverables/book.epub'
    epub.write_bytes(b'broken EPUB')
    with pytest.raises(ValueError):
        build_delivery_package(tmp_path)
    assert all(p.read_bytes() == data for p, data in before.items())
    assert delivery_status(tmp_path)['status'] == 'failed'
    with pytest.raises(DeliveryUnavailable):
        read_current_package(tmp_path)
    assert not list((tmp_path / 'outputs').glob('.delivery-*'))


def test_atomic_zip_publish_failure_keeps_previous_zip_and_can_retry(tmp_path, monkeypatch):
    from core import delivery_package
    compiled_book(tmp_path)
    archive = build_delivery_package(tmp_path).archive_path
    previous = archive.read_bytes()
    state = tmp_path / 'outputs/state/story_state.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'metadata': {'author': 'Updated Author'}}))
    original = delivery_package.os.replace

    def failed_publish(source, destination):
        if Path(destination) == archive:
            raise OSError('injected final publish failure')
        return original(source, destination)

    monkeypatch.setattr(delivery_package.os, 'replace', failed_publish)
    with pytest.raises(OSError, match='injected final publish failure'):
        build_delivery_package(tmp_path)
    assert archive.read_bytes() == previous
    with pytest.raises(DeliveryUnavailable):
        read_current_package(tmp_path)
    monkeypatch.setattr(delivery_package.os, 'replace', original)
    rebuilt = build_delivery_package(tmp_path)
    assert read_current_package(tmp_path) == rebuilt.archive_path.read_bytes()
    assert inspect_package(archive)['author'] == 'Updated Author'


def test_source_change_during_assembly_cannot_publish(tmp_path, monkeypatch):
    compiled_book(tmp_path)
    first = build_delivery_package(tmp_path).archive_path.read_bytes()
    from core import delivery_package
    original = delivery_package._write_archive

    def changed(*args):
        original(*args)
        (tmp_path / 'outputs/deliverables/book.epub').write_bytes(b'concurrent replacement')

    monkeypatch.setattr(delivery_package, '_write_archive', changed)
    with pytest.raises(ContractError, match='delivery_source_changed'):
        build_delivery_package(tmp_path)
    assert (tmp_path / 'outputs/deliverables/book-package.zip').read_bytes() == first


def test_assembly_reads_frozen_author_even_if_live_metadata_changes_and_returns(tmp_path, monkeypatch):
    from core import delivery_package
    compiled_book(tmp_path)
    state = tmp_path / 'outputs/state/story_state.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    original_state = json.dumps({'metadata': {'author': 'Approved Author'}}).encode()
    state.write_bytes(original_state)
    assemble = delivery_package._assemble_delivery_package

    def concurrent_metadata(*args, **kwargs):
        state.write_text(json.dumps({'metadata': {'author': 'Concurrent Author'}}))
        try:
            return assemble(*args, **kwargs)
        finally:
            state.write_bytes(original_state)

    monkeypatch.setattr(delivery_package, '_assemble_delivery_package', concurrent_metadata)
    result = build_delivery_package(tmp_path)
    assert inspect_package(result.archive_path)['author'] == 'Approved Author'


def test_missing_receipt_and_changed_source_cannot_be_downloaded(tmp_path):
    compiled_book(tmp_path)
    result = build_delivery_package(tmp_path)
    assert read_current_package(tmp_path) == result.archive_path.read_bytes()
    (tmp_path / 'outputs/deliverables/book.epub').write_bytes(b'new manuscript')
    with pytest.raises(DeliveryUnavailable, match='delivery_source_changed'):
        read_current_package(tmp_path)
    (tmp_path / 'outputs/publication/export-validation.json').unlink()
    with pytest.raises(DeliveryUnavailable, match='delivery_validation_missing'):
        read_current_package(tmp_path)


def test_package_does_not_contain_internal_receipt_or_private_reports(tmp_path):
    compiled_book(tmp_path)
    hidden = tmp_path / 'outputs/deliverables/quality/private.json'
    hidden.parent.mkdir()
    hidden.write_text('{"raw_response":"private"}')
    result = build_delivery_package(tmp_path)
    assert inspect_package(result.archive_path)['engine_contract_status'] == 'passed'
    with ZipFile(result.archive_path) as archive:
        assert not any('validation' in p or 'private' in p for p in archive.namelist())


def test_changed_approved_manuscript_requires_compile_before_repackaging(tmp_path):
    from core.compiled_source import COMPILED_SOURCE, source_digest
    compiled_book(tmp_path)
    chapter = 'outputs/manuscript/chapter_001_final.md'
    source = tmp_path / chapter
    source.parent.mkdir(parents=True)
    source.write_text('Approved chapter')
    with pytest.raises(ValueError, match='compile_source_missing'):
        build_delivery_package(tmp_path)
    index = epub_index((tmp_path / 'outputs/deliverables/book.epub').read_bytes(), None, None)
    marker = tmp_path / COMPILED_SOURCE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'schema_version': 'novel-compiled-source.v1',
        'sources': {chapter: source_digest(tmp_path, chapter)}, 'chapter_count': 1,
        'content_sha256': index['content_sha256'], 'front_matter_sha256': index['front_matter_sha256']}))
    published = build_delivery_package(tmp_path).archive_path.read_bytes()
    source.write_text('A newly approved chapter')
    with pytest.raises(DeliveryUnavailable, match='delivery_source_changed'):
        read_current_package(tmp_path)
    with pytest.raises(ValueError, match='compile_source_changed'):
        build_delivery_package(tmp_path)
    assert (tmp_path / 'outputs/deliverables/book-package.zip').read_bytes() == published


def test_source_change_between_status_and_download_returns_stale(tmp_path, monkeypatch):
    import core.cover_delivery as delivery
    compiled_book(tmp_path)
    build_delivery_package(tmp_path)
    original = delivery.delivery_status

    def changed(project):
        status = original(project)
        (project / 'outputs/deliverables/book-package.zip').unlink()
        return status

    monkeypatch.setattr(delivery, 'delivery_status', changed)
    with pytest.raises(DeliveryUnavailable) as error:
        read_current_package(tmp_path)
    assert error.value.status['status'] == 'stale'


@pytest.mark.parametrize('author', ['a' * 129, '张' * 43, 'Name\u0085Line', 'Name\nLine'])
def test_h5_author_boundary_is_enforced_without_truncation(tmp_path, author):
    compiled_book(tmp_path)
    state = tmp_path / 'outputs/state/story_state.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'metadata': {'author': author}}))
    with pytest.raises(ValueError, match='author_contract'):
        build_delivery_package(tmp_path)
    assert not (tmp_path / 'outputs/deliverables/book-package.zip').exists()


def test_unpersisted_cover_revision_is_rejected(tmp_path):
    from tests.test_delivery_package import _payloads, _cover_set
    _payloads(tmp_path)
    with pytest.raises(ValueError, match='cover_metadata_identity'):
        build_delivery_package(tmp_path, cover_set=replace(_cover_set(), revision=0))


@pytest.mark.parametrize('canonical,introduction', [(False, False), (True, False), (True, True)])
def test_legacy_rebuild_proves_approved_prose_before_adopting_a_binding(tmp_path, canonical, introduction):
    from core.compiled_source import COMPILED_SOURCE
    text = ('## STORY_LEAD: Introduction\n\nA difficult choice.\n\n' if introduction else '') + '# A choice\n\nTest manuscript'
    compiled_book(tmp_path, marker=text)
    state = tmp_path / 'outputs/state/story_state.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'metadata': {'title': 'Test publication', 'author': 'Fixture'},
                                'chapters': {'1': {'number': 1, 'title': 'A choice'}}}))
    chapter = tmp_path / 'outputs/manuscript/chapter_001_final.md'
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text(text)
    if canonical:
        from artifacts import ArtifactStore
        store = ArtifactStore(tmp_path)
        final = store.put_text(chapter=1, kind='final', text=text, source='test')
        store.set_head(1, 'final', final.revision_id, expected_revision_id=None)
        chapter.write_text('An outdated materialized file must never override the canonical Final.')
    before = (tmp_path / 'outputs/deliverables/book.epub').read_bytes()
    result = build_delivery_package(tmp_path)
    assert (tmp_path / COMPILED_SOURCE).is_file()
    assert read_current_package(tmp_path) == result.archive_path.read_bytes()
    assert (tmp_path / 'outputs/deliverables/book.epub').read_bytes() == before
    # A subsequent source edit cannot be certified by simply rebuilding the ZIP.
    if canonical:
        changed = store.put_text(chapter=1, kind='final', text='A different approved ending.', source='test')
        store.set_head(1, 'final', changed.revision_id, expected_revision_id=final.revision_id)
    else:
        chapter.write_text('A different approved ending.')
    with pytest.raises(ValueError, match='compile_source_changed'):
        build_delivery_package(tmp_path)
    (tmp_path / COMPILED_SOURCE).unlink()
    with pytest.raises(ValueError, match='compiled_epub_changed'):
        build_delivery_package(tmp_path)
    assert not (tmp_path / COMPILED_SOURCE).exists()
