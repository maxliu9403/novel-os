"""A selected cover changes packaging, never the manuscript or its taxonomy."""
from hashlib import sha256
from io import BytesIO
import json
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from PIL import Image
import pytest

from compile_book import gather
from compile_epub import render_epub
from styles import StyleSheet
from core.cover_epub import embed_cover, sync_selected_epub
from core.h5_import import epub_index
from core.narrative_format import infer_narrative_format, serialization_payload
from core.novel_classification import infer_classification


TITLE = '她的选择 <Hers> & "Ours"'
NS = {"opf": "http://www.idpf.org/2007/opf"}


def cover_bytes(color="navy", format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (200, 300), color).save(stream, format=format)
    return stream.getvalue()


def book_fixture(title=TITLE):
    classification = infer_classification(genre="Women's Fiction / Family", audience="female", chapters=2)
    format = infer_narrative_format(classification, chapters=2, target_words=2000, explicit_length=True)
    serialization = serialization_payload(format, None, allow_legacy_defaults=True)
    book = gather(title=title, author="A. Author", genre="Women's Fiction", chapters=[
        {"number": 1, "title": "The Offer", "text": "She kept the letter. Her answer would come tomorrow."},
        {"number": 2, "title": "The Choice", "text": "She chose a home of her own. She still loved them."},
    ], classification=classification.to_dict(), serialization=serialization)
    return book, classification.to_dict(), serialization


