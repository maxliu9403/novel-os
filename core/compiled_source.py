"""Bind a compiled EPUB to the exact approved manuscript inputs."""
from hashlib import sha256
from pathlib import Path
import re

try:
    from .h5_contract import PROFILE, ContractError, require, strict_json, safe_path, checked_archive, xml_document
    from .safe_project_io import read_project_bytes
except ImportError:
    from h5_contract import PROFILE, ContractError, require, strict_json, safe_path, checked_archive, xml_document
    from safe_project_io import read_project_bytes

COMPILED_SOURCE = 'outputs/publication/compiled-source.json'


def source_digest(project: Path, relative: str) -> str | None:
    require(safe_path(relative) and relative.startswith('outputs/'), 'compile_source_path', path=relative)
    raw = read_project_bytes(project, tuple(relative.split('/')), missing_ok=True, max_bytes=PROFILE.zip_expanded)
    return sha256(raw).hexdigest() if raw is not None else None


def verify_sources(project: Path, sources: dict) -> None:
    require(isinstance(sources, dict) and bool(sources), 'compile_source_contract')
    for relative, expected in sources.items():
        require(source_digest(project, relative) == expected, 'compile_source_changed', path=relative)


def compilation_binding(project: Path) -> dict | None:
    raw = read_project_bytes(project, tuple(COMPILED_SOURCE.split('/')), missing_ok=True,
                             max_bytes=PROFILE.document_bytes)
    if raw is None:
        has_manuscript = (project / 'outputs/artifacts/heads.json').exists() or any(
            (project / 'outputs/manuscript').glob('*_final.md'))
        if has_manuscript:
            from artifacts import ArtifactError
            try:
                return _prove_legacy_compilation(project)
            except (ArtifactError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
                raise ContractError('compile_source_invalid: 请检查已采用正文并重新编译') from exc
        return None  # Standalone historical EPUB / technical fixtures have no manuscript authority.
    binding = strict_json(raw, path=COMPILED_SOURCE)
    require(isinstance(binding, dict) and binding.get('schema_version') == 'novel-compiled-source.v1',
            'compile_source_contract')
    require(type(binding.get('chapter_count')) is int and 1 <= binding['chapter_count'] <= PROFILE.spine_items
            and isinstance(binding.get('content_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', binding['content_sha256'])
            and isinstance(binding.get('front_matter_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', binding['front_matter_sha256']), 'compile_source_contract')
    verify_sources(project, binding.get('sources'))
    return binding


def _prove_legacy_compilation(project: Path) -> dict:
    """One explicit rebuild can adopt old compiler output only after byte proof.

    No draft fallback, model, state migration or publication write occurs here.
    The caller persists this binding only after the final ZIP has passed its gate.
    """
    from artifacts import ArtifactStore
    from compile_book import gather
    from compile_epub import render_epub
    from publication_copy import parse_publication_copy
    from styles import StyleSheet
    try:
        from .h5_import import NS, object_digest
    except ImportError:
        from h5_import import NS, object_digest

    sources = {}

    def read(path, *, optional=False):
        raw = read_project_bytes(project, tuple(path.split('/')), missing_ok=True, max_bytes=PROFILE.zip_expanded)
        require(optional or raw is not None, 'compile_source_missing: 请先重新编译正文', path=path)
        sources[path] = sha256(raw).hexdigest() if raw is not None else None
        return raw

    state = strict_json(read('outputs/state/story_state.json'))
    require(isinstance(state, dict), 'compile_source_state')
    rows = state.get('chapters', {})
    require(isinstance(rows, dict) and 1 <= len(rows) <= PROFILE.spine_items, 'compile_source_chapters')
    require(set(rows) == {str(n) for n in range(1, len(rows) + 1)}, 'compile_source_chapters')
    heads = read('outputs/artifacts/heads.json', optional=True)
    for path in ('outputs/publication/novel-classification.json', 'outputs/publication/novel-serialization.json'):
        read(path, optional=True)
    publication_raw = read('outputs/publication/publication-copy.json', optional=True)
    publication = parse_publication_copy(publication_raw) if publication_raw else None
    artifacts = ArtifactStore(project)
    chapters = []
    for n in range(1, len(rows) + 1):
        head = artifacts.get_head(n, 'final')
        if head:
            revision = artifacts.get_revision(head.revision_id)
            raw = read(f'outputs/artifacts/sha256/{revision.sha256}')
            require(sha256(raw).hexdigest() == revision.sha256 and len(raw) == revision.byte_length,
                    'compile_source_integrity')
        else:
            # The pre-artifact format's accepted Final projection is the only fallback.
            require(heads is None, 'compile_source_missing: 当前正文没有已采用的 Final 版本', path=str(n))
            raw = read(f'outputs/manuscript/chapter_{n:03d}_final.md')
        chapters.append({'number': n, 'title': rows[str(n)].get('title', ''), 'text': raw.decode('utf-8')})
    metadata = state.get('metadata', {})
    book = gather(title=metadata.get('title', 'Untitled'), author=metadata.get('author', ''),
                  genre=metadata.get('genre', ''), chapters=chapters, publication_copy=publication)
    if publication:
        require(publication.title == book.title
                and [(c.number, c.sha256) for c in publication.source.chapters]
                == [(c['number'], sha256(c['text'].encode('utf-8')).hexdigest()) for c in chapters],
                'compile_publication_source_changed')
    require(len(book.chapters) == len(rows), 'compile_source_empty_chapter')
    epub_raw = read('outputs/deliverables/book.epub')
    # Archive identity is bound by the export receipt; this marker must survive cover-only OPF updates.
    sources.pop('outputs/deliverables/book.epub')
    with checked_archive(epub_raw, epub=True) as archive:
        container = xml_document(archive.read('META-INF/container.xml'))
        rootfiles = container.findall('{*}rootfiles/{*}rootfile')
        require(len(rootfiles) == 1, 'epub_container')
        opf_path = rootfiles[0].get('full-path')
        require(safe_path(opf_path), 'epub_container')
        opf = xml_document(archive.read(opf_path))
        mapping = opf.findall("opf:metadata/opf:meta[@property='novel-os:chapter-map']", NS)
        require(len(mapping) == 1, 'compile_source_missing: EPUB 无章节映射，请重新编译正文')
        rows_in_epub = strict_json(mapping[0].text)['chapters']
        require([r['number'] for r in rows_in_epub] == list(range(1, len(rows) + 1)), 'compile_source_chapters')
        book.language = opf.findtext('opf:metadata/dc:language', namespaces=NS) or 'en-US'
        rendered = render_epub(book, StyleSheet.from_dict(state.get('compile_styles', {})))
        hashes = []
        with checked_archive(rendered, epub=True) as expected_archive:
            for row in rows_in_epub:
                href = row['href']
                require(safe_path(href), 'compile_source_path')
                data = archive.read(str(Path(opf_path).parent / href))
                expected = expected_archive.read(f"OEBPS/chap{row['number']:03d}.xhtml")
                require(data == expected, 'compiled_epub_changed: 已采用正文与 EPUB 不一致，请重新编译正文', path=href)
                hashes.append({'number': row['number'], 'xhtml_sha256': sha256(data).hexdigest()})
            introductions = [r for r in strict_json(mapping[0].text)['non_chapter_items'] if r['kind'] == 'introduction']
            require(bool(introductions) == ('OEBPS/intro.xhtml' in expected_archive.namelist()), 'compile_introduction_changed')
            if introductions:
                href = introductions[0]['href']
                require(safe_path(href), 'compile_source_path')
                archive_intro = archive.read(str(Path(opf_path).parent / href))
                require(len(introductions) == 1 and archive_intro == expected_archive.read('OEBPS/intro.xhtml'),
                        'compile_introduction_changed')
    verify_sources(project, sources)
    return {'schema_version': 'novel-compiled-source.v1', 'sources': sources,
            'chapter_count': len(rows), 'content_sha256': object_digest(hashes),
            'front_matter_sha256': object_digest([{'kind': 'introduction', 'xhtml_sha256':
                sha256(archive_intro).hexdigest()}] if introductions else [])}
