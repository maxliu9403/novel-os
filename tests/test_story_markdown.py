from __future__ import annotations

from pathlib import Path

import pytest

from core.project_story_insights import project_story_insights
from core.story_markdown import (
    chapter_title,
    heading_sections,
    json_blocks,
    module_number,
    normalized,
    parse_markdown,
    source_ref,
    tables,
)


def test_story_projection_does_not_promote_markdown_examples_to_records(tmp_path: Path):
    project = tmp_path / "example-safe-story"
    source = project / "outputs" / "input" / "prompt.md"
    source.parent.mkdir(parents=True)
    source.write_text('''## 1. Chapter Outline
### Chapter 1 - Actual Opening
The actual story starts here.

````markdown
## Chapter 99 - Documentation Example
### Chapter 98 - Nested Example
[EXAMPLE_JSON]
{"not_a_story_fact": true}
[/EXAMPLE_JSON]
```
````

~~~markdown
| Chapter | Title | Summary |
| --- | --- | --- |
| 97 | Example table | Not a real chapter. |
~~~

    96. Indented example chapter

## 2. Delivery
```json
{"actual_data": true}
```
''')

    story = project_story_insights(project, project.name)

    assert [chapter["number"] for chapter in story["chapters"]] == [1]
    assert len(story["sections"]) == 2
    assert story["sections"][0]["json_blocks"] == []
    assert story["sections"][1]["json_blocks"][0]["data"] == {"actual_data": True}


@pytest.mark.parametrize("opening,inner,closing", [
    ("````markdown", "```", "`````"),
    ("~~~ markdown", "```", "~~~~"),
    ("```", "~~~~", "```"),
])
def test_fences_mask_examples_and_preserve_source_indexes(opening: str, inner: str, closing: str):
    text = f"## Real\r\n{opening}\r\n## Example\r\n{inner}\r\n### Still example\r\n{closing}\r\n### Actual\r\nBody"

    document = parse_markdown(text)

    assert document.text == text
    assert "".join(document.lines) == text
    assert len(document.lines) == len(document.structural_lines) == 8
    assert document.structural_lines[1:6] == ("\r\n",) * 5
    assert [(section.title, section.heading_line, section.end_line) for section in document.headings] == [
        ("Real", 1, 8), ("Actual", 7, 8),
    ]
    assert "## Example\r\n" in document.headings[0].body
    assert document.fences[0].closed is True
    assert parse_markdown(text) is document
    with pytest.raises((AttributeError, TypeError)):
        document.headings[0].title = "mutated"


def test_shorter_or_wrong_character_fence_does_not_close_the_example():
    text = "## Actual\n````markdown\n```\n~~~\n## Example\n"
    document = parse_markdown(text)
    assert [section.title for section in document.headings] == ["Actual"]
    assert document.fences[0].closed is False
    assert document.fences[0].end_index == len(document.lines)


def test_indented_code_is_not_structural_data():
    text = "## Real\n\n    ## Indented heading\n\t## Tabbed heading\n    [EXAMPLE_JSON]\n    {}\n    [/EXAMPLE_JSON]\n\n### Actual\n"
    document = parse_markdown(text)
    assert [section.title for section in document.headings] == ["Real", "Actual"]
    assert document.structural_lines[2:7] == ("\n",) * 5
    assert json_blocks(text, 1, "Real") == ([], [])


