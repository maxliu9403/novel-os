"""Exercise the web workshop using real persistence/contracts and stubbed model/engine."""
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from api.main import create_app
from api.services import ProjectService
from api import workshop
from api.jobs import JobRunner
from test_workshop_handoff import make_prompt
from test_cover_models_v2 import two_character_fixture


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "runner", JobRunner())
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json"))
    monkeypatch.delenv("NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS", raising=False)
    root = tmp_path / "projects"
    app = create_app(projects_root=root, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    return TestClient(app)


def create(client, **fields):
    response = client.post("/api/workshop/sessions", json={"title": "A Mother's Choice", **fields})
    assert response.status_code == 201
    return response.json()


def finish(client, session_id):
    for _ in range(100):
        result = client.get(f"/api/workshop/sessions/{session_id}").json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("Stub workshop did not finish")


def model_result(prompt=None, questions=None):
    return ({"reply": "本轮完成", "questions": questions or [], "prompt_md": prompt,
             "ready_for_confirmation": prompt is not None},
            {"provider": "stub", "model": "workshop-model", "timeout_seconds": 900})


def complete_prompt(tmp_path):
    base = make_prompt(tmp_path).read_text()
    header, rest = base.split("## Engine contracts", 1)
    contracts = rest.split("## Chapters", 1)[0]
    titles = ["创作规格与结构配置", "目标读者、地域与语言", "故事核心与完整梗概", "硬性限制",
              "人物身份、性格与心理档案", "人物关系及变化", "世界规则、时间线与现实约束",
              "秘密、知情范围与证据链", "全书结构", "伏笔、揭示与回收总表", "开篇吸引力与阅读回报",
              "完整分章细纲", "结局与人物归宿", "文风与正文创作规范", "封面交接与交付要求",
              "假设与选定决策记录", "交付前检查记录"]
    body = header
    for i, title in enumerate(titles, 1):
        content = "母亲保存毕业视频与账单，以明确选择重获自主；章节间用信息与情感后果承接。"
        if i == 1:
            content = contracts
        elif i == 12:
            content = "\n".join(f"### Chapter {n} — Choice {n}\n母亲通过新证据改变下一步选择，保留回应空间。" for n in range(1, 22))
        elif i == 15:
            content = "COVER_HANDOFF_BEGIN\n" + json.dumps(two_character_fixture()) + "\nCOVER_HANDOFF_END"
        body += f"\n## {i}. {title}\n\n{content}\n"
    return body


def ready(client, monkeypatch, tmp_path, **fields):
    prompt = complete_prompt(tmp_path)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(prompt))
    data = create(client, **fields)
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                           json={"message": "完善我的框架", "revision": data["revision"]})
    assert response.status_code == 202
    return finish(client, data["id"])


def test_persists_separately_and_accepts_missing_fields(client, tmp_path):
    data = create(client)
    assert data["input"]["mode"] is None
    assert data["input"]["chapters"] is None
    assert data["prompt_md"] == ""
    assert data["skill_version"]
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    assert path.is_file()
    assert client.get("/api/projects").json() == []
    service = workshop.WorkshopService(ProjectService(tmp_path / "projects"))
    assert service.get(data["id"])["input"]["title"] == "A Mother's Choice"


