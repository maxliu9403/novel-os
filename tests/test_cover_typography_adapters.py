"""CLI/HTTP adapters preserve explicit approvals, owner scope and zero-image calls."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import cover_workbench_assets as assets, routes
from core import cover_cli
from core.cover_store import CoverConflict
from core.orchestrator import main
from tests.test_cover_api import _client

SHA = "a" * 64
AMENDMENT = "typography-" + "b" * 32
PROPOSAL = {"schema_version": "cover-typography-amendment.v1", "mode": "overlay", "title_layout": {"title": "A novel"}}


class Flow:
    def __init__(self, root):
        self.store = SimpleNamespace(root=root / "covers", project_path=root)
        self.context = SimpleNamespace(owner_scope="workspace:project:book")
        self.calls = []
        self.error = None

    def validate_candidate(self, cover_set_id, candidate_id):
        self.calls.append((cover_set_id, candidate_id))
        if self.error:
            raise CoverConflict(self.error)


class LocalService:
    def __init__(self):
        self.calls = []
        self.during_review = lambda: None

    def propose_typography_amendment(self, *args, **kwargs):
        self.calls.append(("propose", args, kwargs))
        return {"amendment_id": AMENDMENT, "amendment_sha256": SHA, "approved": False,
                "preview_path": "/private/internal/preview.png", "preview_relative_path": "covers/preview.png"}

    def approve_typography_amendment(self, *args, **kwargs):
        self.calls.append(("approve", args, kwargs))
        if not kwargs["confirm_layout"]:
            raise CoverConflict("Confirm the reviewed layout")
        return {"amendment_id": AMENDMENT, "amendment_sha256": SHA, "approved": True}

    def apply_typography_amendment(self, *args, source_guard, **kwargs):
        self.calls.append(("apply", args, kwargs))
        source_guard()
        self.during_review()
        source_guard()
        return SimpleNamespace(to_dict=lambda: {"cover_set_id": "set", "revision": kwargs["expected_revision"] + 1})


class Queue:
    def __init__(self):
        self.pending = []

    def submit(self, kind, fn, **kwargs):
        self.pending.append((kind, fn, kwargs))
        return "job-local-typography"

    def get(self, job_id):
        return {"job_id": job_id, "status": "running"}


def forbidden(*args, **kwargs):
    raise AssertionError("An image-generation factory must not be used for local typography")


@pytest.fixture
def cli(tmp_path, monkeypatch):
    flow, service = Flow(tmp_path / "book"), LocalService()
    monkeypatch.setattr(cover_cli, "_workflow", lambda project, workspace: flow)
    monkeypatch.setattr(cover_cli, "_mutation_service", lambda: service)
    monkeypatch.setattr(cover_cli, "_review_service", lambda: service)
    monkeypatch.setattr(cover_cli, "_generation_service", forbidden)
    monkeypatch.setattr(cover_cli, "resolve_cover_settings", forbidden)
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps(PROPOSAL))
    common = ["--project", str(flow.store.project_path), "--cover-set", "set", "--candidate", "candidate"]
    return flow, service, proposal, common


def test_cli_requires_three_explicit_steps_and_forwards_exact_hash_cas_and_proposal(cli, capsys):
    flow, service, proposal, common = cli
    assert main(["cover", "propose-typography", *common, "--expected-revision", "7", "--proposal-json", str(proposal)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["approved"] and result["preview_path"].endswith("preview.png")
    assert [call[0] for call in service.calls] == ["propose"]
    assert service.calls[0][2] == {"expected_revision": 7, "proposal": PROPOSAL}
    approval = ["cover", "approve-typography", *common, "--amendment-id", AMENDMENT, "--expected-amendment-sha256", SHA]
    assert main(approval) == 2  # Missing explicit preview confirmation never becomes an apply.
    assert "Confirm" in capsys.readouterr().err
    assert main([*approval, "--confirm-layout"]) == 0
    capsys.readouterr()
    assert main(["cover", "apply-typography", *common, "--expected-revision", "7", "--amendment-id", AMENDMENT,
                 "--expected-amendment-sha256", SHA]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 8
    assert service.calls[-1][1][-1] == AMENDMENT
    assert service.calls[-1][2] == {"expected_revision": 7, "expected_amendment_sha256": SHA}
    assert len(flow.calls) == 6  # Dispatch plus service checks before/after review.


def test_cli_source_guard_and_nonobject_proposal_fail_before_local_mutation(cli, capsys):
    flow, service, proposal, common = cli
    args = ["cover", "propose-typography", *common, "--expected-revision", "7", "--proposal-json", str(proposal)]
    flow.error = "changed source binding"
    assert main(args) == 2 and "changed source" in capsys.readouterr().err
    flow.error = None
    proposal.write_text("[]")
    assert main(args) == 2 and "object" in capsys.readouterr().err
    assert service.calls == []


@pytest.mark.parametrize("command,flags", [
    ("propose-typography", ["--proposal-json", "proposal.json"]),
    ("approve-typography", ["--amendment-id", AMENDMENT]),
    ("apply-typography", ["--amendment-id", AMENDMENT, "--expected-revision", "7"]),
])
def test_cli_missing_cas_or_exact_hash_is_rejected_by_parser(cli, command, flags):
    _, service, _, common = cli
    with pytest.raises(SystemExit) as exc:
        main(["cover", command, *common, *flags])
    assert exc.value.code == 2 and service.calls == []


@pytest.fixture(params=["project", "workshop"])
def http(request, tmp_path, monkeypatch):
    client, image_client = _client(tmp_path, monkeypatch)
    project_id = client.post("/api/projects", json={"title": "Local Typography", "genre": "Drama"}).json()["id"]
    flow, service, queue = Flow(tmp_path / "owner"), LocalService(), Queue()
    flow.context.owner_scope = f"workspace:{request.param}:owner"
    monkeypatch.setattr(assets, "project_workflow", lambda svc, selected: flow)
    monkeypatch.setattr(assets, "workshop_workflow", lambda svc, selected: flow)
    monkeypatch.setattr(assets, "runner", queue)
    client.app.dependency_overrides[routes.get_cover_mutation_service] = lambda: service
    client.app.dependency_overrides[routes.get_cover_review_service] = lambda: service
    client.app.dependency_overrides[routes.get_cover_service] = forbidden
    base = f"/api/projects/{project_id}" if request.param == "project" else "/api/workshop/sessions/session-one"
    base += "/covers/set/candidates/candidate/typography-amendments"
    return client, base, flow, service, queue, image_client, request.param


def test_http_scopes_preview_approval_and_deferred_apply_without_generation_factory(http, monkeypatch):
    client, base, flow, service, queue, image_client, kind = http
    result = client.post(base, json={"expected_revision": 7, "proposal": PROPOSAL})
    assert result.status_code == 201, result.text
    assert "preview_path" not in result.json()
    assert result.json()["preview_url"] == base + f"/{AMENDMENT}/preview"
    assert service.calls[0][2].get("cover_root") == (flow.store.root if kind == "workshop" else None)
    assert client.post(base + f"/{AMENDMENT}/approve", json={"expected_amendment_sha256": SHA, "confirm_layout": False}).status_code == 409
    approved = client.post(base + f"/{AMENDMENT}/approve", json={"expected_amendment_sha256": SHA, "confirm_layout": True})
    assert approved.status_code == 200 and approved.json()["approved"]
    started = client.post(base + f"/{AMENDMENT}/apply", json={"expected_revision": 7, "expected_amendment_sha256": SHA})
    assert started.status_code == 202 and len(queue.pending) == 1
    assert not any(call[0] == "apply" for call in service.calls)
    kind_name, worker, meta = queue.pending[0]
    assert kind_name == "cover.typography" and flow.context.owner_scope in meta["unique_key"]
    assert worker().to_dict()["revision"] == 8
    assert service.calls[-1][2]["expected_amendment_sha256"] == SHA
    assert service.calls[-1][2]["expected_revision"] == 7
    assert len(flow.calls) == 7  # Proposal, approvals, dispatch, worker, and service checks.
    assert image_client.calls == 0


def test_http_source_guard_is_rechecked_when_background_apply_starts(http):
    client, base, flow, service, queue, image_client, _ = http
    result = client.post(base + f"/{AMENDMENT}/apply", json={"expected_revision": 7, "expected_amendment_sha256": SHA})
    assert result.status_code == 202
    flow.error = "source changed after dispatch"
    with pytest.raises(CoverConflict, match="source changed"):
        queue.pending[0][1]()
    assert service.calls == [] and image_client.calls == 0


def test_cli_passes_live_source_guard_through_review(cli, capsys):
    flow, service, _, common = cli
    service.during_review = lambda: setattr(flow, "error", "analysis challenged during review")
    assert main(["cover", "apply-typography", *common, "--expected-revision", "7", "--amendment-id", AMENDMENT,
                 "--expected-amendment-sha256", SHA]) == 2
    assert "analysis challenged" in capsys.readouterr().err
    assert len(flow.calls) == 3


def test_http_passes_live_source_guard_through_review(http):
    client, base, flow, service, queue, image_client, _ = http
    service.during_review = lambda: setattr(flow, "error", "approval revoked during review")
    result = client.post(base + f"/{AMENDMENT}/apply", json={"expected_revision": 7, "expected_amendment_sha256": SHA})
    assert result.status_code == 202
    with pytest.raises(CoverConflict, match="approval revoked"):
        queue.pending[0][1]()
    assert len(flow.calls) == 4 and image_client.calls == 0


@pytest.mark.parametrize("suffix,body", [
    ("", {"expected_revision": True, "proposal": PROPOSAL}),
    ("/approve", {"expected_amendment_sha256": SHA}),
    ("/approve", {"expected_amendment_sha256": SHA, "confirm_layout": "yes"}),
    ("/approve", {"expected_amendment_sha256": SHA, "confirm_layout": True, "title_layout": {}}),
    ("/apply", {"expected_revision": 7, "expected_amendment_sha256": "wrong"}),
    ("/apply", {"expected_revision": 7, "expected_amendment_sha256": SHA, "proposal": PROPOSAL}),
])
def test_http_rejects_implicit_confirmation_inline_rewrites_and_missing_binding_fields(http, suffix, body):
    client, base, flow, service, queue, image_client, _ = http
    url = base if not suffix else base + f"/{AMENDMENT}" + suffix
    result = client.post(url, json=body)
    assert result.status_code == 422, result.text
    assert service.calls == [] and queue.pending == [] and flow.calls == [] and image_client.calls == 0


def test_http_preview_is_owner_scoped_binary_and_integrity_failures_are_not_served(http, monkeypatch):
    from core import cover_typography_amendments as module
    client, base, flow, service, queue, image_client, _ = http
    seen = []
    def read(root, amendment_id, **kwargs):
        seen.append((root, amendment_id, kwargs))
        if amendment_id != AMENDMENT:
            raise ValueError("amendment binding or preview hash mismatch")
        return b"verified png fixture bytes"
    monkeypatch.setattr(module, "read_amendment_preview", read)
    response = client.get(base + f"/{AMENDMENT}/preview")
    assert response.status_code == 200 and response.content == b"verified png fixture bytes"
    assert response.headers["content-type"] == "image/png" and response.headers["cache-control"] == "private, no-store"
    assert seen == [(flow.store.root, AMENDMENT, {"cover_set_id": "set", "candidate_id": "candidate"})]
    assert client.get(base + "/different/preview").status_code == 400
    assert service.calls == [] and image_client.calls == 0


def test_real_cli_amendment_preserves_original_recipe_budget_and_workflow_gate(tmp_path, monkeypatch, capsys):
    from copy import deepcopy
    from dataclasses import replace
    from core.cover_director import CoverArtDirector
    from tests.test_cover_cross_novel_contract import prepare_book, BOOKS
    from tests.test_cover_service import _actual_jpeg, _independent_evaluator, _service

    current = prepare_book(tmp_path, BOOKS[0])
    flow, project = current["flow"], current["project"]
    direction = flow.plan(current["analysis"].analysis_id, CoverArtDirector.from_fixture(current["payload"]))
    flow.approve(direction.direction_id, expected_brief_sha256=direction.brief_sha256,
        approved_direction_sha256=direction.direction_sha256, confirm_source=True)
    brief, concepts, generation = flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    service, image_client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    cover_set = service.generate(project.name, project, brief, concepts, **generation)
    assert len(image_client.prompts) == 4
    candidate = cover_set.candidates[0]
    candidate = replace(candidate, quality_report={**candidate.quality_report, "human_acceptance": {
        "reason": "Acceptance of the previous image only", "image_sha256": candidate.sha256}})
    cover_set = flow.store.save(replace(cover_set, candidates=(candidate, *cover_set.candidates[1:])),
                                expected_revision=cover_set.revision)
    original_candidate = candidate.to_dict()
    original_recipe = deepcopy(cover_set.concepts[0].scene_plan["_compiled_recipe"])
    approval_bytes = flow._approval_path(direction.direction_id).read_bytes()
    proposed_layout = deepcopy(original_recipe["title_layout"])
    proposed_layout["region"] = [.05, .08, .9, .18]
    proposal_path = tmp_path / "new-layout.json"
    proposal_path.write_text(json.dumps({"schema_version": "cover-typography-amendment.v1",
        "mode": "overlay", "title_layout": proposed_layout}))
    monkeypatch.setattr(cover_cli, "_workflow", lambda *args: flow)
    monkeypatch.setattr(cover_cli, "_mutation_service", lambda: service)
    monkeypatch.setattr(cover_cli, "_review_service", lambda: service)
    monkeypatch.setattr(cover_cli, "_generation_service", forbidden)
    monkeypatch.setattr(image_client, "generate", forbidden)
    common = ["--project", str(project), "--cover-set", cover_set.cover_set_id, "--candidate", candidate.candidate_id]
    assert main(["cover", "propose-typography", *common, "--expected-revision", str(cover_set.revision),
                 "--proposal-json", str(proposal_path)]) == 0
    proposal = json.loads(capsys.readouterr().out)
    assert Path(proposal["preview_path"]).is_file() and not proposal["approved"]
    assert flow.store.load(cover_set.cover_set_id).to_dict() == cover_set.to_dict()
    exact = ["--amendment-id", proposal["amendment_id"], "--expected-amendment-sha256", proposal["amendment_sha256"]]
    assert main(["cover", "approve-typography", *common, *exact, "--confirm-layout"]) == 0
    capsys.readouterr()
    assert main(["cover", "apply-typography", *common, *exact, "--expected-revision", str(cover_set.revision)]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["revision"] == cover_set.revision + 1
    saved = flow.store.load(cover_set.cover_set_id)
    revised = saved.candidates[0]
    assert revised.sha256 == proposal["preview_sha256"] != candidate.sha256
    assert revised.attempt_history[:-1] == candidate.attempt_history
    assert saved.concepts[0].scene_plan["_compiled_recipe"] == original_recipe
    assert "human_acceptance" not in revised.quality_report
    assert flow._approval_path(direction.direction_id).read_bytes() == approval_bytes
    assert len(image_client.prompts) == 4
    flow.validate_candidate(saved.cover_set_id, revised.candidate_id)
    with pytest.raises(CoverConflict, match="does not authorize image generation"):
        flow.validate_candidate(saved.cover_set_id, revised.candidate_id, for_generation=True)
    assert candidate.to_dict() == original_candidate
    # Deleting one candidate receipt cannot turn the changed pixels back into a legacy candidate.
    bad_report = deepcopy(revised.quality_report)
    bad_report.pop("typography_amendment")
    flow.store.save(replace(saved, candidates=(replace(revised, quality_report=bad_report), *saved.candidates[1:])),
                    expected_revision=saved.revision)
    with pytest.raises(CoverConflict, match="receipt"):
        flow.validate_candidate(saved.cover_set_id, revised.candidate_id)
