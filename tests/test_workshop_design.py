"""M1 creative guidance, author provenance, and recovery without paid inference."""
from copy import deepcopy
import json
import threading

import pytest

from api import workshop
from api.workshop_delivery import author_context, checkpoint_fingerprint
from api.workshop_design import new_design
from core import genre_design
from core.novel_classification import catalog_payload
from test_workshop_api import client, create, finish, model_result, complete_prompt  # noqa: F401
from test_workshop_contract_repair import stub_model, submit


def notes(selection="Werewolf"):
    return {"proposals": [{
        "title": "庇护的交换", "story": "Rin拒绝献出盟友后失去庇护，只能争取旧敌结盟。",
        "tradeoff": "自由带来现实损失，侧重族群政治而非命定爱情。", "recommended": True,
        "genre_roles": [{"selection": selection, "role": "world", "contribution": "族群规则改变可用的安全资源。"}],
        "mechanisms": [{"selection": selection, "name": "庇护契约", "rule": "获得庇护须提供一次狩猎劳务。",
            "boundary": "族长不能直接命令无契约者；脱离时失去庇护而非生命。", "cost": "Rin失去冬季栖身地。",
            "choice": "保护盟友还是提供劳务换回庇护。", "consequence": "旧敌提出更苛刻的联盟条件。",
            "payoff": "Rin凭新联盟改变庇护规则，承担被驱逐的代价。", "removal_effect": "移除族群契约，联盟与高潮的权力交换不成立。"}],
    }], "recommendation_reason": "让狼人身份产生实际的生存限制。", "assumptions": ["族群规模尚待作者决定。"],
       "conflicts": ["恢复庇护还是创建新群体尚未确定。"],
       "next_focus": {"topic": "旧敌为何愿意结盟", "why": "共同利益才能使中段联盟可信。"}}


def response(design_notes=None):
    data = model_result()[0]
    data["reply"] = "### 新走线\n\nRin拒绝献出盟友，旧敌因此有了交换筹码。"
    data["design_notes"] = notes() if design_notes is None else design_notes
    return json.dumps(data, ensure_ascii=False)


def discussed(client, monkeypatch):
    calls = stub_model(monkeypatch, lambda *_: response())
    data = create(client, genres=["Werewolf", "Revenge", "Female Growth"])
    submit(client, data, "先提出不同走线", intent="discuss")
    return finish(client, data["id"]), calls


def decide(client, data, status="accepted", **fields):
    return client.post(f'/api/workshop/sessions/{data["id"]}/decisions', json={
        "revision": data["revision"], "proposal_id": data["creative_design"]["rounds"][0]["proposals"][0]["id"],
        "status": status, **fields})


def test_genre_guidance_preserves_all_selections_and_publication_catalog():
    before = catalog_payload()
    selected = ["Werewolf", "Revenge", "Female Growth", "Domestic Betrayal", "Mystery", "火星农耕", "Dark", "Adult"]
    guidance = genre_design.genre_guidance(selected)
    assert [s["selection"] for s in guidance["selections"]] == selected
    assert {p["id"] for p in guidance["packs"]} == {"werewolf", "contemporary_family", "mystery_suspense"}
    assert guidance["selections"][0]["catalog_ids"] == ["werewolf"]
    assert guidance["selections"][6]["catalog_axes"] == ["tone"]
    assert guidance["selections"][5]["coverage"] == "general_guidance"
    assert guidance["selections"][1]["coverage"] == "general_guidance"
    assert "design_guidance_not_reader_validated" == guidance["maturity"]
    assert all("priority" not in s for s in guidance["selections"])
    assert catalog_payload() == before
    assert {p["id"] for p in genre_design.genre_guidance(list(reversed(selected)))["packs"]} == {p["id"] for p in guidance["packs"]}


@pytest.mark.parametrize("selected,pack", [("狼人", "werewolf"), ("werewolf", "werewolf"),
    ("Ethical Drama", "contemporary_family"), ("家庭背叛", "contemporary_family"), ("Thriller", "mystery_suspense")])
def test_exact_chinese_english_and_id_selection(selected, pack):
    assert genre_design.genre_guidance([selected])["packs"][0]["id"] == pack


def test_no_substring_or_premise_adopts_unselected_mechanisms():
    assert genre_design.genre_guidance(["不要狼人，想要原创类型"])["packs"] == []
    assert new_design({"genres": [], "premise": "拒绝狼人"})["policy"]["guidance"]["packs"] == []


