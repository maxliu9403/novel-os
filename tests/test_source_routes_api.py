"""Route HTTP tenancy, author input and request-local dispatch; no model/network.

Core persistence/coverage is exercised in test_source_story_design. This suite
replaces only its public interface and the job adapter to control crash/retry
windows without racing daemon workers.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from api.main import create_app
from api import source_routes
from api.services import ProjectService
from core.creative_types import digest
from core.reference_assets import atomic_json
from test_source_library import bundle

_REAL_DESIGN_FOR = source_routes.design_for


class DeferredRunner:
    def __init__(self):
        self.jobs = {}
        self.functions = {}

    def submit(self, kind, fn, meta=None, **options):
        key = uuid.uuid4().hex
        self.jobs[key] = {
            "job_id": key, "kind": kind, "status": "running",
            "cancel_requested": False, "meta": meta,
            "event": options["cancel_event"],
        }
        self.functions[key] = fn
        return key

    def get(self, job_id):
        row = self.jobs.get(job_id)
        return {k: v for k, v in row.items() if k != "event"} if row else None

    def cancel(self, job_id):
        row = self.jobs[job_id]
        row["event"].set()
        row["cancel_requested"] = True
        return True

    def finish(self, job_id):
        self.functions[job_id]()
        self.jobs[job_id]["status"] = "done"


class DesignAdapter:
    def __init__(self):
        self.runs = {}
        self.inputs = []
        self.executions = []
        self.route_sets = []

    def list_runs(self):
        return deepcopy(list(self.runs.values()))

    def prepare(self, request, *, model, catalog):
        for row in self.runs.values():
            if row["request"]["operation_id"] == request["operation_id"]:
                if digest(row["request"]) != digest(request):
                    raise ValueError("操作标识已用于不同请求。")
                return deepcopy(row)
        identifier = uuid.uuid4().hex
        row = {
            "run_id": identifier, "status": "queued", "stage": "prepared",
            "completed": 0, "total": 1, "model_calls": 0,
            "created_at": f"2026-11-11T12:{len(self.runs):02d}:00Z",
            "request": deepcopy(request), "model": deepcopy(model),
        }
        self.inputs.append({"request": deepcopy(request), "model": deepcopy(model), "catalog": deepcopy(catalog)})
        self.runs[identifier] = row
        return deepcopy(row)

    def status(self, run_id):
        if run_id not in self.runs:
            raise ValueError("找不到路线任务。")
        return deepcopy(self.runs[run_id])

    def run(self, run_id, client, cancelled):
        self.executions.append((run_id, client, cancelled.is_set()))
        self.runs[run_id].update(status="complete", completed=1)
        return self.status(run_id)

    def pause(self, run_id):
        self.runs[run_id]["status"] = "paused"

    def list_route_sets(self):
        return deepcopy(self.route_sets)

    def get_route_set(self, sha):
        for item in self.route_sets:
            if item["route_set_sha256"] == sha:
                return deepcopy(item)
        raise ValueError("找不到此路线版本。")

    def get_understanding(self, sha):
        return {
            "narrative_endpoint": {"event": "Trust changed in the last supplied scene."},
            "opening_analysis": "The first scene makes the cost of silence visible.",
            "summary_projection": "All supplied scenes were considered.",
            "open_threads": [{"question": "Who will keep the promise?"}],
            "private_transport": {"path": "/not-for-public-output"},
        }


@pytest.fixture
def routes_api(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    projects = ProjectService(tmp_path / "projects")
    app = create_app(projects_root=projects.root, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    client = TestClient(app)
    library = source_routes.source_store(projects)
    manifest = library.import_files(bundle())
    engines = {}
    jobs = DeferredRunner()
    route = {
        "provider": "fixture", "model": "model-a", "base_url": "https://private.invalid/v1",
        "api_key": "private-test-key", "max_tokens": 8192, "timeout_seconds": 90,
        "planning_input_bytes": 123456, "connection_id": "connection-a",
    }
    created_clients = []

    def design_for(current_projects, source_id):
        source_routes.source_store(current_projects).get(source_id)
        return engines.setdefault(source_id, DesignAdapter())

    def client_for(binding, cancel):
        value = SimpleNamespace(binding=deepcopy(binding), cancel=cancel)
        created_clients.append(value)
        return value

    monkeypatch.setattr(source_routes, "design_for", design_for)
    monkeypatch.setattr(source_routes, "runner", jobs)
    monkeypatch.setattr(source_routes, "_resolve_route", lambda: route)
    monkeypatch.setattr(source_routes, "_client", client_for)
    return SimpleNamespace(
        client=client, projects=projects, library=library, source_id=manifest["id"],
        engines=engines, jobs=jobs, route=route, clients=created_clients,
        prefix=f"/api/source-books/{manifest['id']}",
    )


def payload(**changes):
    return {
        "operation_id": "author-request-001", "operation": "initial_routes",
        "mode": "source_development", "feedback": "  保留她的选择。\n加强事业线。  ",
        "author_context": {"premise": "  My original words.\n", "audience_channel": "female"},
        **changes,
    }


def test_dispatch_captures_settings_keeps_author_exact_and_excludes_credentials(routes_api):
    env = routes_api
    response = env.client.post(env.prefix + "/route-runs", json=payload())
    assert response.status_code == 202, response.text
    run = response.json()
    prepared = env.engines[env.source_id].inputs[0]
    assert prepared["request"]["feedback"] == payload()["feedback"]
    assert prepared["request"]["author_context"] == payload()["author_context"]
    assert prepared["model"]["planning_input_bytes"] == 123456
    assert "api_key" not in prepared["model"] and "base_url" not in prepared["model"]
    assert prepared["catalog"] and all(row["version"] and row["sha256"] for row in prepared["catalog"])
    env.route.update(model="model-b", api_key="different-key")
    env.jobs.finish(run["job_id"])
    assert env.clients[0].binding["model"] == "model-a"
    assert env.clients[0].binding["api_key"] == "private-test-key"


def test_same_operation_retries_without_settings_or_catalog_and_rejects_changed_body(routes_api, monkeypatch):
    env = routes_api
    original = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    monkeypatch.setattr(source_routes, "_resolve_route", lambda: pytest.fail("retry resolved new settings"))
    monkeypatch.setattr(source_routes, "catalog_for", lambda _: pytest.fail("retry replaced catalog snapshot"))
    retried = env.client.post(env.prefix + "/route-runs", json=payload())
    assert retried.status_code == 202
    assert retried.json()["run_id"] == original["run_id"]
    assert len(env.jobs.jobs) == 1
    conflict = env.client.post(env.prefix + "/route-runs", json=payload(feedback="Different instructions"))
    assert conflict.status_code == 409
    assert len(env.engines[env.source_id].inputs) == 1


def test_gets_do_not_create_design_data_or_change_legacy_analysis(routes_api):
    env = routes_api
    old = env.library.path(env.source_id) / "study" / "result.json"
    atomic_json(old, {"legacy": "preserve exactly"})
    before = old.read_bytes()
    assert env.client.get(env.prefix + "/route-runs").json() == {"items": []}
    assert env.client.get(env.prefix + "/route-sets").json() == {"items": [], "runs": [], "active_run": None}
    assert old.read_bytes() == before
    assert not (env.library.path(env.source_id) / "story-design").exists()


def test_partial_explicit_selection_is_kept_and_stale_or_fake_refs_are_rejected(routes_api):
    env = routes_api
    row = next(r for r in source_routes.catalog_for(env.projects) if r["kind"] == "genre")
    ref = {"id": row["id"], "version": row["version"], "source_sha256": row["sha256"]}
    context = {"audience_channel": "female", "creative_types": [ref]}
    accepted = env.client.post(env.prefix + "/route-runs", json=payload(author_context=context))
    assert accepted.status_code == 202, accepted.text
    assert env.engines[env.source_id].inputs[0]["request"]["author_context"] == context
    bad = {**ref, "source_sha256": "0" * 64}
    invalid = env.client.post(env.prefix + "/route-runs", json=payload(
        operation_id="author-request-002", author_context={"creative_types": [bad]},
    ))
    assert invalid.status_code == 409
    assert len(env.jobs.jobs) == 1
    assert env.client.post(env.prefix + "/route-runs", json=payload(catalog=[])).status_code == 422


def test_pause_and_resume_use_current_connection_without_changing_original_request(routes_api):
    env = routes_api
    original = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    path = env.prefix + "/route-runs/" + original["run_id"]
    paused = env.client.post(path + "/pause").json()
    assert paused["cancel_requested"] and paused["status"] == "paused"
    # A draining request cannot be started a second time.
    env.client.post(path + "/resume", json={})
    assert len(env.jobs.jobs) == 1
    env.jobs.jobs[original["job_id"]]["status"] = "error"
    env.route["model"] = "model-b"
    resumed = env.client.post(path + "/resume", json={})
    assert resumed.status_code == 202
    env.jobs.finish(resumed.json()["job_id"])
    assert env.clients[-1].binding["model"] == "model-b"
    assert env.engines[env.source_id].inputs[0]["model"]["model"] == "model-a"
    assert env.engines[env.source_id].inputs[0]["request"]["feedback"] == payload()["feedback"]


def test_missing_worker_is_read_only_interruption_and_explicit_resume_restores_dispatch(routes_api):
    env = routes_api
    original = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    env.jobs.jobs.clear()
    path = env.prefix + "/route-runs/" + original["run_id"]
    before = env.engines[env.source_id].status(original["run_id"])
    status = env.client.get(path).json()
    assert status["interrupted"] and not status["worker_active"]
    assert env.engines[env.source_id].status(original["run_id"]) == before
    restored = env.client.post(path + "/resume", json={})
    assert restored.status_code == 202 and restored.json()["worker_active"]


def test_separate_books_capture_separate_models_and_cancellation(routes_api):
    env = routes_api
    first = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    files = bundle()
    files["chapters/2.md"] += b" A different book has a different promise."
    second_book = env.library.import_files(files)
    second_prefix = "/api/source-books/" + second_book["id"]
    env.route["model"] = "model-b"
    second = env.client.post(second_prefix + "/route-runs", json=payload()).json()
    env.client.post(env.prefix + "/route-runs/" + first["run_id"] + "/pause")
    assert env.jobs.jobs[first["job_id"]]["event"].is_set()
    assert not env.jobs.jobs[second["job_id"]]["event"].is_set()
    env.jobs.finish(second["job_id"])
    assert env.clients[-1].binding["model"] == "model-b"
    assert env.engines[second_book["id"]].executions[-1][2] is False


def test_worker_initialization_error_is_visible_without_exposing_provider_error(routes_api):
    env = routes_api
    run = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    env.jobs.jobs[run["job_id"]].update(status="error", error="private-test-key at /private/config")
    response = env.client.get(env.prefix + "/route-runs/" + run["run_id"])
    assert response.status_code == 200
    assert response.json()["interrupted"] and response.json()["error"]
    assert "private-test-key" not in response.text and "/private/config" not in response.text


def test_compound_feedback_and_complete_options_survive_http_boundary(routes_api):
    env = routes_api
    request = payload(
        operation="revise_routes", base_route_set_sha256="a" * 64,
        target_route_ids=["route_a"], requested_actions=[
            {"operation": "revise_routes", "target_route_ids": ["route_a"]},
            {"operation": "expand_routes", "requested_count": 2},
        ],
    )
    response = env.client.post(env.prefix + "/route-runs", json=request)
    assert response.status_code == 202
    prepared = env.engines[env.source_id].inputs[0]["request"]
    assert [a["operation"] for a in prepared["requested_actions"]] == ["revise_routes", "expand_routes"]
    route_set = {
        "route_set_sha256": "b" * 64, "understanding_sha256": "c" * 64,
        "routes": [{"route_id": "route_a", "causal_arc": "Opening through an explicit ending."}],
    }
    env.engines[env.source_id].route_sets = [route_set]
    received = env.client.get(env.prefix + "/route-sets/" + "b" * 64).json()
    assert received["routes"] == route_set["routes"]
    assert received["understanding"]["opening_analysis"]
    assert "private_transport" not in received["understanding"]
    assert env.client.get(env.prefix + "/route-sets").json()["items"] == [route_set]


def test_foreign_source_run_and_path_references_are_not_resolved(routes_api):
    env = routes_api
    absent = "/api/source-books/source-" + "f" * 64
    assert env.client.get(absent + "/route-sets").status_code == 404
    assert env.client.post(absent + "/route-runs", json=payload()).status_code == 404
    assert env.client.get(env.prefix + "/route-runs/not-a-run").status_code == 404
    assert env.client.get(env.prefix + "/route-sets/not-a-sha").status_code == 404
    assert not env.jobs.jobs


def test_symlinked_job_metadata_is_rejected_without_reading_target(routes_api, tmp_path):
    env = routes_api
    original = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    job_path = source_routes._job_path(env.projects, env.source_id, original["run_id"])
    job_path.unlink()
    secret = tmp_path / "private.json"
    secret.write_text(json.dumps({"secret": "must not appear"}))
    job_path.symlink_to(secret)
    response = env.client.get(env.prefix + "/route-runs/" + original["run_id"])
    assert response.status_code == 404
    assert "must not appear" not in response.text


class StoryModel:
    """Schema-valid fixture, reading the actual host-provided source IDs."""
    provider_name = "fixture"
    planning_input_bytes = None

    def __init__(self, binding):
        self.model = binding["model"]
        self.requests = []

    def complete(self, system, user):
        request = json.loads(user)
        self.requests.append((system, request))
        source = request["source_view"]
        references = [row["id"] for row in source] or [row["id"] for row in request["evidence_view"]]
        slots = request["task"]["allowed_output_slots"]
        catalog = request["valid_type_catalog"]
        genre = next(row["id"] for row in catalog if row["kind"] == "genre")
        tag = next(row["id"] for row in catalog if row["kind"] == "tag")
        routes = []
        for slot in slots:
            routes.append({
                "slot": slot, "title": "A choice with consequences " + slot,
                "reader_promise": "See the protagonist reclaim her decision without losing earned trust.",
                "continuity_basis": "Continue after the last supplied choice.",
                "source_refs": references,
                "causal_arc": {
                    "opening": "She refuses the unfair condition in front of her partner.",
                    "development": "The refusal costs the shared contract and exposes their dependence.",
                    "turning_point": "She discloses her own mistake and asks for an equal agreement.",
                    "climax": "They defend the new agreement together at the public hearing.",
                    "ending": "She keeps the business and freely chooses to rebuild trust.",
                },
                "character_relationship_arcs": ["Dependence becomes negotiated cooperation."],
                "emotional_design": [{
                    "expectation": "Her refusal should matter.", "scene": "She returns the signed agreement.",
                    "choice": "She gives up immediate security.", "cost": "The contract is cancelled.",
                    "change": "Her partner must negotiate instead of command.",
                    "payoff": "A fair agreement is accepted at the hearing.",
                }],
                "highlights": [{"scene": "She returns the agreement in public.", "story_value": "Her autonomy changes the conflict."}],
                "long_hooks_and_payoffs": [{"question": "Can trust survive an equal relationship?", "development": "Both must accept accountability.", "payoff": "They choose a new agreement freely."}],
                "type_suggestion": {"audience": "female", "genre_id": genre, "tag_ids": [tag], "reason": "Relationship choice drives the emotional payoff."},
                "proposed_assumptions": ["The hearing is new proposed fiction."],
                "tradeoffs": ["The decision carries a financial cost."],
                "difference_from_existing": "The protagonist uses open negotiation rather than concealment.",
                "changes_from_parent": "Developed the consequences of the author's latest request.",
            })
        result = {"routes": routes, "recommended_slot": slots[0] if slots else "", "recommendation_reason": "The choice develops the supplied trust conflict."}
        if request["task"]["operation"] == "initial_routes" and "understanding" in request["response_schema"].get("properties", {}):
            result["understanding"] = {
                "story_summary": "A choice changes trust across the complete supplied story.",
                "narrative_endpoint": "The last supplied chapter ends with that changed trust.",
                "opening_analysis": "The opening makes the cost of silence visible.",
                "open_threads": ["The early promise remains unfulfilled."],
                "observations": [{"local_id": "observation_1", "kind": "open_thread", "statement": "The early promise is unresolved.", "source_refs": references, "supersedes": []}],
            }
        return json.dumps(result)


def test_real_core_http_roundtrip_retains_old_analysis_and_runs_revise_then_expand(routes_api, monkeypatch):
    env = routes_api
    monkeypatch.setattr(source_routes, "design_for", _REAL_DESIGN_FOR)
    clients = []

    def client_for(binding, cancel):
        model = StoryModel(binding)
        clients.append(model)
        return model

    monkeypatch.setattr(source_routes, "_client", client_for)
    source_path = env.library.path(env.source_id)
    old = source_path / "study" / "result.json"
    atomic_json(old, {"continuation_plan": {"premise": "Historical analysis stays readable."}})
    protected = {str(p.relative_to(source_path)): p.read_bytes() for p in source_path.rglob("*") if p.is_file()}

    first = env.client.post(env.prefix + "/route-runs", json=payload()).json()
    env.jobs.finish(first["job_id"])
    finished = env.client.get(env.prefix + "/route-runs/" + first["run_id"]).json()
    assert finished["status"] == "ready", finished
    assert finished["model_calls"] == 1
    first_set = env.client.get(env.prefix + "/route-sets/" + finished["route_set_sha256"]).json()
    assert len(first_set["routes"]) == 3
    assert first_set["understanding"]["summary_projection"]
    assert "顶级商业小说资料研读与整书开发主编" in clients[0].requests[0][0]
    assert env.client.get(env.prefix + "/route-sets").json()["active_run"] is None

    target = first_set["routes"][0]["route_id"]
    later = payload(
        operation_id="author-request-compound", operation="revise_routes",
        feedback="  保留她的结局，完善第一条，再给两条不同方向。\n",
        base_route_set_sha256=first_set["sha256"], target_route_ids=[target],
        requested_actions=[
            {"operation": "revise_routes", "target_route_ids": [target]},
            {"operation": "expand_routes", "requested_count": 2},
        ],
    )
    second = env.client.post(env.prefix + "/route-runs", json=later)
    assert second.status_code == 202, second.text
    env.jobs.finish(second.json()["job_id"])
    second_state = env.client.get(env.prefix + "/route-runs/" + second.json()["run_id"]).json()
    assert second_state["status"] == "ready", second_state
    revised = env.client.get(env.prefix + "/route-sets/" + second_state["route_set_sha256"]).json()
    assert len(revised["routes"]) == 5
    assert revised["routes"][1:3] == first_set["routes"][1:3]
    assert revised["routes"][0]["parent_sha256"] == first_set["routes"][0]["sha256"]
    assert revised["understanding_sha256"] == first_set["understanding_sha256"]
    assert revised["feedback_history"][-1]["exact_text"] == later["feedback"]
    assert revised["feedback_history"][0]["exact_text"] == payload()["feedback"]
    assert env.client.get(env.prefix + "/route-sets/" + first_set["sha256"]).json() == first_set
    for relative, original in protected.items():
        assert (source_path / relative).read_bytes() == original

    # A successful operation can be fetched/retried even after worker loss;
    # this never dispatches the model again or creates a new artifact.
    env.jobs.jobs.clear()
    repeated = env.client.post(env.prefix + "/route-runs", json=later)
    assert repeated.json()["run_id"] == second.json()["run_id"]
    env.client.post(env.prefix + "/route-runs/" + second.json()["run_id"] + "/resume", json={})
    assert not env.jobs.jobs and len(clients) == 2
