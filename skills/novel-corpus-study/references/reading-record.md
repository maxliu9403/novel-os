# Reading records

Use UTF-8 JSON for the ledger and Markdown for per-book interpretation. Keep originals outside the repository unless the user explicitly wants them there. Never place copied chapters in evidence notes.

```json
{
  "schema": "novel-corpus-reading.v1",
  "reader": "researcher identifier",
  "files": [
    {
      "book_id": "source id",
      "path": "/absolute/source/book/chapters/001.md",
      "sha256": "64 lowercase hex characters",
      "fully_read": true,
      "observation": "A concrete event/change in this chapter, or what the synopsis actually promises.",
      "kind": "narrative",
      "note_path": "/absolute/research/book-note.md"
    }
  ]
}
```

`kind` can be `synopsis`, `narrative`, `author_note`, `promotion`, `mixed`, or `unclassified`. Unclassified material may be read, but narrative statistics must not count it blindly. A mixed file can include a concise explanation of its components in the observation. The ledger is a declaration, not proof of reading; full-text inspection and source-specific notes are still required.

Record only after viewing and reading the full file. Inventory creation, keyword searching and a successful file open do not establish complete reading. For a long chapter spanning several tool calls, finish all ranges before setting fully_read.

Per-book notes should include:

- Scope: provided files and official synopsis read; unknown paid continuation/ending.
- Actual sequence: goals → choices → consequences → reader value, with chapter references.
- What synopsis promises versus what the free chapters demonstrate.
- Actual tag contribution, tags not observed and misleading metadata.
- Transferable craft plus an exception to any tempting universal formula.
- Gaps/duplicates/non-narrative files; do not inflate chapter counts.

An approximately 400–700 Chinese character synthesis plus one observation per chapter is usually sufficient; add detail where a contradiction or unusual structure needs it. Avoid extensive plot retelling and repeated boilerplate.

When updating a corpus, compare content hashes. Changed files invalidate the matching reading declaration; unchanged files can retain it. New synthesis must state the scope reviewed. Never backfill a ledger by hashing unread files.