def test_freezes_resources_and_policy_for_existing_discussion(monkeypatch, tmp_path):
    design = new_design({"genres": ["Werewolf"]})
    session = {"input": {}, "messages": [], "creative_design": design}
    original = deepcopy(author_context(session))
    fingerprint = checkpoint_fingerprint(session, "skill")
    resource = json.loads(genre_design.RESOURCE.read_bytes())
    resource["version"] = "future"
    resource["packs"][0]["failure_modes"].append("future rule")
    path = tmp_path / "resource.json"
    path.write_text(json.dumps(resource))
    monkeypatch.setattr(genre_design, "RESOURCE", path)
    assert new_design({"genres": ["Werewolf"]})["policy_sha256"] != design["policy_sha256"]
    assert author_context(session) == original
    assert checkpoint_fingerprint(session, "skill") == fingerprint
    assert "creative_design" not in author_context({"input": {}, "messages": []})


def test_proposals_are_durable_not_author_decisions_and_prompt_uses_snapshot(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    assert data["status"] == "draft" and data["prompt_md"] == ""
    design = data["creative_design"]
    assert design["decisions"] == []
    record = design["rounds"][0]
    assert record["status"] == "recorded"
    assert record["message_index"] == 1
    assert record["proposals"][0]["id"] == "r1:p1"
    assert record["assumptions"] == notes()["assumptions"]
    assert len(calls) == 1
    assert "CREATIVE DESIGN RECORD" in calls[0][0]
    context = json.loads(calls[0][1])["creative_design"]
    assert context["guidance"] == design["policy"]["guidance"]
    restored = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert restored["creative_design"] == design
    submit(client, data, "按推荐来，继续推进联盟代价", intent="discuss")
    current = finish(client, data["id"])
    # Natural language remains author evidence; the model cannot forge a button action.
    context = json.loads(calls[1][1])
    assert context["conversation"][-1]["content"] == "按推荐来，继续推进联盟代价"
    assert context["creative_design"]["rounds"] == design["rounds"]
    assert current["creative_design"]["decisions"] == []
    assert current["prompt_md"] == "" and not current["ready_for_confirmation"]


@pytest.mark.parametrize("bad", [None, {}, {"decisions": [{"status": "accepted"}]},
    {**notes(), "proposals": [{**notes()["proposals"][0], "id": "fake"}]},
    notes("unselected"), {**notes(), "assumptions": ["x" * 50000]}])
def test_optional_bad_notes_never_retry_or_discard_reply(client, monkeypatch, bad):
    raw = model_result()[0]
    raw["design_notes"] = bad
    calls = stub_model(monkeypatch, lambda *_: json.dumps(raw))
    data = create(client, genres=["Werewolf"])
    submit(client, data, "继续", intent="discuss")
    current = finish(client, data["id"])
    assert current["status"] == "draft", current["last_error"]
    assert len(calls) == 1 and "本轮完成" in current["messages"][-1]["content"]
    assert current["creative_design"]["rounds"][0]["status"] == "unavailable"
    assert current["creative_design"]["decisions"] == []


@pytest.mark.parametrize("status,note", [("accepted", ""), ("rejected", ""), ("deferred", ""), ("revised", "盟友改为姐姐")])
def test_author_action_is_local_revision_bound_and_reaches_next_prompt(client, monkeypatch, status, note):
    data, calls = discussed(client, monkeypatch)
    old_fingerprint = checkpoint_fingerprint(data, data["skill_version"])
    result = decide(client, data, status, note=note)
    assert result.status_code == 200, result.text
    current = result.json()
    assert len(calls) == 1
    decision = current["creative_design"]["decisions"][0]
    assert decision["status"] == status and decision["source"] == "author_action"
    assert decision["note"] == note and decision["message_index"] == 2
    assert current["messages"][-1]["role"] == "user"
    assert current["delivery_run_id"] is None
    assert checkpoint_fingerprint(current, current["skill_version"]) != old_fingerprint
    assert decide(client, data, status).status_code == 409
    assert client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": current["revision"]}).status_code == 422
    submit(client, current, "继续", intent="discuss")
    finished = finish(client, data["id"])
    assert len(calls) == 2 and finished["prompt_md"] == ""
    assert json.loads(calls[-1][1])["creative_design"]["author_decisions"] == [decision]


def test_cannot_forge_proposal_empty_revision_or_unsupported_decision(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    assert decide(client, data, proposal_id="r999:p1").status_code == 422
    assert decide(client, data, "revised", note=" ").status_code == 422
    assert decide(client, data, "invented").status_code == 422
    assert len(calls) == 1
    restored = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert restored["revision"] == data["revision"] and restored["creative_design"]["decisions"] == []


def test_active_turn_rejects_decisions_and_pause_does_not_publish_late_notes(client, monkeypatch):
    data, _ = discussed(client, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def slow(*_):
        entered.set()
        assert release.wait(3)
        result, route = model_result()
        result["design_notes"] = notes()
        return result, route

    monkeypatch.setattr(workshop, "_complete", slow)
    job = submit(client, data, "继续", intent="discuss")
    assert entered.wait(2)
    try:
        running = client.get(f'/api/workshop/sessions/{data["id"]}').json()
        assert decide(client, running).status_code == 409
        paused = client.post(f'/api/workshop/sessions/{data["id"]}/pause',
                             json={"revision": running["revision"], "job_id": job["job_id"]}).json()
        assert paused["status"] == "paused"
    finally:
        release.set()
    restored = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert restored["creative_design"] == data["creative_design"]


def test_delivery_uses_current_selected_mechanisms_not_all_historical_options(client, monkeypatch):
    data, _ = discussed(client, monkeypatch)
    accepted = decide(client, data).json()
    context = author_context({**accepted, "turn_intent": "deliver"})["creative_design"]
    assert context["rounds"] == []
    assert len(context["adopted_proposals"]) == 1
    rejected = decide(client, accepted, "rejected").json()
    assert author_context({**rejected, "turn_intent": "deliver"})["creative_design"]["adopted_proposals"] == []
    assert len(rejected["creative_design"]["decisions"]) == 2  # lossless history
    submit(client, rejected, "换一条路线继续", intent="discuss")
    current = finish(client, data["id"])
    assert len(current["creative_design"]["rounds"]) == 2
    assert author_context(current)["creative_design"]["rounds"] == current["creative_design"]["rounds"][-1:]


def test_decision_change_cannot_reapprove_an_unchanged_old_skeleton(client, monkeypatch, tmp_path):
    data, _ = discussed(client, monkeypatch)
    data = decide(client, data).json()
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    saved = json.loads(path.read_text())
    saved["prompt_md"] = complete_prompt(tmp_path)
    path.write_text(json.dumps(saved))
    stale, route = model_result()
    stale["ready_for_confirmation"] = True
    monkeypatch.setattr(workshop, "_complete", lambda *_: (stale, route))
    submit(client, data, "更新完整骨架", intent="deliver")
    current = finish(client, data["id"])
    assert current["status"] == "error"
    assert "创作决定已修改" in current["last_error"]
    assert current["prompt_md"] == saved["prompt_md"]
    assert current["creative_design"]["decisions"] == data["creative_design"]["decisions"]


def test_restart_archives_old_decisions_and_excludes_them_from_new_context(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    data = decide(client, data).json()
    saved = client.post(f'/api/workshop/sessions/{data["id"]}/input', json={
        "revision": data["revision"], "input": {**data["input"], "genres": ["Mystery"]}}).json()
    assert saved["creative_design"] == data["creative_design"]
    assert decide(client, saved).status_code == 409
    result = client.post(f'/api/workshop/sessions/{data["id"]}/restart', json={"revision": saved["revision"]})
    assert result.status_code == 202
    current = finish(client, data["id"])
    context = json.loads(calls[-1][1])["creative_design"]
    assert context["author_decisions"] == [] and context["rounds"] == []
    assert context["guidance"]["selections"][0]["selection"] == "Mystery"
    history = client.get(f'/api/workshop/sessions/{data["id"]}/history/{saved["revision"]}').json()
    assert history["creative_design"] == data["creative_design"]
    assert current["creative_design"]["decisions"] == []


def test_legacy_get_does_not_migrate_but_explicit_new_turn_can_adopt(client, monkeypatch, tmp_path):
    data = create(client)
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    data.pop("creative_design")
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert "creative_design" not in client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert path.read_bytes() == before
    stub_model(monkeypatch, lambda *_: response())
    submit(client, data, "继续", intent="discuss")
    assert "creative_design" in finish(client, data["id"])


def test_delivery_receives_adopted_choices_but_does_not_add_proposal_round(client, monkeypatch, tmp_path):
    data, calls = discussed(client, monkeypatch)
    data = decide(client, data).json()
    prompt = complete_prompt(tmp_path)
    # Existing full skeleton uses the single-request update path.
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    saved = json.loads(path.read_text())
    saved["prompt_md"] = prompt
    path.write_text(json.dumps(saved))
    calls = stub_model(monkeypatch, lambda *_: json.dumps(model_result(prompt)[0]))
    submit(client, data, "生成完整骨架", intent="deliver")
    current = finish(client, data["id"])
    assert current["status"] == "ready", current["last_error"]
    assert len(current["creative_design"]["rounds"]) == 1
    assert json.loads(calls[0][1])["creative_design"]["author_decisions"][0]["status"] == "accepted"
    assert "omit design_notes" in calls[0][0]