def test_turn_returns_before_model_and_rejects_concurrent_revision(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def complete(*_):
        entered.set()
        assert release.wait(3)
        return model_result(questions=["计划多少章？"])
    monkeypatch.setattr(workshop, "_complete", complete)
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    response = client.post(url, json={"message": "帮我构思", "revision": 0})
    assert response.status_code == 202
    assert entered.wait(1)
    assert response.json()["status"] == "running"
    current = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert current["status"] == "running"
    assert client.post(url, json={"message": "another", "revision": current["revision"]}).status_code == 409
    release.set()
    current = finish(client, data["id"])
    assert current["questions"] == ["计划多少章？"]
    assert current["prompt_md"] == ""
    assert client.post(url, json={"message": "stale", "revision": 0}).status_code == 409


def test_timeout_preserves_full_prompt_and_all_history(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    before = data["prompt_md"]
    received = []
    def fail(session, _skill):
        received.append(session)
        raise TimeoutError("secret-request-body")
    monkeypatch.setattr(workshop, "_complete", fail)
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                           json={"message": "请再加强女儿动机", "revision": data["revision"]})
    assert response.status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "error"
    assert "超时" in result["last_error"]
    assert "secret-request-body" not in json.dumps(result)
    assert result["prompt_md"] == before
    assert len(result["messages"]) == 3
    assert received[0]["prompt_md"] == before
    assert result["logs"][-1]["event"] == "turn_failed"
    assert "elapsed_seconds" in result["logs"][-1]


def test_more_than_five_turns_preserve_earliest_decisions(client, monkeypatch):
    seen = []
    def complete(session, _skill):
        seen.append(session)
        return model_result(questions=[f"第{len(seen)}轮：**角色动机**需要补充什么？"])
    monkeypatch.setattr(workshop, "_complete", complete)
    data = create(client)
    for i in range(8):
        response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                               json={"message": f"决定{i}", "revision": data["revision"]})
        assert response.status_code == 202
        data = finish(client, data["id"])
    assert len(data["messages"]) == 16
    assert seen[-1]["messages"][0]["content"] == "决定0"
    assert data["messages"][1]["questions"] == ["第1轮：**角色动机**需要补充什么？"]
    assert seen[-1]["messages"][1]["questions"] == data["messages"][1]["questions"]
    assert data["questions"] == ["第8轮：**角色动机**需要补充什么？"]


def test_prepare_confirms_downloads_same_prompt_and_full_relative_command(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    response = client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": data["revision"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revision"] == data["revision"]
    command = result["prepared"]["command"]
    assert "./deploy.sh novel ./prompt/" in command
    assert str(tmp_path) not in command
    assert str(workshop.REPO) not in command
    assert "markdown html docx epub pdf" in command
    assert result["prepared"]["validation"]["status"] == "metadata_and_contracts_valid"
    response = client.get(f'/api/workshop/sessions/{data["id"]}/prompt')
    assert response.text == result["prompt_md"]
    assert "attachment" in response.headers["content-disposition"]
    assert not (tmp_path / "projects").exists()


def test_revision_invalidates_prepared_command(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    data = client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": data["revision"]}).json()
    assert data["prepared"]
    client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={"revision": data["revision"], "message": "修改结局"})
    revised = finish(client, data["id"])
    assert revised["prepared"] is None
    assert client.post(f'/api/workshop/sessions/{data["id"]}/launch', json={"revision": revised["revision"]}).status_code == 409


def test_launch_is_idempotent_and_project_is_immediately_visible(client, monkeypatch, tmp_path):
    calls = []
    from core.pipeline_runner import PipelineRunner
    def run(self, spec):
        calls.append(spec)
        return SimpleNamespace(run_id="run-test", status="completed")
    monkeypatch.setattr(PipelineRunner, "run", run)
    data = ready(client, monkeypatch, tmp_path, method_mode="off")
    data = client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": data["revision"]}).json()
    url = f'/api/workshop/sessions/{data["id"]}/launch'
    launched = client.post(url, json={"revision": data["revision"]})
    assert launched.status_code == 202, launched.text
    again = client.post(url, json={"revision": data["revision"]})
    assert again.json() == launched.json()
    project_id = launched.json()["project_id"]
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert len(calls) == 1
    spec = calls[0]
    assert spec.output_formats == workshop.FORMATS
    assert spec.approval_policy == "auto" and spec.quality_policy == "evidence_v1"
    assert spec.method_policy["mode"] == "off"
    assert Path(spec.prompt_path).read_text() == data["prompt_md"]
    from api import db
    assert project_id in db.projects_for_workspace("default")
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                       json={"revision": data["revision"], "message": "不能再改已启动稿"}).status_code == 409


def test_failed_submit_cleans_only_new_project_and_can_retry(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    data = client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": data["revision"]}).json()
    real = workshop.runner.submit
    monkeypatch.setattr(workshop.runner, "submit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue failed")))
    url = f'/api/workshop/sessions/{data["id"]}/launch'
    assert client.post(url, json={"revision": data["revision"]}).status_code == 503
    assert not (tmp_path / "projects" / data["prepared"]["project_name"]).exists()
    monkeypatch.setattr(workshop.runner, "submit", real)
    from core.pipeline_runner import PipelineRunner
    monkeypatch.setattr(PipelineRunner, "run", lambda *_: SimpleNamespace(run_id="retry", status="completed"))
    assert client.post(url, json={"revision": data["revision"]}).status_code == 202