def test_json_transport_ignores_outer_examples_and_keeps_actual_raw_and_source():
    text = '''````markdown
[EXAMPLE_JSON]
{"example": true}
[/EXAMPLE_JSON]
```json
{"example_fence": true}
```
````
~~~application/json
{
  "actual": [1, 2],
  "unknown_future_field": null
}
~~~~
[STORY_JSON]
{"also_actual": true}
[/STORY_JSON]
FOUNDATION_BEGIN
{"foundation": true}
FOUNDATION_END
'''

    blocks, warnings = json_blocks(text, 100, "Structure")

    assert warnings == []
    assert [block["kind"] for block in blocks] == ["json_fence", "STORY_JSON", "FOUNDATION"]
    assert blocks[0]["raw"] == '{\n  "actual": [1, 2],\n  "unknown_future_field": null\n}\n'
    assert blocks[0]["data"] == {"actual": [1, 2], "unknown_future_field": None}
    assert blocks[0]["source"] == {
        "kind": "adopted_skeleton", "path": "outputs/input/prompt.md", "section": "Structure",
        "line_start": 108, "line_end": 113,
    }
    assert blocks[1]["source"]["line_start"] == 114
    assert blocks[2]["source"]["line_end"] == 119


@pytest.mark.parametrize("opening,closing", [("```json", "```"), ("[STORY_JSON]", "[/STORY_JSON]")])
def test_unclosed_json_retains_raw_even_when_inner_json_is_valid(opening: str, closing: str):
    raw = '{"complete_inner_json": true}\n'
    blocks, warnings = json_blocks(f"{opening}\n{raw}", 8, "Delivery")
    assert len(blocks) == len(warnings) == 1
    assert blocks[0]["raw"] == raw
    assert blocks[0]["valid"] is False
    assert blocks[0]["data"] is None
    assert blocks[0]["warning"] == f"Missing closing marker {closing}."
    assert blocks[0]["source"]["line_start"] == 8
    assert blocks[0]["source"]["line_end"] == 9


def test_invalid_json_does_not_drop_raw_or_unknown_markers():
    markdown = '[BROKEN_JSON]\n{"missing":\n[/BROKEN_JSON]\n'
    blocks, warnings = json_blocks(markdown, 3, "Protocol")
    assert blocks[0]["valid"] is False
    assert blocks[0]["raw"] == '{"missing":\n'
    assert "Invalid JSON block BROKEN_JSON" in warnings[0]
    assert blocks[0]["source"]["line_end"] == 5


def test_table_reader_masks_full_document_before_scoped_selection():
    text = '''## Tables
~~~markdown
| Name | Detail |
| --- | --- |
| Example | Not data |
~~~

    | Name | Detail |
    | --- | --- |
    | Indented | Not data |

| Name | Detail |
| :--- | ---: |
| Actual | Has an escaped \\| separator |
'''
    lines = text.splitlines(keepends=True)
    expected = [(["Name", "Detail"], [(13, ["Actual", "Has an escaped | separator"])])]
    assert list(tables(lines, 2, len(lines))) == expected
    assert list(tables(parse_markdown(text).structural_lines, 2, len(lines))) == expected


@pytest.mark.parametrize("title,allow_numbered,expected", [
    ("Chapter 12 - New Door", False, (12, "New Door")),
    ("**Chapter 4: Arrival**", False, (4, "Arrival")),
    ("第一百二十章：归途", False, (120, "归途")),
    ("第两章 新门", False, (2, "新门")),
    ("7. New Door", False, None),
    ("7. New Door", True, (7, "New Door")),
    ("Chapter 0 - Not a chapter", False, None),
    ("第零章", False, None),
])
def test_chapter_title_supports_explicit_chapter_formats(title: str, allow_numbered: bool, expected):
    assert chapter_title(title, allow_numbered=allow_numbered) == expected


def test_source_refs_and_heading_sections_preserve_nested_boundaries():
    text = "## 一、核心 ##\nOpening\n### Detail\nText\n## 二、结局\nEnding\n"
    sections = heading_sections(text)
    assert [section.end_line for section in sections] == [4, 4, 6]
    assert module_number(sections[0].title) == (1, "核心")
    assert normalized("Character / Ledger") == "characterledger"
    assert source_ref("adopted_skeleton", "p.md", sections[1]) == {
        "kind": "adopted_skeleton", "path": "p.md", "section": "Detail",
        "line_start": 3, "line_end": 4,
    }
    assert source_ref("adopted_skeleton", "p.md", sections[1], title="Field", line_start=4)["line_start"] == 4
