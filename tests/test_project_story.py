from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import create_app
from api.project_story import get_project_story
from api.services import ProjectService
from core.project_story_insights import project_story_insights


def _make_project(
    root: Path,
    project_id: str = "story-project",
    *,
    prompt: str | bytes | None = None,
    outline: object | str | None = None,
    state: object | str | None = None,
) -> Path:
    project = root / project_id
    (project / "outputs" / "input").mkdir(parents=True)
    (project / "outputs" / "state").mkdir(parents=True)
    state_value = {"characters": {}} if state is None else state
    state_text = state_value if isinstance(state_value, str) else json.dumps(state_value)
    (project / "outputs" / "state" / "story_state.json").write_text(state_text)
    if prompt is not None:
        prompt_bytes = prompt if isinstance(prompt, bytes) else prompt.encode("utf-8")
        (project / "outputs" / "input" / "prompt.md").write_bytes(prompt_bytes)
    if outline is not None:
        outline_text = outline if isinstance(outline, str) else json.dumps(outline)
        (project / "outputs" / "outline.json").write_text(outline_text)
    return project


def _client(root: Path) -> TestClient:
    return TestClient(create_app(projects_root=root, db_url=f"sqlite:///{root / 'test.db'}"))


def test_projects_complete_modules_chapters_characters_relationships_and_json(tmp_path: Path):
    prompt = """# 示例书

## 1. 故事核心与完整梗概

一个关于选择的故事。

## 2. 人物档案

### Riley

Protagonist. Keeps a field notebook.

### Kaleb

Cabin owner. Wants Riley to stay.

## 3. 完整分章细纲

### 第1章：雪路

Riley reaches the closed road and chooses shelter.

### Chapter 2 - The Cabin

Kaleb offers a room, with conditions left unsaid.

## 4. 人物关系

**Riley ↔ Kaleb**

Their trust is explicitly strained by Kaleb's demand.

## 5. 结构化交付

[STORY_JSON]
{"unknown_future_field":{"kept":true},"beats":[1,2]}
[/STORY_JSON]

[BROKEN_JSON]
{"unfinished":
[/BROKEN_JSON]
"""
    state = {
        "characters": {
            "char_riley": {"full_name": "Riley", "role": "protagonist"},
            "char_kaleb": {"full_name": "Kaleb", "role": "antagonist"},
        }
    }
    project = _make_project(tmp_path, prompt=prompt, state=state)

    story = project_story_insights(project, project.name)

    assert [section["number"] for section in story["sections"]] == [1, 2, 3, 4, 5]
    assert story["sections"][0]["markdown"] == "\n一个关于选择的故事。\n\n"
    assert [(chapter["number"], chapter["title"]) for chapter in story["chapters"]] == [
        (1, "雪路"),
        (2, "The Cabin"),
    ]
    assert {character["id"] for character in story["characters"]} == {
        "char_riley",
        "char_kaleb",
    }
    assert len(story["relationship_edges"]) == 1
    edge = story["relationship_edges"][0]
    assert {edge["source_character_id"], edge["target_character_id"]} == {
        "char_riley",
        "char_kaleb",
    }
    blocks = story["sections"][-1]["json_blocks"]
    assert blocks[0]["valid"] is True
    assert blocks[0]["data"]["unknown_future_field"] == {"kept": True}
    assert blocks[1]["valid"] is False
    assert blocks[1]["raw"] == '{"unfinished":\n'
    assert any("Invalid JSON block BROKEN_JSON" in warning for warning in story["warnings"])
    assert story["narrative"]["text"] == "\n一个关于选择的故事。\n\n"


def test_does_not_fabricate_relationships_from_coappearance(tmp_path: Path):
    prompt = """## 1. 人物档案
### 林岚
主角。
### 周禾
同事。
## 2. 完整分章细纲
### 第一章：值夜班
林岚和周禾在同一间办公室值夜班。
"""
    project = _make_project(tmp_path, prompt=prompt)

    story = project_story_insights(project, project.name)

    assert len(story["characters"]) == 2
    assert story["relationship_edges"] == []
    assert all(character["relationships"] == [] for character in story["characters"])


def test_chapter_scene_lists_do_not_create_extra_chapters(tmp_path: Path):
    prompt = """## 1. Chapter Outline
### Chapter 1 - Opening
Scene goals:
1. Establish home.
2. Reveal a secret.
3. End with a choice.
"""
    project = _make_project(tmp_path, prompt=prompt)
    story = project_story_insights(project, project.name)
    assert [item["number"] for item in story["chapters"]] == [1]
    assert "3. End with a choice." in story["chapters"][0]["summary"]


