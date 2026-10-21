from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from core.cover_review_units import REVIEW_UNIT_VERSION, build_review_units, review_plan_identity


def source(*pieces, document_id="primary"):
    text = "".join(pieces)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = {"document_id": document_id, "text": text, "sha256": digest}
    segments, cursor = [], 0
    for index, piece in enumerate(pieces):
        end = cursor + len(piece.encode("utf-8"))
        segments.append({"segment_id": f"segment-{document_id}-{index}", "document_id": document_id,
            "document_sha256": digest, "byte_start": cursor, "byte_end": end, "text": piece,
            "heading_path": ["Do not trust the old segmenter's heading path"]})
        cursor = end
    return document, segments


def assert_exact_partition(documents, segments, units):
    members = [member for unit in units for member in unit["members"]]
    assert [member["segment_id"] for member in members] == [segment["segment_id"] for segment in segments]
    assert len({member["segment_id"] for member in members}) == len(segments)
    for document in documents:
        owned = [unit for unit in units if unit["document_id"] == document["document_id"]]
        body = document["text"].encode("utf-8")
        assert b"".join(unit["text"].encode("utf-8") for unit in owned) == body
        for unit in owned:
            assert unit["text"].encode("utf-8") == body[unit["byte_start"]:unit["byte_end"]]
            for member in unit["members"]:
                assert member["text"].encode("utf-8") == body[member["byte_start"]:member["byte_end"]]
                for heading in member["source_context"]:
                    quote = body[heading["byte_start"]:heading["byte_end"]]
                    assert quote == heading["text"].encode("utf-8")
                    assert hashlib.sha256(quote).hexdigest() == heading["sha256"]
                    assert heading["document_sha256"] == document["sha256"]


def test_parent_child_headings_and_intervening_blank_segments_share_first_body_review():
    document, segments = source("# Book\n\n", "## Characters\n\n", "\n", "### char_mason · Mason\n\n",
                                "\n\n", "Mason is eight.\n\n", "His goal is independence.\n")
    original = deepcopy((document, segments))
    units = build_review_units([document], segments)
    assert len(units) == 2
    assert [member["structural_role"] for member in units[0]["members"]] == ["heading", "heading", "whitespace", "heading", "whitespace", "content"]
    context = units[0]["members"][-1]["source_context"]
    assert [item["title"] for item in context] == ["Book", "Characters", "char_mason · Mason"]
    assert (document, segments) == original
    assert_exact_partition([document], segments, units)


def test_actual_levels_handle_skipped_levels_and_replace_siblings():
    document, segments = source("# Book\n\n", "### Mason\n\n", "First character.\n\n",
                                "### Claire\n\n", "Second character.\n\n", "## Ending\n\n", "Consequence.\n")
    units = build_review_units([document], segments)
    assert len(units) == 3
    assert [item["title"] for item in units[1]["members"][-1]["source_context"]] == ["Book", "Claire"]
    assert [item["level"] for item in units[1]["members"][-1]["source_context"]] == [1, 3]
    assert [item["title"] for item in units[2]["members"][-1]["source_context"]] == ["Book", "Ending"]
    assert_exact_partition([document], segments, units)


def test_orphan_sibling_and_trailing_headings_remain_owned_review_units():
    document, segments = source("## Orphan\n\n", "## Actual section\n\n", "A conflict.\n\n", "## Last orphan\n\n", "\n")
    units = build_review_units([document], segments)
    assert len(units) == 3
    assert units[0]["text"] == "## Orphan\n\n"
    assert units[-1]["text"] == "## Last orphan\n\n\n"
    assert all(member["structural_role"] != "content" for member in units[-1]["members"])
    assert_exact_partition([document], segments, units)


def test_document_boundary_never_pairs_an_orphan_heading_with_an_attachment():
    first, first_segments = source("# Main\n", document_id="primary")
    second, second_segments = source("Attachment prose.\n", "# Attachment title\n", document_id="attachment")
    documents, segments = [first, second], [*first_segments, *second_segments]
    units = build_review_units(documents, segments)
    assert len(units) == 3
    assert units[1]["members"][0]["source_context"] == []
    assert_exact_partition(documents, segments, units)


def test_crlf_unicode_and_atx_indentation_keep_exact_utf8_context_spans():
    document, segments = source("# 小说\r\n\r\n", "   ### 玛拉 ###\r\n\r\n", "她关上门。\r\n\r\n",
                                "    # This is indented code\r\n", "Next paragraph.\r\n")
    units = build_review_units([document], segments)
    heading = units[0]["members"][-1]["source_context"][-1]
    assert heading["title"] == "玛拉" and heading["level"] == 3
    assert heading["text"] == "   ### 玛拉 ###\r\n"
    assert heading["byte_start"] == len("# 小说\r\n\r\n".encode("utf-8"))
    assert units[1]["members"][0]["structural_role"] == "content"
    assert units[-1]["members"][0]["source_context"][-1]["title"] == "玛拉"
    assert_exact_partition([document], segments, units)


