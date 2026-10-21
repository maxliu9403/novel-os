"""Naming and editable basics preserve the durable outline/launch contract."""
import json
import shlex
import threading
from types import SimpleNamespace

import pytest

from api import workshop, workshop_titles
from test_workshop_api import client, ready, finish, model_result  # noqa: F401
from test_workshop_handoff import make_prompt


CANDIDATES = [{"title": name, "reason": "以人物选择呼应故事"}
              for name in ("The Empty Chair", "Her Own Name", "After His Promise")]
ROUTE = {"provider": "stub", "model": "naming-model", "timeout_seconds": 900}


def suggest(client, data, instructions="", current_title=""):
    response = client.post(f'/api/workshop/sessions/{data["id"]}/titles',
                           json={"revision": data["revision"], "instructions": instructions, "current_title": current_title})
    assert response.status_code == 202, response.text
    return finish(client, data["id"])


def test_title_rounds_preserve_story_and_all_requests_after_timeout(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    snapshots = []
    def complete(snapshot, rules):
        snapshots.append(snapshot)
        assert "书名" in rules
        return CANDIDATES, ROUTE
    monkeypatch.setattr(workshop_titles, "complete_titles", complete)
    first = suggest(client, data, "简短英文，不透露结局")
    assert first["messages"] == data["messages"]
    assert first["prompt_md"] == workshop._confirm_format(data["prompt_md"])
    assert first["naming"]["candidates"] == CANDIDATES
    assert first["naming"]["confirmed_title"] is None
    assert first["prepared"] is None
    second = suggest(client, first, "保留 Chair，但强调成长", "My Own Chair")
    assert snapshots[-1]["naming"]["requests"][-1]["current_title"] == "My Own Chair"
    assert len(snapshots[-1]["naming"]["requests"]) == 2
    assert snapshots[-1]["naming"]["requests"][0]["candidates"] == CANDIDATES
    def fail(*_):
        raise TimeoutError("private model details")
    monkeypatch.setattr(workshop_titles, "complete_titles", fail)
    failed = suggest(client, second, "再少两个词")
    assert failed["status"] == "error"
    assert "超时" in failed["last_error"]
    assert failed["prompt_md"] == second["prompt_md"]
    assert failed["naming"]["candidates"] == CANDIDATES
    assert len(failed["naming"]["requests"]) == 3
    assert failed["naming"]["requests"][-1]["instructions"] == "再少两个词"
    assert "private model details" not in json.dumps(failed)
    assert client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                       json={"revision": failed["revision"]}).status_code == 422


def test_human_title_flows_to_metadata_cover_command_and_existing_pipeline(client, monkeypatch, tmp_path):
    from core.pipeline_runner import PipelineRunner
    from core.cover_handoff import parse_cover_handoff
    calls = []
    monkeypatch.setattr(PipelineRunner, "run", lambda self, spec:
                        calls.append(spec) or SimpleNamespace(run_id="title-run", status="completed"))
    monkeypatch.setattr(workshop_titles, "complete_titles", lambda *_: (CANDIDATES, ROUTE))
    data = ready(client, monkeypatch, tmp_path)
    data = suggest(client, data)
    title = "A Mother's Choice: $80,000 & `Hope`."
    response = client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                           json={"revision": data["revision"], "title": title})
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared["revision"] == data["revision"] + 1
    assert prepared["input"] == data["input"]
    assert prepared["naming"]["confirmed_title"] == title
    assert prepared["prepared"]["validation"]["title"] == title
    assert f"Title: {title}\n" in prepared["prompt_md"]
    assert parse_cover_handoff(prepared["prompt_md"]).title == title
    tokens = shlex.split(prepared["prepared"]["command"].replace("\\\n", ""))
    assert f"NOVEL_OS_TITLE={title}" in tokens
    assert prepared["prepared"]["output_formats"] == list(workshop.FORMATS)
    launched = client.post(f'/api/workshop/sessions/{data["id"]}/launch',
                           json={"revision": prepared["revision"]})
    assert launched.status_code == 202, launched.text
    assert calls[0].title == title
    assert calls[0].output_formats == workshop.FORMATS
    assert client.post(f'/api/workshop/sessions/{data["id"]}/input',
                       json={"revision": prepared["revision"], "input": data["input"]}).status_code == 409
    assert client.post(f'/api/workshop/sessions/{data["id"]}/titles',
                       json={"revision": prepared["revision"]}).status_code == 409