def test_restart_marks_interrupted_without_losing_prompt(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    service = workshop.WorkshopService(ProjectService(tmp_path / "projects"))
    with service._locked(data["id"]) as stored:
        stored.update(status="running", job_id="job-from-previous-process")
        service._save(stored)
    result = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert result["status"] == "error"
    assert "重启" in result["last_error"]
    assert result["prompt_md"] == data["prompt_md"]


def test_workshop_route_timeout_and_bounded_format_repair(monkeypatch):
    from core import provider_settings
    from core.llm_client import LLMClient
    routes, prompts, configs = [], [], []
    def route(name):
        routes.append(name)
        return {"provider": "codex", "model": "workshop-only", "base_url": "", "api_key": "secret",
                "max_tokens": 16384, "reasoning_effort": "medium", "timeout_seconds": 900}
    monkeypatch.setattr(provider_settings, "resolve_text_route", route)
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kwargs: configs.append(kwargs))
    def complete(self, system, user):
        prompts.append((system, user))
        if len(prompts) < 3:
            return "invalid"
        return json.dumps(model_result(questions=["读者是谁？"])[0])
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.delenv("NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS", raising=False)
    result, meta = workshop._complete({"id": "test", "revision": 1, "input": {},
                                      "messages": [{"role": "user", "content": "one"}], "prompt_md": "old complete draft"}, "SKILL RULES")
    assert routes == ["workshop"]
    assert len(prompts) == 3
    assert configs[0]["timeout_seconds"] == 900
    assert configs[0]["max_tokens"] == 16384
    assert "old complete draft" in prompts[-1][1]
    assert "questions field is a required JSON artifact" in prompts[0][0]
    assert "reply string MUST be reader-facing Markdown" in prompts[0][0]
    assert result["questions"] == ["读者是谁？"]


def test_discussion_markdown_keeps_complete_content_and_internal_code():
    reply = "### 方向建议\n\n- **A：成长**，保留心理动机。\n- **B：反击**，分阶段兑现。\n\n```text\n线索 A → 选择 B\n```\n\n最后一项细节也应完整保留。"
    payload = model_result()[0]
    payload["reply"] = "````markdown\n" + reply + "\n````"
    result = workshop._parse_response(json.dumps(payload), "")
    assert result["reply"] == reply
    payload["reply"] = reply
    assert workshop._parse_response(json.dumps(payload), "")["reply"] == reply


def test_markdown_example_followed_by_prose_and_code_is_not_unwrapped():
    reply = "```markdown\n### 章节标题示例\n```\n\n下面是参数示例：\n\n```python\nchapters = 20\n```"
    payload = model_result()[0]
    payload["reply"] = reply
    assert workshop._parse_response(json.dumps(payload), "")["reply"] == reply


def test_plain_model_reply_has_readable_markdown_heading_without_losing_text():
    payload = model_result()[0]
    original = "已保留全部人物关系。\n\n最后一章的伏笔尚未回收。"
    payload["reply"] = original
    reply = workshop._parse_response(json.dumps(payload), "")["reply"]
    assert reply == "### 本轮说明\n\n" + original


def test_skill_disclosure_is_staged_and_version_stable():
    intake, version = workshop._skill_bundle("intake")
    design, full_version = workshop._skill_bundle("design")
    revision, _ = workshop._skill_bundle("revision")
    assert version == full_version
    assert len(intake) < len(revision) < len(design)
    for bundle in (intake, design, revision):
        for reference in ("assets/novel-skeleton-template.md", "references/prompt-contract.md",
                          "references/narrative-format.md", "references/novel-classification.md"):
            assert f"--- {reference} ---" in bundle


def test_complete_brief_in_premise_can_produce_skeleton_on_first_turn(client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    observed = []
    def complete(session, skill):
        observed.append(session)
        # An empty form is not proof that the writer omitted facts from their prose.
        assert "--- assets/novel-skeleton-template.md ---" in skill
        assert "--- references/prompt-contract.md ---" in skill
        assert "--- references/narrative-format.md ---" in skill
        assert "--- references/novel-classification.md ---" in skill
        return model_result(prompt)
    monkeypatch.setattr(workshop, "_complete", complete)
    data = create(client, premise="21章，每章约550英文词，单本短篇，面向美国成年女性，"
                  "家庭悬疑与成长，六章前不能对质，强化开篇钩子。母亲保存视频并追查账单。")
    assert data["input"]["chapters"] is None and not data["input"]["audience"]
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                           json={"revision": data["revision"], "message": "请依据已给框架完善完整骨架"})
    assert response.status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "ready", result["last_error"]
    assert result["prompt_md"] == prompt
    assert len(observed) == 1
    assert observed[0]["messages"] == [{"role": "user", "content": "请依据已给框架完善完整骨架"}]


