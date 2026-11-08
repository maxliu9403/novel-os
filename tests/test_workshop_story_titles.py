"""Exercise title adoption with the actual story-first renderer and launch boundary."""
from copy import deepcopy
import hashlib
import json
import shlex
from types import SimpleNamespace

import pytest

from api import workshop, workshop_titles
from core.narrative_format import parse_narrative_format_block
from core.story_design import MARKER, render
from test_story_first_engine import design  # noqa: F401
from test_workshop_api import client, create, finish, model_result  # noqa: F401
from test_workshop_handoff import make_prompt


@pytest.mark.parametrize("mode", ["short_novel", "standalone_long", "multi_volume", "series_installment"])
def test_story_first_title_confirmation_preserves_story_and_reaches_launch(client, monkeypatch, design, tmp_path, mode):
    from core.llm_client import LLMClient
    from core.pipeline_runner import PipelineRunner

    monkeypatch.setattr(LLMClient, "complete", lambda *_args, **_kwargs: pytest.fail("Title adoption called a model"))
    calls = []
    monkeypatch.setattr(PipelineRunner, "run", lambda self, spec:
                        calls.append(spec) or SimpleNamespace(run_id="story-title-test", status="completed"))
    session, story, chapters = deepcopy(design)
    session["input"]["mode"] = story["narrative_mode"] = chapters["narrative_mode"] = mode
    if mode in {"multi_volume", "series_installment"}:
        story["narrative_format"] = parse_narrative_format_block(
            make_prompt(tmp_path, mode=mode, chapters=2, target_words=1600).read_text()).to_dict()
    rendered = render(session, story, chapters)["prompt_md"]
    assert "COVER_HANDOFF_BEGIN" not in rendered
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(rendered))
    data = create(client, title="The Choice", author="A. Writer", chapters=2, words_per_chapter=800, mode=mode)
    url = f'/api/workshop/sessions/{data["id"]}'
    assert client.post(url + "/turns", json={"revision": data["revision"], "message": "完善故事"}).status_code == 202
    data = finish(client, data["id"])
    assert data["status"] == "ready", data.get("last_error")

    # Match the production path: naming first confirms structural contracts.
    suggestions = [{"title": "The Ally Who Returned Her Key", "reason": "归还钥匙改变信任"}]
    monkeypatch.setattr(workshop_titles, "complete_titles", lambda *_: (suggestions, {}))
    assert client.post(url + "/titles", json={"revision": data["revision"]}).status_code == 202
    data = finish(client, data["id"])
    before = data["prompt_md"]
    contract = parse_narrative_format_block(before).to_dict()
    title = "She Asked for the Truth, Her Ally Returned the Key"
    response = client.post(url + "/prepare", json={"revision": data["revision"], "title": title})
    assert response.status_code == 200, response.text
    prepared = response.json()
    expected = before.replace("Title: The Choice\n", f"Title: {title}\n", 1)
    assert prepared["prompt_md"] == expected
    assert prepared["naming"]["confirmed_title"] == title
    assert prepared["naming"]["requests"] == data["naming"]["requests"]
    assert prepared["messages"] == data["messages"]
    assert prepared["input"] == data["input"]
    assert parse_narrative_format_block(expected).to_dict() == contract
    assert prepared["prepared"]["validation"]["title"] == title
    assert prepared["prepared"]["validation"]["mode"] == mode
    assert prepared["prepared"]["prompt_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert f"NOVEL_OS_TITLE={title}" in shlex.split(prepared["prepared"]["command"].replace("\\\n", ""))
    assert (tmp_path / "workshop_v2" / data["id"] / "prepared.md").read_text() == expected

    # Reconfirming unchanged metadata is stable and does not advance the revision.
    response = client.post(url + "/prepare", json={"revision": prepared["revision"], "title": title})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == prepared["revision"]
    response = client.post(url + "/launch", json={"revision": prepared["revision"]})
    assert response.status_code == 202, response.text
    assert len(calls) == 1 and calls[0].title == title
    project = tmp_path / "projects" / response.json()["project_id"]
    assert (project / "outputs/input/prompt.md").read_text() == expected
    poster = client.get(f'/api/projects/{response.json()["project_id"]}/poster')
    assert poster.status_code == 200, poster.text
    assert poster.json()["title"] == title
    assert poster.json()["source"]["preview"] == expected


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("malformation", ["begin_only", "end_only", "duplicate", "stray_begin", "invalid_json"])
def test_present_cover_handoff_must_remain_unique_and_valid(compact, malformation):
    from test_cover_models_v2 import two_character_fixture
    block = "COVER_HANDOFF_BEGIN\n" + json.dumps(two_character_fixture()) + "\nCOVER_HANDOFF_END"
    bad = {"begin_only": "COVER_HANDOFF_BEGIN", "end_only": "COVER_HANDOFF_END",
           "duplicate": block + "\n" + block, "stray_begin": block + "\nCOVER_HANDOFF_BEGIN",
           "invalid_json": "COVER_HANDOFF_BEGIN\n{broken}\nCOVER_HANDOFF_END"}[malformation]
    source = (MARKER + "\n" if compact else "") + "Title: The Choice\n## Story\nA returned key.\n" + bad
    with pytest.raises(ValueError):
        workshop_titles.rename_prompt(source, "The Returned Key")


def test_unknown_format_cannot_silently_skip_missing_cover_contract():
    with pytest.raises(ValueError, match="封面交接"):
        workshop_titles.rename_prompt("Title: The Choice\n## Story\nA returned key.", "The Returned Key")


def test_story_first_existing_cover_contract_is_updated_without_touching_story():
    from core.cover_handoff import parse_cover_handoff
    from test_cover_models_v2 import two_character_fixture
    story = MARKER + "\nTitle: The Choice\n## Story\nThe Choice appears in a letter.\n"
    source = story + "COVER_HANDOFF_BEGIN\n" + json.dumps(two_character_fixture()) + "\nCOVER_HANDOFF_END"
    result = workshop_titles.rename_prompt(source, "The Returned Key")
    assert result.startswith(story.replace("Title: The Choice\n", "Title: The Returned Key\n", 1))
    assert parse_cover_handoff(result).title == "The Returned Key"
