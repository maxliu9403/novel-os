"""Exploratory story discussion stays durable without prematurely publishing a skeleton."""
import json

from api import workshop
from test_workshop_api import client, create, finish, model_result, ready  # noqa: F401
from test_workshop_contract_repair import stub_model, submit


def test_sparse_brief_can_explore_without_questions_or_complete_export(client, monkeypatch):
    replies = [
        "### 三条走线\n\n- 母亲撤回无偿劳动，迫使家人承担后果。\n"
        "- 女儿的独立目标与母亲的计划冲突。\n- 对手调整策略，使最初证据失效。",
        "### 继续推进\n\n母亲拒绝再次代签，女儿因此失去原定机会。"
        "她选择和母亲共同寻找替代路径，旧盟友则为自保拒绝帮助。",
    ]

    def respond(attempt, *_):
        data = model_result()[0]
        data["reply"] = replies[attempt-1]
        return json.dumps(data, ensure_ascii=False)

    calls = stub_model(monkeypatch, respond)
    data = create(client, premise="一个总替家人收拾残局的母亲决定停止牺牲。")
    for message in ("帮我开拓故事", "继续"):
        submit(client, data, message)
        data = finish(client, data["id"])
        assert data["status"] == "draft", data["last_error"]
        assert data["questions"] == []
        assert data["prompt_md"] == ""
        assert data["ready_for_confirmation"] is False

    context = json.loads(calls[1][1])
    assert context["initial_brief"]["premise"] == data["input"]["premise"]
    assert context["conversation"][1]["content"] == replies[0]
    assert context["conversation"][-1]["content"] == "继续"
    assert [m["content"] for m in data["messages"]] == ["帮我开拓故事", replies[0], "继续", replies[1]]
    assert client.get("/api/projects").json() == []
    assert client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                       json={"revision": data["revision"]}).status_code == 422


def test_exploration_keeps_last_export_until_selected_revision_is_complete(client, monkeypatch, tmp_path):
    real_complete = workshop._complete
    data = ready(client, monkeypatch, tmp_path)
    previous = data["prompt_md"]
    monkeypatch.setattr(workshop, "_complete", real_complete)
    proposed_reply = "### 中段新方向\n\n女儿希望离开，母亲希望保住房子；这次分歧改变两人的行动顺序。"
    revised = previous.replace("母亲通过新证据改变下一步选择，保留回应空间。",
                               "母女因目标分歧调整行动，选择的后果带出下一步证据。")

    def respond(attempt, *_):
        if attempt == 1:
            result = model_result()[0]
            result["reply"] = proposed_reply
            return json.dumps(result, ensure_ascii=False)
        return json.dumps(model_result(revised)[0], ensure_ascii=False)

    calls = stub_model(monkeypatch, respond)
    submit(client, data, "先探索一个不同的中段走线，不急着重写整份骨架")
    exploring = finish(client, data["id"])
    assert exploring["status"] == "draft"
    assert exploring["prompt_md"] == previous
    assert exploring["ready_for_confirmation"] is False
    assert client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                       json={"revision": exploring["revision"]}).status_code == 422

    submit(client, exploring, "采用这条走线，收敛并交付完整骨架")
    settled = finish(client, data["id"])
    assert settled["status"] == "ready", settled["last_error"]
    assert settled["prompt_md"] == revised
    context = json.loads(calls[1][1])
    assert context["current_complete_skeleton"] == previous
    assert context["conversation"][-2]["content"] == proposed_reply
    prepared = client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
                           json={"revision": settled["revision"]})
    assert prepared.status_code == 200, prepared.text
    assert "markdown html docx epub pdf" in prepared.json()["prepared"]["command"]


def test_all_workshop_phases_supply_creative_rules_and_compatible_export_contracts():
    versions = []
    for phase in ("intake", "design", "revision"):
        bundle, version = workshop._skill_bundle(phase)
        versions.append(version)
        for name in ("references/creation-path.md", "references/story-exploration.md",
                     "references/narrative-format.md", "references/prompt-contract.md",
                     "assets/novel-skeleton-template.md"):
            assert f"--- {name} ---" in bundle
        assert bundle.index("--- references/story-exploration.md ---") < bundle.index("--- references/prompt-contract.md ---")
    assert len(set(versions)) == 1