def epub_parts(epub):
    with ZipFile(BytesIO(epub)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    container = ET.fromstring(members["META-INF/container.xml"])
    opf_path = container.find("{*}rootfiles/{*}rootfile").get("full-path")
    return members, opf_path, ET.fromstring(members[opf_path])


def embedded_cover(epub):
    members, path, opf = epub_parts(epub)
    images = [item for item in opf.findall("opf:manifest/opf:item", NS)
              if "cover-image" in item.get("properties", "").split()]
    assert len(images) == 1
    item = images[0]
    return members[posixpath.normpath(posixpath.join(posixpath.dirname(path), item.get("href")))], item


def rewrite(epub, replacements=None, *, duplicate=None):
    output = BytesIO()
    with ZipFile(BytesIO(epub)) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            target.writestr(info, (replacements or {}).get(info.filename, source.read(info)))
        if duplicate:
            target.writestr(duplicate, b"duplicate")
    return output.getvalue()


def test_embedding_preserves_every_original_member_except_opf_and_maps_cover_for_h5():
    book, classification, serialization = book_fixture()
    original = render_epub(book, StyleSheet())
    image = cover_bytes()
    updated = embed_cover(original, image, title=TITLE, expected_sha256=sha256(image).hexdigest())
    before, opf_path, _ = epub_parts(original)
    after, _, opf = epub_parts(updated)
    for path, data in before.items():
        if path != opf_path:
            assert after[path] == data, path
    before_index = epub_index(original, serialization, classification)
    after_index = epub_index(updated, serialization, classification)
    assert after_index["chapter_count"] == 2
    assert after_index["content_sha256"] == before_index["content_sha256"]
    assert after_index["chapters"][0]["epub"]["spine_index"] == before_index["chapters"][0]["epub"]["spine_index"] + 1
    cover_page = after_index["non_chapter_items"][0]
    assert cover_page["kind"] == "title_page" and cover_page["epub"]["spine_index"] == 0
    page = ET.fromstring(after[cover_page["epub"]["zip_path"]])
    assert page.find("{*}head/{*}title").text == TITLE
    assert page.find("{*}body/{*}img").get("alt") == TITLE
    assert embedded_cover(updated)[0] == image
    legacy_cover = [node for node in opf.findall("opf:metadata/opf:meta", NS) if node.get("name") == "cover"]
    assert len(legacy_cover) == 1 and legacy_cover[0].get("content") == embedded_cover(updated)[1].get("id")
    with ZipFile(BytesIO(updated)) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == ZIP_STORED


def test_existing_cover_is_replaced_once_and_repeat_is_byte_identical():
    book, classification, serialization = book_fixture()
    first = embed_cover(render_epub(book, StyleSheet()), cover_bytes(), title=TITLE)
    assert embed_cover(first, cover_bytes(), title=TITLE) == first
    second_image = cover_bytes("maroon")
    second = embed_cover(first, second_image, title=TITLE)
    first_members, path, _ = epub_parts(first)
    second_members, _, opf = epub_parts(second)
    assert first_members.keys() == second_members.keys()
    assert embedded_cover(second)[0] == second_image
    changed = [name for name in first_members if first_members[name] != second_members[name]]
    assert changed == ["OEBPS/novel-os/cover.png"]
    assert len(opf.findall("opf:guide/opf:reference", NS)) == 1
    assert len(epub_index(second, serialization, classification)["non_chapter_items"]) == len(epub_index(first, serialization, classification)["non_chapter_items"])


def test_legacy_cover_assets_and_unrelated_resources_survive_format_change():
    book, classification, serialization = book_fixture()
    original = embed_cover(render_epub(book, StyleSheet()), cover_bytes(), title=TITLE)
    members, path, opf = epub_parts(original)
    item = next(item for item in opf.findall("opf:manifest/opf:item", NS) if item.get("properties") == "cover-image")
    item.set("href", "legacy-poster.png")
    members["OEBPS/legacy-poster.png"] = members.pop("OEBPS/novel-os/cover.png")
    page_path = "OEBPS/novel-os/cover.xhtml"
    members[page_path] = members[page_path].replace(b'"cover.png"', b'"../legacy-poster.png"')
    members[path] = ET.tostring(opf)
    members["OEBPS/shared-art.png"] = cover_bytes("white")
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    updated = embed_cover(stream.getvalue(), cover_bytes("teal", "JPEG"), title=TITLE)
    result, _, updated_opf = epub_parts(updated)
    assert result["OEBPS/legacy-poster.png"] == members["OEBPS/legacy-poster.png"]
    assert result["OEBPS/shared-art.png"] == members["OEBPS/shared-art.png"]
    assert embedded_cover(updated)[1].get("media-type") == "image/jpeg"
    retained = updated_opf.find("opf:manifest/opf:item[@href='legacy-poster.png']", NS)
    assert retained is not None and "cover-image" not in retained.get("properties", "")
    assert epub_index(updated, serialization, classification)["chapter_count"] == 2


def test_initial_compile_can_include_the_exact_selected_cover():
    book, classification, serialization = book_fixture()
    image = cover_bytes()
    rendered = render_epub(book, StyleSheet(), cover_image=image, cover_sha256=sha256(image).hexdigest())
    assert embedded_cover(rendered)[0] == image
    assert epub_index(rendered, serialization, classification)["chapter_count"] == 2


def test_sync_is_atomic_idempotent_and_does_not_fabricate_a_manuscript(tmp_path):
    image = cover_bytes()
    params = {"title": TITLE, "expected_sha256": sha256(image).hexdigest()}
    assert sync_selected_epub(tmp_path, image, **params) is False
    path = tmp_path / "outputs/deliverables/book.epub"
    assert not path.exists()
    path.parent.mkdir(parents=True)
    original = render_epub(book_fixture()[0], StyleSheet())
    path.write_bytes(original)
    with pytest.raises(ValueError, match="SHA-256"):
        sync_selected_epub(tmp_path, image, title=TITLE, expected_sha256="a" * 64)
    assert path.read_bytes() == original
    assert sync_selected_epub(tmp_path, image, **params) is True
    first = path.read_bytes()
    assert sync_selected_epub(tmp_path, image, **params) is False
    assert path.read_bytes() == first
    assert not list(path.parent.glob(".book.epub.*"))


@pytest.mark.parametrize("mutation", ["duplicate", "chapter_cover", "traversal", "entity"])
def test_unsafe_epub_is_rejected_without_touching_existing_bytes(tmp_path, mutation):
    original = render_epub(book_fixture()[0], StyleSheet())
    _, path, opf = epub_parts(original)
    if mutation == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            original = rewrite(original, duplicate=path)
    elif mutation in {"chapter_cover", "traversal"}:
        guide = ET.SubElement(opf, "{" + NS["opf"] + "}guide")
        chapter = opf.find("opf:manifest/opf:item[@id='c1']", NS)
        ET.SubElement(guide, "{" + NS["opf"] + "}reference", {"type": "cover", "href":
            "../../outside.xhtml" if mutation == "traversal" else chapter.get("href")})
        original = rewrite(original, {path: ET.tostring(opf)})
    else:
        original = rewrite(original, {path: b'<!DOCTYPE package [<!ENTITY x "unsafe">]>' + ET.tostring(opf)})
    destination = tmp_path / "outputs/deliverables/book.epub"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(original)
    image = cover_bytes()
    with pytest.raises(ValueError):
        sync_selected_epub(tmp_path, image, title=TITLE, expected_sha256=sha256(image).hexdigest())
    assert destination.read_bytes() == original
