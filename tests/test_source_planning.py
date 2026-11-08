"""Complete reading, proposals and reusable version binding; no paid model calls."""

import json

import pytest
from test_source_library import bundle, plan, response
from test_workshop_api import client  # noqa: F401

from core.source_library import SourceLibrary
from core.source_study import PLANNED_VERSION, SourceStudy
from core.source_versions import SourceVersions


def study_for(tmp_path, large=False):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle(large=large))
    return SourceStudy(library, book["id"])


@pytest.mark.parametrize("large", [False, True])
def test_plan_shares_final_read_and_never_contaminates_observations(tmp_path, large):
    study = study_for(tmp_path, large)
    state = study.prepare({"model": "fixture"}, with_plan=True)
    calls = []

    def complete(system, user):
        payload = json.loads(user)
        calls.append(payload)
        assert len(system.encode()) + len(user.encode()) < 119000
        assert "All NEW characters are 18+" in system
        final = "continuation_plan" in payload["response_contract"]["properties"]
        return "```json\n" + response(planned=final) + "\n```"

    result = study.run(complete)
    assert len(calls) == state["total"] == 2 * state["reading_batches"] - 1
    assert (
        sum("continuation_plan" in c["response_contract"]["properties"] for c in calls)
        == 1
    )
    assert result["analysis"] == json.loads(response())
    assert result["continuation_plan"] == plan()
    assert result["schema"] == PLANNED_VERSION
    assert study.result(result["analysis_sha256"]) == result
    assert study.prepare({"model": "different"}, with_plan=True)["status"] == "complete"


def test_legacy_analysis_gets_one_plan_call_and_remains_selectable_during_reanalysis(
    tmp_path,
):
    study = study_for(tmp_path)
    study.prepare({"model": "old"})
    old = study.run(lambda *_: response())
    state = study.prepare({"model": "new"}, with_plan=True)
    assert state["total"] == 1
    assert study.result(old["analysis_sha256"]) == old
    calls = []

    def complete(system, user):
        payload = json.loads(user)
        calls.append(payload)
        assert payload["analysis"] == old["analysis"]
        assert "segments" not in payload
        return json.dumps(plan())

    new = study.run(complete)
    assert len(calls) == 1 and new["analysis"] == old["analysis"]
    study.prepare({"model": "next"}, restart=True, with_plan=True)
    assert study.result(old["analysis_sha256"]) == old
    assert study.result(new["analysis_sha256"]) == new
    assert len(SourceVersions(study.library, study.source_id).summaries()) == 2


def test_failed_plan_can_resume_only_explicitly_and_keeps_reading_version(tmp_path):
    study = study_for(tmp_path)
    study.prepare({"model": "fixture"})
    old = study.run(lambda *_: response())
    study.prepare({"model": "fixture"}, with_plan=True)
    with pytest.raises(ValueError, match="格式无效"):
        study.run(lambda *_: '{"premise":"unfinished"}')
    assert study.result(old["analysis_sha256"]) == old
    study.prepare({"model": "fixture"}, with_plan=True)
    assert len(list(study.root.glob("failed-*.json"))) == 1
    study.run(lambda *_: json.dumps(plan()))
    assert study.status()["status"] == "complete"


def test_version_lookup_rejects_unknown_and_tampered_receipts(tmp_path):
    study = study_for(tmp_path)
    study.prepare({"model": "fixture"}, with_plan=True)
    result = study.run(lambda *_: response(planned=True))
    for sha in ("../state", "a" * 64):
        with pytest.raises(ValueError):
            study.result(sha)
    for path in [
        study.root / "result.json",
        study.root / "results" / f"{result['analysis_sha256']}.json",
    ]:
        changed = dict(result, continuation_plan=plan() | {"premise": "tampered"})
        path.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        study.result(result["analysis_sha256"])


def test_history_selection_freezes_plan_and_tracks_discussions(client, tmp_path):
    from test_workshop_api import create

    from api.routes import get_service
    from api.source_library import store_for

    projects = client.app.dependency_overrides[get_service]()
    library = store_for(projects)
    imported = library.import_files(bundle())
    study = SourceStudy(library, imported["id"])
    study.prepare({"model": "fixture"}, with_plan=True)
    old = study.run(lambda *_: response(planned=True))
    study.prepare({"model": "new"}, restart=True, with_plan=True)
    source = {
        "source_id": imported["id"],
        "analysis_sha256": old["analysis_sha256"],
        "mode": "source_development",
        "use_cover": False,
    }
    # Current execution need not be complete to reuse a valid historical receipt.
    session = create(client, source_book=source)
    assert session["source_context"]["continuation_plan"] == plan()
    assert session["source_context"]["planning_status"] == "proposal_not_adopted"
    detail = client.get(
        f"/api/source-books/{imported['id']}?version={old['analysis_sha256']}"
    )
    assert detail.status_code == 200
    assert detail.json()["result"] == old
    usage = client.get(f"/api/source-books/{imported['id']}/usage")
    assert usage.status_code == 200, usage.text
    assert usage.json()["counts"]["discussions"] == 1
    assert usage.json()["sessions"][0]["id"] == session["id"]
    from core.reference_assets import atomic_json

    for title in ("First continuation", "Second continuation"):
        project = projects.create_project(title)
        atomic_json(projects.base_dir / project.id / "outputs/private/source-context.json", session["source_context"])
    usage = client.get(f'/api/source-books/{imported["id"]}/usage').json()
    assert usage["counts"] == {"discussions": 1, "projects": 2, "completed": 0, "in_progress": 2}
    listing = client.get('/api/projects').json()
    assert all(p['source']['source_id'] == imported['id'] for p in listing)
    assert all('analysis' not in p['source'] for p in listing)


def test_real_discussion_request_receives_plan_as_proposal(tmp_path, monkeypatch):
    from test_workshop_api import model_result
    from test_workshop_compact_discussion import ready_session
    from test_workshop_contract_repair import stub_model

    from api import workshop
    from core.llm_client import LLMClient

    session = ready_session()
    session["source_context"] = {
        "mode": "source_development",
        "continuation_plan": plan(),
        "analysis": {"observed_age": "Dora is five"},
    }
    calls = stub_model(monkeypatch, lambda *_: json.dumps(model_result()[0]))
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kw: None)
    workshop._complete(session, workshop._skill_bundle()[0])
    assert len(calls) == 1
    context = json.loads(calls[0][1])
    assert context["source_reference"]["continuation_plan"] == plan()
    assert "Dora is five" in calls[0][1]
    assert "not adopted canon" in context["source_reference_policy"]


def test_project_provenance_updates_without_loading_analysis(tmp_path):
    from core.reference_assets import atomic_json
    from core.source_provenance import project_source

    source = {
        "source_id": "source-" + "a" * 64,
        "title": "Reference",
        "analysis_sha256": "b" * 64,
        "mode": "source_development",
    }
    path = tmp_path / "outputs/private/source-context.json"
    atomic_json(path, {**source, "analysis": {"large_private_text": "excluded"}})
    assert project_source(tmp_path) == source
    changed = {**source, "analysis_sha256": "c" * 64}
    atomic_json(path, changed)
    assert project_source(tmp_path) == changed
    path.unlink()
    assert project_source(tmp_path) is None