def test_relationship_code_examples_cannot_replace_source_participants(tmp_path: Path):
    prompt = """## Character Profiles
### Alice
Role: lead
### Bob
Role: friend
## Relationships
**Alice ↔ Bob**
They are friends.
```markdown
IDs and shape: `example_a` ↔ `example_b`; enemies.
```
"""
    project = _make_project(tmp_path, prompt=prompt)
    story = project_story_insights(project, project.name)
    edge = story["relationship_edges"][0]
    ids = {item["name"]: item["id"] for item in story["characters"]}
    assert (edge["source_character_id"], edge["target_character_id"]) == (ids["Alice"], ids["Bob"])
    assert edge["label"] != "enemies"
    assert "example_a" in edge["description"]  # The cited original is still complete.


def test_markdown_tables_and_chinese_numbered_modules_are_supported(tmp_path: Path):
    prompt = """   ## 一、人物设定 ##
| 姓名 | 角色定位 | 描述 |
| --- | --- | --- |
| 苏遥 | 调查者 | 追查失踪账本。 |
| 赵森 | 证人 | 拒绝交出钥匙。 |

## 二、人物关系
| 关系 | 变化 |
| --- | --- |
| 苏遥 ↔ 赵森 | 双方明确约定只交换可核实的事实。 |

## 三、章节大纲
| 章节 | 标题 | 简介 |
| --- | --- | --- |
| 第1章 | 雨夜账本 | 苏遥在雨里找到被撕掉的一页。 |
| 2 | 钥匙 | 赵森拒绝让步，并说明原因。 |
"""
    project = _make_project(tmp_path, prompt=prompt)

    story = project_story_insights(project, project.name)

    assert [section["number"] for section in story["sections"]] == [1, 2, 3]
    assert [(chapter["number"], chapter["title"]) for chapter in story["chapters"]] == [
        (1, "雨夜账本"),
        (2, "钥匙"),
    ]
    assert {character["name"] for character in story["characters"]} == {"苏遥", "赵森"}
    assert len(story["relationship_edges"]) == 1
    assert "只交换可核实的事实" in story["relationship_edges"][0]["description"]


def test_historical_character_ledger_profiles_are_supported(tmp_path: Path):
    prompt = """## 6. Character Ledger

### Megan Hale

```yaml
id: megan_hale
public_identity: Licensed contractor and mother.
desire: Keep people safe.
```

### Daniel Hale

```yaml
id: daniel_hale
public_identity: Company president and Megan's husband.
desire: Preserve control of the company.
```
"""
    project = _make_project(tmp_path, prompt=prompt)

    story = project_story_insights(project, project.name)

    assert [character["name"] for character in story["characters"]] == [
        "Megan Hale",
        "Daniel Hale",
    ]
    assert "Licensed contractor and mother" in story["characters"][0]["description"]
    assert all(character["source"]["kind"] == "adopted_skeleton"
               for character in story["characters"])


def test_historical_json_character_ledger_is_supported(tmp_path: Path):
    prompt = """## Character ledger and personality-driven decision design

```json
[
  {"id":"char_audrey","name":"Audrey Blake","role":"protagonist","public_identity":"Separated wife and returning designer."},
  {"id":"char_lucas","name":"Lucas","role":"relationship counterpart","public_identity":"Startup leader who must respect Audrey's choices."}
]
```
"""
    project = _make_project(tmp_path, prompt=prompt)

    story = project_story_insights(project, project.name)

    assert [(item["id"], item["name"]) for item in story["characters"]] == [
        ("char_audrey", "Audrey Blake"),
        ("char_lucas", "Lucas"),
    ]
    assert "Separated wife and returning designer" in story["characters"][0]["description"]


def test_historical_numbered_peer_profiles_use_state_only_for_identity(tmp_path: Path):
    prompt = """## Character personalities and relationship ledger
Map Nora to protagonist and Daniel to husband.

## 1. Nora Bennett / 诺拉·贝内特——女主
Nora has an independently arranged course and a fixed departure date.

## 2. Daniel Bennett / 丹尼尔·贝内特——丈夫
Daniel wants Nora to absorb the cost of his commitments.
"""
    project = _make_project(
        tmp_path,
        prompt=prompt,
        state={
            "characters": {
                "protagonist": {"full_name": "Nora Bennett", "role": "protagonist"},
                "husband": {"full_name": "Daniel Bennett", "role": "antagonist"},
            }
        },
    )

    story = project_story_insights(project, project.name)

    assert [(item["id"], item["name"]) for item in story["characters"]] == [
        ("protagonist", "Nora Bennett"),
        ("husband", "Daniel Bennett"),
    ]
    assert "independently arranged course" in story["characters"][0]["description"]


