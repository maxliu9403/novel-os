"""Real, tiny publication inputs for delivery tests; never a model call."""
import json
from compile_book import gather
from compile_epub import render_epub
from styles import StyleSheet


def compiled_book(project, *, book=None, marker="Test manuscript"):
    book = book or gather(title="Test publication", author="Fixture", genre="", chapters=[
        {"number": 1, "title": "A choice", "text": marker},
    ])
    destination = project / 'outputs/deliverables'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'book.epub').write_bytes(render_epub(book, StyleSheet()))
    publication = project / 'outputs/publication'
    publication.mkdir(parents=True, exist_ok=True)
    for name, value in [('novel-classification', book.classification), ('novel-serialization', book.serialization)]:
        if value:
            (publication / f'{name}.json').write_text(json.dumps(value))
    return destination
