# Document import fixtures

`story.doc` is an original three-line test document generated from the following
text using macOS `textutil -convert doc`. It contains no third-party prose,
macros, links, private data or images.

```text
Original test fixture.
Mara must choose between family loyalty and her own future.
FINAL CONSTRAINT: the daughter remains her biological child.
```

DOCX and PDF test fixtures are generated in memory by `test_document_import.py`;
native legacy Word and PDF parsing is required in the deployment-image tests.