def test_rename_does_not_change_series_names_chapters_or_prose(tmp_path):
    prompt = make_prompt(tmp_path, mode="series_installment").read_text()
    # Supply the same real cover contract used by complete skeleton fixtures.
    from test_cover_models_v2 import two_character_fixture
    prompt += "\nCOVER_HANDOFF_BEGIN\n" + json.dumps(two_character_fixture()) + "\nCOVER_HANDOFF_END"
    prompt += "\nA Mother's Choice. is mentioned in a scene.\n"
    prompt = prompt.replace("Title: A Mother", "  Title: A Mother")
    revised = workshop_titles.rename_prompt(prompt, "The Daughter's Future.")
    assert '"title": "The Family"' in revised
    assert "A Mother's Choice. is mentioned in a scene." in revised
    assert "# Novel skeleton" in revised
    assert revised.split("## Chapters")[1].split("COVER_HANDOFF_BEGIN")[0] == prompt.split("## Chapters")[1].split("COVER_HANDOFF_BEGIN")[0]


@pytest.mark.parametrize("title", ["", "  ", "Two\nLines", "\x00", "a" * 501])
def test_rejects_invalid_title_without_overwriting_prepared_file(client, monkeypatch, tmp_path, title):
    data = ready(client, monkeypatch, tmp_path)
    url = f'/api/workshop/sessions/{data["id"]}/prepare'
    original = client.post(url, json={"revision": data["revision"]}).json()
    path = tmp_path / "workshop_v2" / data["id"] / "prepared.md"
    saved = path.read_text()
    response = client.post(url, json={"revision": original["revision"], "title": title})
    assert response.status_code == 422
    assert path.read_text() == saved
    assert client.get(f'/api/workshop/sessions/{data["id"]}').json()["prepared"] == original["prepared"]


