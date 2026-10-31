"""Format boundaries: character adaptation must not turn nearby prose into facts."""
import json
from pathlib import Path

from core.project_story_insights import project_story_insights


def project_story(tmp_path: Path, prompt: str, state: dict | None = None):
    project = tmp_path / "compat-story"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/state").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_text(prompt, encoding="utf-8")
    (project / "outputs/state/story_state.json").write_text(json.dumps(state or {}))
    return project_story_insights(project, project.name)


def test_profile_field_subheadings_are_not_characters(tmp_path):
    story = project_story(tmp_path, """## Character Profiles
### Alice
Role: protagonist
#### Motivation
Protect her sister.
#### Desire
Build a new home.
### Bob
Role: ally
""")
    assert [item["name"] for item in story["characters"]] == ["Alice", "Bob"]
    assert "Protect her sister" in story["characters"][0]["description"]


def test_numbered_chapter_about_known_person_is_not_a_profile(tmp_path):
    story = project_story(tmp_path, """## Chapter plan
## 1. Alice faces the board
Alice negotiates a new deadline.
""", {"characters": {"char_alice": {"name": "Alice", "role": "villain"}}})
    assert story["characters"] == []


def test_state_role_is_not_reported_as_source_fact(tmp_path):
    story = project_story(tmp_path, """## Character Profiles
### Alice
- Age: 31
Her occupation is not yet confirmed.
""", {"characters": {"char_alice": {"name": "Alice", "role": "villain"}}})
    assert story["characters"][0]["id"] == "char_alice"
    assert story["characters"][0]["role"] == ""


def test_numbered_peer_profiles_require_a_contiguous_ledger_region(tmp_path):
    story = project_story(tmp_path, """## Character personalities and relationship ledger
A guide to the cast.
## 叙述与人物规模
Two adult leads.
## 1. Alice / 艾丽丝——调查员
Alice has a bounded role.
## 2. Bob / 鲍勃——朋友
Bob supplies transport.
## Chapter plan
## 3. Alice — Return to the city
This is a chapter description, not a character profile.
""", {"characters": {"char_alice": {"name": "Alice"}, "char_bob": {"name": "Bob"}}})
    assert [(item["name"], item["role"]) for item in story["characters"]] == [
        ("Alice", "调查员"), ("Bob", "朋友")]
    assert all("chapter description" not in item["description"] for item in story["characters"])


def test_explicit_source_role_fields_take_priority_over_state(tmp_path):
    story = project_story(tmp_path, """## Character Profiles
### Alice
```yaml
id: char_alice
public_identity: licensed surveyor
```
### Bob
```yaml
id: char_bob
tier: supporting
```
### Carol (char_carol)
```json
{"id":"char_carol","name":"Carol","role":"supporting_friend"}
```
""", {"characters": {"char_alice": {"name": "Alice", "role": "villain"}}})
    assert [(item["name"], item["role"]) for item in story["characters"]] == [
        ("Alice", "licensed surveyor"), ("Bob", "supporting"), ("Carol", "supporting_friend")]


def test_unstructured_character_content_keeps_original_text(tmp_path):
    raw = "The principal cast will be chosen later.\n#### Motivation\nA shared dream.\n"
    story = project_story(tmp_path, "## Character Profiles\n" + raw)
    assert story["characters"] == []
    assert any(raw == item["text"] for item in story["unstructured_sections"])


def test_profile_ids_with_translated_names_are_explicit_source_fields(tmp_path):
    story = project_story(tmp_path, """## 人物档案
E01｜艾丽丝 Alice：调查员；在城市工作。
## Character Profiles
### char_bob · Bob
Role: friend
""", {"characters": {"E01": {"name": "Alice"}, "char_bob": {"name": "Bob"}}})
    assert [(item["id"], item["name"]) for item in story["characters"]] == [
        ("E01", "Alice"), ("char_bob", "Bob")]


def test_combined_profile_retains_two_explicit_people(tmp_path):
    story = project_story(tmp_path, """## Character Core
### Ann Baker, 45, and Ben Baker, 21 — parent and child
They live in the same household.
""", {"characters": {"char_ann": {"name": "Ann Baker"}, "char_ben": {"name": "Ben Baker"}}})
    assert [(item["id"], item["name"]) for item in story["characters"]] == [
        ("char_ann", "Ann Baker"), ("char_ben", "Ben Baker")]


def test_markdown_example_does_not_supply_live_role_or_identity(tmp_path):
    story = project_story(tmp_path, """## Character Profiles
### Alice
Occupation has not been confirmed.
```markdown
Role: villain
```
## Relationships
```markdown
`char_alice` ↔ `char_bob`
```
""")
    assert story["characters"][0]["role"] == ""
    assert story["characters"][0]["id"] == "character_alice"


def test_numbered_name_and_role_without_ledger_remains_unstructured(tmp_path):
    story = project_story(tmp_path, """## An unrelated outline
## 1. Alice — investigator
The scene describes her arrival.
""", {"characters": {"char_alice": {"name": "Alice"}}})
    assert story["characters"] == []


def test_inline_profile_citations_stay_on_each_real_source_line(tmp_path):
    story = project_story(tmp_path, "## Character Core\nAlice: House builder.\nBob: Teacher.\n")
    assert [(item["name"], item["source"]["line_start"], item["source"]["line_end"])
            for item in story["characters"]] == [("Alice", 2, 2), ("Bob", 3, 3)]
