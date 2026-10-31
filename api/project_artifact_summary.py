"""Bounded, read-only artifact projection for project cards.

The artifact store intentionally validates its complete append-only journal on
every independent lookup.  A library card needs the Final head for every
chapter, so doing those lookups independently makes the work grow with both
chapter and revision counts.  This module takes one lock-protected snapshot and
keeps the same journal, head, and blob integrity checks.
"""

from __future__ import annotations

from collections.abc import Iterable

from artifacts import ArtifactStore, _ArtifactFileLock, _validate_chapter_kind


def final_chapter_texts(
    store: ArtifactStore,
    chapter_numbers: Iterable[int],
) -> dict[int, str]:
    """Return verified Final text for requested chapters from one snapshot."""
    wanted = set(chapter_numbers)
    if not wanted:
        return {}
    for chapter in wanted:
        _validate_chapter_kind(chapter, "final")

    with store._process_lock:
        with _ArtifactFileLock(store.artifact_root, store.lock_path):
            revisions = store._load_revisions()
            heads = store._load_heads(revisions)
            selected = {
                chapter: revisions[head.revision_id]
                for (chapter, kind), head in heads.items()
                if kind == "final" and chapter in wanted
            }
    return {
        chapter: store._read_verified_text(revision)
        for chapter, revision in selected.items()
    }