def test_changed_basics_preserve_original_and_history_but_require_updated_outline(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path, premise="最初构想", chapters=21)
    data = client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                       json={"revision": data["revision"], "title": "The Empty Chair"}).json()
    url = f'/api/workshop/sessions/{data["id"]}'
    new_input = {**data["input"], "chapters": 30, "constraints": "第六章前不要对质"}
    changed = client.post(url + "/input", json={"revision": data["revision"], "input": new_input}).json()
    assert changed["initial_input"] == data["input"]
    assert changed["input"] == new_input
    assert changed["messages"][:-1] == data["messages"]
    assert '"before": 21' in changed["messages"][-1]["content"]
    assert changed["prompt_md"] == data["prompt_md"]
    assert changed["input_needs_revision"] is True
    assert changed["prepared"] is None and not changed["ready_for_confirmation"]
    assert changed["naming"]["source_sha256"] == ""
    assert client.post(url + "/prepare", json={"revision": changed["revision"], "title": "New"}).status_code == 422
    assert client.post(url + "/titles", json={"revision": changed["revision"]}).status_code == 422
    assert client.post(url + "/launch", json={"revision": changed["revision"]}).status_code == 409
    assert client.post(url + "/input", json={"revision": data["revision"], "input": new_input}).status_code == 409
    monkeypatch.setattr(workshop, "_complete", lambda *_:
                        ({"reply": "已完成", "questions": [], "prompt_md": None, "ready_for_confirmation": True}, ROUTE))
    assert client.post(url + "/turns", json={"revision": changed["revision"], "message": "更新"}).status_code == 409
    assert client.post(url + "/restart", json={"revision": changed["revision"]}).status_code == 202
    failed = finish(client, data["id"])
    assert failed["status"] == "error" and failed["input_needs_revision"]
    assert failed["prompt_md"] == ""
    assert client.get(url + f"/history/{changed['revision']}").json()["prompt_md"] == data["prompt_md"]
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(data["prompt_md"]))
    client.post(url + "/turns", json={"revision": failed["revision"], "message": "重新返回完整稿"})
    stale = finish(client, data["id"])
    assert stale["input_needs_revision"] and stale["status"] == "error"
    # Even a changed paragraph cannot pass with the old chapter count.
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(data["prompt_md"] + "\n新的说明。"))
    client.post(url + "/turns", json={"revision": stale["revision"], "message": "补充"})
    mismatched = finish(client, data["id"])
    assert mismatched["input_needs_revision"] and mismatched["status"] == "error"
    assert "章节数" in mismatched["last_error"]
    # Accept a complete, structurally valid 30-chapter revision.
    updated = data["prompt_md"].replace("Chapters: 21", "Chapters: 30")
    import re
    block = re.search(r"\[NARRATIVE_FORMAT_JSON\](.*?)\[/NARRATIVE_FORMAT_JSON\]", updated, re.S)
    from core.narrative_format import NarrativeFormat
    contract = json.loads(block.group(1))
    contract.pop("format_id", None)
    contract["total_chapters"] = 30
    contract["volumes"][0]["chapter_end"] = 30
    contract = NarrativeFormat.from_dict(contract).to_dict()
    updated = updated[:block.start(1)] + "\n" + json.dumps(contract) + "\n" + updated[block.end(1):]
    updated = updated.replace("## 13.", "\n".join(f"### Chapter {n} — Choice {n}\n新的取证与抉择。" for n in range(22, 31)) + "\n\n## 13.")
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(updated))
    client.post(url + "/turns", json={"revision": mismatched["revision"], "message": "更新完整三十章"})
    finished = finish(client, data["id"])
    assert finished["status"] == "ready", finished["last_error"]
    assert not finished["input_needs_revision"]


def test_running_title_request_rejects_other_mutations(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    def complete(*_):
        entered.set()
        assert release.wait(3)
        return CANDIDATES, ROUTE
    monkeypatch.setattr(workshop_titles, "complete_titles", complete)
    url = f'/api/workshop/sessions/{data["id"]}'
    assert client.post(url + "/titles", json={"revision": data["revision"]}).status_code == 202
    try:
        assert entered.wait(1)
        current = client.get(url).json()
        for suffix, extra in [("input", {"input": data["input"]}), ("titles", {}),
                              ("prepare", {"title": "Title"}), ("turns", {"message": "改故事"})]:
            assert client.post(url + "/" + suffix, json={"revision": current["revision"], **extra}).status_code == 409
    finally:
        release.set()
        finish(client, data["id"])


def test_model_uses_workshop_route_full_naming_context_and_bounded_format_retry(monkeypatch):
    from core import llm_client, provider_settings
    calls = []
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda name:
                        {"provider": name, "model": "independent-model", "base_url": "", "api_key": "",
                         "max_tokens": 4096, "reasoning_effort": "high"})
    class FakeClient:
        def __init__(self, **options):
            self.options = options
        def complete(self, system, user):
            calls.append((self.options, system, user))
            return json.dumps({"candidates": [CANDIDATES[0]] * 3 if len(calls) == 1 else CANDIDATES})
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    result, route = workshop_titles.complete_titles({"turn_timeout_seconds": 900, "prompt_md": "完整骨架",
        "input": {"language": "English"}, "naming": {"requests": [{"instructions": "原要求"}, {"instructions": "新要求"}]}}, "rules")
    assert result == CANDIDATES
    assert len(calls) == 2
    assert calls[0][0]["model"] == "independent-model"
    assert 0 < calls[1][0]["timeout_seconds"] <= calls[0][0]["timeout_seconds"] <= 900
    assert all(text in calls[1][2] for text in ("原要求", "新要求", "完整骨架"))
    assert route["timeout_seconds"] == 900