def test_section_projection_is_not_truncated(tmp_path: Path):
    body = "完整骨架段落。\n" * 20_000
    project = _make_project(tmp_path, prompt=f"## 1. 全书模块\n{body}")

    story = project_story_insights(project, project.name)

    assert story["sections"][0]["markdown"] == body
    assert story["source"]["bytes"] == len(f"## 1. 全书模块\n{body}".encode("utf-8"))


def test_preserves_unstructured_character_and_relationship_sections(tmp_path: Path):
    character_text = "这里保留作者自由书写的人物群像，没有要求转成卡片。\n第二行也必须完整保留。\n"
    relationship_text = "关系的张力写在叙述里，没有声明任何结构化边。\n"
    prompt = (
        "## 1. 人物台账\n" + character_text
        + "## 2. 人物关系\n" + relationship_text
    )
    project = _make_project(tmp_path, prompt=prompt)

    story = project_story_insights(project, project.name)

    assert story["characters"] == []
    assert story["relationship_edges"] == []
    assert [(item["kind"], item["text"]) for item in story["unstructured_sections"]] == [
        ("characters", character_text),
        ("relationships", relationship_text),
    ]


def test_outline_is_an_explicit_fallback_and_empty_json_is_not_malformed(tmp_path: Path):
    project = _make_project(
        tmp_path,
        outline={
            "chapters": [{"number": 7, "title": "Fallback", "summary": "Only in outline."}],
            "characters": [{"id": "char_ada", "name": "Ada", "role": "witness"}],
            "relationships": [],
        },
        state={},
    )

    story = project_story_insights(project, project.name)

    assert story["source"]["available"] is False
    assert story["chapters"][0]["source"]["kind"] == "outline_fallback"
    assert story["characters"][0]["source"]["kind"] == "outline_fallback"
    assert "Adopted skeleton is not available." in story["warnings"]
    assert not any("identity map could not be parsed" in warning for warning in story["warnings"])


def test_partial_or_fallback_records_do_not_hide_original_ledger(tmp_path: Path):
    prompt = "## Character Profiles\nCast not fully specified.\n### Alice\nRole: lead\n"
    project = _make_project(tmp_path, prompt=prompt)
    story = project_story_insights(project, project.name)
    assert len(story["characters"]) == 1
    assert story["unstructured_sections"][0]["text"] == prompt.split("\n", 1)[1]

    source = project / "outputs/input/prompt.md"
    source.write_text("## Character Profiles\nThe cast remains undecided.\n")
    (project / "outputs/outline.json").write_text(json.dumps({
        "characters": [{"id": "alice", "name": "Alice", "role": "lead"}],
    }))
    story = project_story_insights(project, project.name)
    assert story["characters"][0]["source"]["kind"] == "outline_fallback"
    assert story["unstructured_sections"][0]["text"] == "The cast remains undecided.\n"


def test_outline_does_not_override_or_duplicate_source_backed_character(tmp_path: Path):
    project = _make_project(
        tmp_path,
        prompt="## 1. Character Profiles\n### Ada\nSource-backed description.\n",
        outline={
            "characters": [{
                "id": "outline_ada",
                "name": "Ada",
                "description": "Fallback must not replace the adopted skeleton.",
            }]
        },
    )

    story = project_story_insights(project, project.name)

    assert len(story["characters"]) == 1
    assert story["characters"][0]["description"] == "Source-backed description."
    assert story["characters"][0]["source"]["kind"] == "adopted_skeleton"


def test_cache_invalidates_when_bytes_change_even_if_mtime_and_size_do_not(tmp_path: Path):
    before = "## 1. 完整分章细纲\n### Chapter 1 - Alpha\nSame length.\n"
    after = "## 1. 完整分章细纲\n### Chapter 1 - Bravo\nSame length.\n"
    assert len(before.encode()) == len(after.encode())
    project = _make_project(tmp_path, prompt=before)
    path = project / "outputs" / "input" / "prompt.md"
    original_stat = path.stat()

    first = project_story_insights(project, project.name)
    path.write_text(after)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = project_story_insights(project, project.name)

    assert first["chapters"][0]["title"] == "Alpha"
    assert second["chapters"][0]["title"] == "Bravo"
    assert first["source"]["modified_ns"] == second["source"]["modified_ns"]
    assert first["source"]["sha256"] != second["source"]["sha256"]