def test_valid_transport_with_truncated_skeleton_keeps_previous_version(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    previous = data["prompt_md"]
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result("# Short outline"))
    client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                json={"revision": data["revision"], "message": "加强伏笔"})
    result = finish(client, data["id"])
    assert result["status"] == "error"
    assert result["prompt_md"] == previous
    assert "1–17" in result["last_error"]
    candidates = tmp_path / "workshop_v2" / data["id"] / "candidates"
    assert any(path.read_text() == "# Short outline" for path in candidates.glob("*.md"))


def test_fenced_pending_format_is_confirmed_without_guessing_mode(client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    from core.narrative_format import parse_narrative_format_block
    contract = parse_narrative_format_block(prompt).to_dict()
    contract["confirmation_status"] = "pending_confirmation"
    fenced = "[NARRATIVE_FORMAT_JSON]\n```json\n" + json.dumps(contract) + "\n```\n[/NARRATIVE_FORMAT_JSON]"
    prompt = re.sub(r"\[NARRATIVE_FORMAT_JSON\].*?\[/NARRATIVE_FORMAT_JSON\]", lambda _: fenced, prompt, flags=re.DOTALL)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(prompt))
    data = create(client)
    client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={"revision": 0, "message": "采用这个设计"})
    data = finish(client, data["id"])
    assert data["status"] == "ready", data["last_error"]
    prepared = client.post(f'/api/workshop/sessions/{data["id"]}/prepare', json={"revision": data["revision"]})
    assert prepared.status_code == 200, prepared.text
    assert parse_narrative_format_block(prepared.json()["prompt_md"]).mode == contract["mode"]


