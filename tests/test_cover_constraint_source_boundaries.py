from __future__ import annotations

import pytest

from core.cover_constraint_sources import (
    MAX_CONSTRAINT_SOURCE_TEXT,
    extract_cover_constraints,
    resolve_constraint_sources,
)
from tests.test_cover_constraint_sources import snapshot
from core.cover_sources import capture_source


def _recapture(context, frozen, text):
    project = context.project_resolver(frozen.selector.project_id)
    (project / "outputs/input/prompt.md").write_text(text, encoding="utf-8")
    return capture_source(context, frozen.selector)


def test_duplicate_handoff_blocks_are_rejected(snapshot):
    context, frozen = snapshot
    duplicated = _recapture(context, frozen, frozen.primary_text + frozen.primary_text)

    with pytest.raises(ValueError, match="exactly once"):
        extract_cover_constraints(duplicated)


def test_node_after_excerpt_limit_remains_resolvable(snapshot):
    context, frozen = snapshot
    large_section = frozen.primary_text.replace(
        "node_id: door_choice", "earlier source context\n" * 1000 + "node_id: door_choice"
    )
    expanded = _recapture(context, frozen, large_section)

    sources = resolve_constraint_sources(expanded)

    assert "node_id: door_choice" in sources[1].text
    assert all(len(source.text) <= MAX_CONSTRAINT_SOURCE_TEXT for source in sources)


def test_duplicate_node_anchors_are_ambiguous(snapshot):
    context, frozen = snapshot
    duplicated = _recapture(context, frozen, frozen.primary_text.replace(
        "node_id: door_choice", "node_id: door_choice\nnode_id: door_choice"
    ))

    with pytest.raises(ValueError, match="ambiguous"):
        extract_cover_constraints(duplicated)


def test_numbered_nested_chapters_belong_to_their_parent_section(snapshot):
    context, frozen = snapshot
    text = frozen.primary_text.replace(
        "### Door decision", "### 1. 第一章\n#### 6. 小节\n### 9. Door decision"
    )
    nested = _recapture(context, frozen, text)

    sources = resolve_constraint_sources(nested)

    assert sources[1].section_number == 9
    assert "### 9. Door decision" in sources[1].text
    assert "node_id: door_choice" in sources[1].text


def test_unnumbered_peer_heading_ends_constraint_source_section(snapshot):
    context, frozen = snapshot
    text = frozen.primary_text.replace("### Door decision", "## Appendix\n### Door decision")
    changed = _recapture(context, frozen, text)

    with pytest.raises(ValueError, match="node is not defined"):
        extract_cover_constraints(changed)


def test_duplicate_top_level_numbered_sections_remain_invalid(snapshot):
    context, frozen = snapshot
    text = frozen.primary_text.replace("### Door decision", "## 6. Duplicate section")
    changed = _recapture(context, frozen, text)

    with pytest.raises(ValueError, match="duplicate skeleton section number"):
        extract_cover_constraints(changed)


@pytest.mark.parametrize("suffix", ["!", "---", "人物关系", "人物关系及变化extra"])
def test_constraint_section_slug_requires_exact_title_or_explicit_alias(snapshot, suffix):
    context, frozen = snapshot
    changed = _recapture(context, frozen, frozen.primary_text.replace(
        "section:6.relationship_map", f"section:6.{suffix}"
    ))

    with pytest.raises(ValueError, match="does not match"):
        extract_cover_constraints(changed)


@pytest.mark.parametrize("suffix", ["人物关系及变化", "relationship_map"])
def test_constraint_section_slug_accepts_exact_title_and_registered_alias(snapshot, suffix):
    context, frozen = snapshot
    changed = _recapture(context, frozen, frozen.primary_text.replace(
        "section:6.relationship_map", f"section:6.{suffix}"
    ))

    assert resolve_constraint_sources(changed)[0].reference == f"section:6.{suffix}"


_FENCED_EXAMPLES = [
    ("```markdown", "", "```"),
    ("~~~markdown", "", "~~~"),
    ("   ```markdown", "  ```not a closing fence\n~~~\n", "  ````"),
    (" ~~~markdown", "~~~not a closing fence\n```\n", "   ~~~~"),
    ("````markdown", "```\n````not a closing fence\n~~~~\n", "`````"),
    ("~~~~markdown", "~~~\n~~~~not a closing fence\n````\n", "~~~~~"),
]


@pytest.mark.parametrize("opening, misleading_end, closing", _FENCED_EXAMPLES)
@pytest.mark.parametrize("reference_kind", ["section", "node_line", "node_heading"])
def test_fenced_examples_cannot_establish_constraint_sources(
    snapshot, opening, misleading_end, closing, reference_kind,
):
    context, frozen = snapshot
    text = frozen.primary_text
    if reference_kind == "section":
        text = text.replace("section:9.node:door_choice", "section:99")
        example = "## 99. Example only\nThis is not an approved skeleton section."
        needle = "### Door decision"
        expected_error = "does not exist"
    else:
        example = "node_id: door_choice" if reference_kind == "node_line" else "### Example [node_door_choice]"
        needle = "node_id: door_choice"
        expected_error = "node is not defined"
    text = text.replace(needle, f"{opening}\n{misleading_end}{example}\n{closing}")
    changed = _recapture(context, frozen, text)

    with pytest.raises(ValueError, match=expected_error):
        extract_cover_constraints(changed)


def test_fenced_examples_do_not_change_real_node_offsets_or_source_excerpt(snapshot):
    context, frozen = snapshot
    example = ("   ````markdown\r\n## 6. Not a real section\r\n"
               "```\r\nnode_id: door_choice\r\n" + "示例内容🙂\r\n" * 40 + "   `````\r\n")
    text = frozen.primary_text.replace("node_id: door_choice", example + "node_id: door_choice")
    changed = _recapture(context, frozen, text)

    sources = resolve_constraint_sources(changed)

    full_section = text[text.index("## 9."):text.index("## 15.")]
    anchor = full_section.rindex("node_id: door_choice")
    start = max(0, anchor - 200)
    assert sources[1].text == full_section[start:start + MAX_CONSTRAINT_SOURCE_TEXT]
    assert "示例内容🙂\r\n" in sources[1].text
    assert sources[1].source_sha256 == changed.source_content_sha256
    assert changed.primary_text == text