def test_fallback_and_identity_dependencies_invalidate_on_content_hash(tmp_path: Path):
    outline_before = {"chapters": [{"number": 1, "title": "Alpha"}]}
    outline_after = {"chapters": [{"number": 1, "title": "Bravo"}]}
    state_before = {"characters": {"char_one": {"full_name": "Ada"}}}
    state_after = {"characters": {"char_two": {"full_name": "Ada"}}}
    project = _make_project(
        tmp_path,
        prompt="## 1. 人物档案\n### Ada\nWitness.\n",
        outline=outline_before,
        state=state_before,
    )
    outline_path = project / "outputs" / "outline.json"
    state_path = project / "outputs" / "state" / "story_state.json"
    outline_stat = outline_path.stat()
    state_stat = state_path.stat()

    first = project_story_insights(project, project.name)
    outline_path.write_text(json.dumps(outline_after))
    state_path.write_text(json.dumps(state_after))
    assert outline_path.stat().st_size == outline_stat.st_size
    assert state_path.stat().st_size == state_stat.st_size
    os.utime(outline_path, ns=(outline_stat.st_atime_ns, outline_stat.st_mtime_ns))
    os.utime(state_path, ns=(state_stat.st_atime_ns, state_stat.st_mtime_ns))
    second = project_story_insights(project, project.name)

    assert first["chapters"][0]["title"] == "Alpha"
    assert second["chapters"][0]["title"] == "Bravo"
    assert first["characters"][0]["id"] == "char_one"
    assert second["characters"][0]["id"] == "char_two"
    first_dependencies = {item["kind"]: item for item in first["source"]["dependencies"]}
    second_dependencies = {item["kind"]: item for item in second["source"]["dependencies"]}
    assert first_dependencies["outline_fallback"]["sha256"] != second_dependencies["outline_fallback"]["sha256"]
    assert first_dependencies["state_id_map"]["sha256"] != second_dependencies["state_id_map"]["sha256"]


def test_story_source_returns_exact_bytes_and_supports_etag(tmp_path: Path):
    raw = b"# Exact\r\n\r\n## 1. Module\r\nLine with trailing spaces.  \r\n"
    _make_project(tmp_path, prompt=raw)
    client = _client(tmp_path)

    response = client.get("/api/projects/story-project/story/source")

    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["x-source-sha256"]
    assert response.headers["etag"] == f'"{response.headers["x-source-sha256"]}"'
    unchanged = client.get(
        "/api/projects/story-project/story/source",
        headers={"If-None-Match": f'W/{response.headers["etag"]}'},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == response.headers["etag"]


@pytest.mark.parametrize("prompt", [None, b"\xff\xfe", "symlink"])
def test_missing_malformed_or_symlinked_source_is_graceful(tmp_path: Path, prompt):
    project = _make_project(tmp_path, prompt=None if prompt == "symlink" else prompt)
    if prompt == "symlink":
        outside = tmp_path / "outside.md"
        outside.write_text("private outside source")
        (project / "outputs" / "input" / "prompt.md").symlink_to(outside)
    client = _client(tmp_path)

    story_response = client.get("/api/projects/story-project/story")
    source_response = client.get("/api/projects/story-project/story/source")

    assert story_response.status_code == 200
    body = story_response.json()
    assert body["source"]["available"] is False
    assert body["sections"] == []
    assert body["warnings"]
    assert source_response.status_code == 404
    if prompt == "symlink":
        assert "private outside source" not in story_response.text


def test_project_path_traversal_is_a_404(tmp_path: Path):
    outside = _make_project(tmp_path.parent, "outside", prompt="secret marker")
    assert outside.exists()
    service = ProjectService(tmp_path)

    with pytest.raises(HTTPException) as caught:
        get_project_story("../outside", service)

    assert caught.value.status_code == 404


def test_malformed_fallback_files_warn_without_losing_skeleton(tmp_path: Path):
    project = _make_project(
        tmp_path,
        prompt="## 1. 完整分章细纲\n### Chapter 1 - Present\nSource-backed.\n",
        outline="{broken",
        state="[]",
    )

    story = project_story_insights(project, project.name)

    assert story["chapters"][0]["title"] == "Present"
    assert any("Outline fallback could not be parsed" in warning for warning in story["warnings"])
    assert any("identity map could not be parsed" in warning for warning in story["warnings"])