@pytest.mark.parametrize("opening,not_a_close,closing", [
    ("````python\n", "```\n", "`````\n"),
    ("```json\n", "~~~\n", "```\n"),
    ("~~~yaml\n", "```\n", "~~~~\n"),
    ("```\n", "``` trailing text\n", "```\n"),
    ("```\n", "    ```\n", "   ```\n"),
])
def test_fenced_hash_lines_never_create_heading_context_even_when_split_across_segments(opening, not_a_close, closing):
    document, segments = source("# Real\n\n", opening, "# Fake one\n", not_a_close, "## Fake two\n", closing,
                                "## Real next\n\n", "Narrative resumes.\n")
    units = build_review_units([document], segments)
    members = [member for unit in units for member in unit["members"]]
    assert members[2]["structural_role"] == members[4]["structural_role"] == "content"
    assert [item["title"] for item in members[4]["source_context"]] == ["Real"]
    assert [item["title"] for item in members[-1]["source_context"]] == ["Real", "Real next"]
    assert_exact_partition([document], segments, units)


def test_oversized_heading_continuations_keep_original_members_without_truncation():
    document, segments = source("# " + "长" * 50, "长" * 50 + "\n\n", "Narrative " * 5000)
    units = build_review_units([document], segments)
    assert len(units) == 1
    assert [member["structural_role"] for member in units[0]["members"]] == ["heading", "heading", "content"]
    assert len(units[0]["text"].encode("utf-8")) > 24000
    assert_exact_partition([document], segments, units)


def test_plan_identity_binds_source_members_and_borrowed_context_deterministically():
    document, segments = source("# Book\n\n", "Text.\n\n", "More text.\n")
    units = build_review_units([document], segments)
    repeated = build_review_units(deepcopy([document]), deepcopy(segments))
    assert units == repeated
    assert REVIEW_UNIT_VERSION == "cover-source-review-units.v1"
    assert review_plan_identity(units) == review_plan_identity(repeated)
    renamed = deepcopy(segments)
    renamed[-1]["segment_id"] = "another-original-segment-id"
    assert build_review_units([document], renamed)[-1]["unit_id"] != units[-1]["unit_id"]
    changed_context = deepcopy(units)
    changed_context[-1]["members"][0]["source_context"][0]["title"] = "Changed context"
    assert review_plan_identity(changed_context) != review_plan_identity(units)
    other_document, other_segments = source("# Else\n\n", "Text.\n\n", "More text.\n")
    assert build_review_units([other_document], other_segments)[-1]["unit_id"] != units[-1]["unit_id"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reorder", "gap", "overlap", "body", "hash", "foreign"])
def test_invalid_original_partition_is_rejected_without_silent_repairs(mutation):
    document, segments = source("# Title\n\n", "First paragraph.\n\n", "Last paragraph.\n")
    if mutation == "missing":
        segments.pop()
    elif mutation == "duplicate":
        segments.append(deepcopy(segments[-1]))
    elif mutation == "reorder":
        segments.reverse()
    elif mutation == "gap":
        segments[1]["byte_start"] += 1
    elif mutation == "overlap":
        segments[1]["byte_start"] -= 1
    elif mutation == "body":
        segments[1]["text"] = "Invented source"
    elif mutation == "hash":
        segments[1]["document_sha256"] = "0" * 64
    else:
        segments[1]["document_id"] = "foreign"
    with pytest.raises(ValueError):
        build_review_units([document], segments)


def test_documents_require_correct_hash_unique_ids_and_complete_ordered_members():
    document, segments = source("First.\n")
    other, other_segments = source("Second.\n", document_id="attachment")
    with pytest.raises(ValueError, match="hash"):
        build_review_units([{**document, "sha256": "0" * 64}], segments)
    with pytest.raises(ValueError, match="unique"):
        build_review_units([document, document], segments)
    with pytest.raises(ValueError, match="partition"):
        build_review_units([document, other], [*other_segments, *segments])
    with pytest.raises(ValueError, match="every frozen source byte"):
        build_review_units([document, other], segments)
    empty, no_segments = source(document_id="empty")
    assert build_review_units([empty], no_segments) == []
    assert_exact_partition([document, other, empty], [*segments, *other_segments],
        build_review_units([document, other, empty], [*segments, *other_segments]))