def edit_session_file(tmp_path, session_id, **changes):
    path = tmp_path / "workshop_v2" / session_id / "session.json"
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_session_list_keeps_same_title_sessions_distinct_and_recovers_by_id(client, tmp_path, monkeypatch):
    first = create(client, title="同名小说", premise="第一条故事线\n女儿保存证据。")
    second = create(client, title="同名小说", premise="第二条故事线")
    edit_session_file(tmp_path, first["id"], updated_at="2026-10-07T01:00:00+00:00",
                      messages=[{"role": "user", "content": "只属于第一个会话"}])
    edit_session_file(tmp_path, second["id"], updated_at="2026-10-07T02:00:00+00:00")
    monkeypatch.setattr(workshop, "_complete", lambda *_: pytest.fail("Listing must not invoke a model"))
    response = client.get("/api/workshop/sessions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [row["id"] for row in body["sessions"]] == [second["id"], first["id"]]
    assert [row["title"] for row in body["sessions"]] == ["同名小说", "同名小说"]
    assert body["sessions"][1]["message_count"] == 1
    assert body["sessions"][1]["premise_preview"] == "第一条故事线 女儿保存证据。"
    assert set(body["sessions"][0]) == {"id", "title", "status", "revision", "updated_at",
                                       "project_id", "has_prompt", "message_count", "premise_preview"}
    restored_first = client.get(f'/api/workshop/sessions/{first["id"]}').json()
    restored_second = client.get(f'/api/workshop/sessions/{second["id"]}').json()
    assert restored_first["messages"][0]["content"] == "只属于第一个会话"
    assert restored_second["messages"] == []
    assert client.get("/api/projects").json() == []


def test_session_list_uses_current_top_title_and_searches_title_or_uuid_prefix(client, tmp_path):
    first = create(client, title="Old title", premise="x" * 180)
    second = create(client, title="Family Draft")
    untitled = create(client, title="")
    edit_session_file(tmp_path, first["id"], prompt_md="# 骨架\nTitle: Family Final\n\n## 1. Spec\nTitle: Ignore inner title\n",
                      status="ready", revision=4)
    result = client.get("/api/workshop/sessions", params={"q": "  FAMILY  "}).json()
    assert result["total"] == 2
    rows = {row["id"]: row for row in result["sessions"]}
    assert rows[first["id"]]["title"] == "Family Final"
    assert rows[first["id"]]["has_prompt"] is True
    assert rows[first["id"]]["revision"] == 4
    assert rows[first["id"]]["premise_preview"] == "x" * 160 + "…"
    assert rows[second["id"]]["has_prompt"] is False
    assert client.get("/api/workshop/sessions", params={"q": "Old title"}).json()["total"] == 0
    for query in (first["id"], first["id"][:8].upper()):
        found = client.get("/api/workshop/sessions", params={"q": query}).json()
        assert found["total"] == 1 and found["sessions"][0]["id"] == first["id"]
    unnamed = client.get("/api/workshop/sessions", params={"q": untitled["id"]}).json()
    assert unnamed["sessions"][0]["title"] == "未命名作品"


def test_session_list_pagination_is_stable_and_total_is_filtered_count(client, tmp_path):
    entries = [create(client, title="Family") for _ in range(3)]
    for data in entries:
        edit_session_file(tmp_path, data["id"], updated_at="2026-10-07T02:00:00+00:00")
    ids = sorted((entry["id"] for entry in entries), reverse=True)
    page = client.get("/api/workshop/sessions", params={"q": "Family", "limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert [row["id"] for row in page["sessions"]] == [ids[1]]
    assert client.get("/api/workshop/sessions", params={"offset": 50}).json() == {"sessions": [], "total": 3}
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get("/api/workshop/sessions", params=params).status_code == 422


def test_session_list_is_scoped_to_workspace_and_does_not_create_empty_store(client, tmp_path):
    assert client.get("/api/workshop/sessions").json() == {"sessions": [], "total": 0}
    assert not (tmp_path / "workshop_v2").exists()
    default = create(client)
    root = tmp_path / "projects"
    first_service = ProjectService(root, SimpleNamespace(id="workspace-a", slug="workspace-a"))
    second_service = ProjectService(root, SimpleNamespace(id="workspace-b", slug="workspace-b"))
    first = workshop.WorkshopService(first_service).create(workshop.SessionInput(title="A private story"))
    second = workshop.WorkshopService(second_service).create(workshop.SessionInput(title="B private story"))
    assert [row["id"] for row in client.get("/api/workshop/sessions").json()["sessions"]] == [default["id"]]
    default_service = ProjectService(root, SimpleNamespace(id="default", slug="default"))
    client.app.dependency_overrides[workshop.get_service] = lambda: default_service
    assert [row["id"] for row in client.get("/api/workshop/sessions").json()["sessions"]] == [default["id"]]
    client.app.dependency_overrides[workshop.get_service] = lambda: first_service
    assert [row["id"] for row in client.get("/api/workshop/sessions").json()["sessions"]] == [first["id"]]
    assert client.get("/api/workshop/sessions", params={"q": second["id"]}).json()["total"] == 0
    assert client.get(f'/api/workshop/sessions/{second["id"]}').status_code == 404


def test_session_list_skips_corrupt_legacy_non_uuid_and_symlink_records(client, tmp_path):
    healthy = create(client)
    root = tmp_path / "workshop_v2"
    bad_rows = ["invalid json", "[]", json.dumps({"schema": "workshop.v1", "id": "old"}),
                json.dumps({**healthy, "updated_at": "not-a-date"})]
    for body in bad_rows:
        broken = create(client)
        # Even a mostly valid record with an incorrect id must not alias a healthy session.
        (root / broken["id"] / "session.json").write_text(body, encoding="utf-8")
    wrong_type = create(client)
    edit_session_file(tmp_path, wrong_type["id"], messages="corrupted payload")
    invalid_timestamp = create(client)
    edit_session_file(tmp_path, invalid_timestamp["id"], updated_at="not-a-date")
    named = root / "not-a-session-uuid"
    named.mkdir()
    (named / "session.json").write_text(json.dumps(healthy), encoding="utf-8")
    symlink_id = "a" * 32
    (root / symlink_id).symlink_to(root / healthy["id"], target_is_directory=True)
    symlink_file = create(client)
    linked = root / symlink_file["id"] / "session.json"
    linked.unlink()
    linked.symlink_to(root / healthy["id"] / "session.json")
    response = client.get("/api/workshop/sessions")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["sessions"][0]["id"] == healthy["id"]
    assert "corrupted payload" not in response.text


def test_session_list_reconciles_interrupted_model_job_and_preserves_draft(client, tmp_path):
    data = create(client)
    edit_session_file(tmp_path, data["id"], status="running", job_id="lost-after-restart",
                      prompt_md="# Story\nTitle: Preserved Story\n", updated_at="2026-01-01T00:00:00+00:00")
    result = client.get("/api/workshop/sessions").json()
    assert result["total"] == 1
    assert result["sessions"][0]["status"] == "error"
    assert result["sessions"][0]["has_prompt"] is True
    assert result["sessions"][0]["updated_at"] != "2026-01-01T00:00:00+00:00"
    restored = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert restored["prompt_md"] == "# Story\nTitle: Preserved Story\n"
    assert "重启" in restored["last_error"]
    assert restored["logs"][-1]["event"] == "interrupted"


@pytest.mark.parametrize('has_draft', [False, True])
def test_timeout_freezes_actual_limit_and_retry_keeps_complete_context(client, monkeypatch, tmp_path, has_draft):
    from core import provider_settings
    from core.llm_client import LLMClient
    current = {'seconds': 600}
    configs, requests = [], []
    monkeypatch.setattr(provider_settings, 'workshop_timeout_seconds', lambda *_: current['seconds'])
    monkeypatch.setattr(provider_settings, 'resolve_text_route', lambda _: {
        'provider': 'codex', 'model': 'workshop-only', 'base_url': '', 'api_key': '',
        'max_tokens': 16384, 'reasoning_effort': 'medium', 'timeout_seconds': current['seconds']})
    monkeypatch.setattr(LLMClient, '__init__', lambda self, **kwargs: configs.append(kwargs))
    def complete(self, system, user):
        requests.append(json.loads(user))
        if len(requests) == 1:
            current['seconds'] = 1200  # Settings changed while the first call was in progress.
            raise TimeoutError('provider private output')
        return json.dumps({'reply': '### 本轮建议\n\n保留全部设定继续讨论。', 'questions': [],
                           'prompt_md': None, 'ready_for_confirmation': False})
    monkeypatch.setattr(LLMClient, 'complete', complete)
    data = create(client, premise='完整基础提示' * 1500 + '末尾约束必须保留', constraints='第六章前禁止对质')
    messages = []
    for n in range(8):
        messages.extend([{'role': 'user', 'content': f'早期决定{n}'},
                         {'role': 'assistant', 'content': f'早期建议{n}', 'questions': [f'历史问题{n}']}])
    skeleton = complete_prompt(tmp_path) if has_draft else ''
    pending = ['旧版会话仍待回答的最新问题']
    edit_session_file(tmp_path, data['id'], messages=messages, prompt_md=skeleton, questions=pending)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    assert client.post(url, json={'revision': 0, 'message': '保留原设定完善骨架'}).status_code == 202
    failed = finish(client, data['id'])
    assert failed['status'] == 'error'
    assert '本轮上限 600 秒' in failed['last_error']
    assert '1200' not in failed['last_error']
    if has_draft:
        assert '上一版完整骨架已保留' in failed['last_error']
    else:
        assert '尚未生成完整骨架' in failed['last_error']
        assert '上一版' not in failed['last_error']
    assert failed['logs'][-1]['timeout_seconds'] == 600
    assert client.post(url, json={'revision': failed['revision'], 'message': '使用完整上下文重试'}).status_code == 202
    result = finish(client, data['id'])
    assert result['status'] == 'draft'
    assert [item['timeout_seconds'] for item in configs] == [600, 1200]
    assert requests[1]['initial_brief'] == data['input']
    assert requests[1]['conversation'][:16] == messages
    assert requests[1]['conversation'][1]['questions'] == ['历史问题0']
    assert requests[1]['conversation'][-2]['content'] == '保留原设定完善骨架'
    assert requests[1]['conversation'][-1]['content'] == '使用完整上下文重试'
    assert requests[1]['previous_pending_questions'] == pending
    assert requests[1]['current_complete_skeleton'] == skeleton
    assert result['prompt_md'] == skeleton
    assert 'provider private output' not in json.dumps(result)
