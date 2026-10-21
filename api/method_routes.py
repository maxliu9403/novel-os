"""Studio-only advisory controls. These routes never modify manuscript authority."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from artifacts import ArtifactStore, ArtifactError
from narrative_methods import MethodPolicy, ReviewInput
from narrative_methods.runtime import MethodReviews
from narrative_methods.store import MethodStore, MethodConflict

from .jobs import runner, ProjectJobBlocked
from .project_operations import project_operations, ProjectMutationBlocked
from .services import ProjectService, ProjectNotFound, ChapterNotFound


class PolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(max_length=64)
    policy: dict


class ReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    revision_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    retry_of: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


class KeepWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=128)
    finding_index: int = Field(ge=0, strict=True)
    note: str = Field(default="", max_length=2000)


@contextmanager
def _errors():
    try:
        yield
    except (ProjectNotFound, ChapterNotFound, KeyError) as exc:
        raise HTTPException(404, "Project, chapter or revision not found") from exc
    except (MethodConflict, ArtifactError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ProjectJobBlocked, ProjectMutationBlocked) as exc:
        raise HTTPException(409, "Project is not available for review") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def register_method_routes(router, get_service):
    @router.get("/projects/{project_id}/method-reviews/{report_id}/source")
    def source(project_id: str, report_id: str, svc: ProjectService = Depends(get_service)):
        with _errors():
            path = svc.project_path(project_id)
            store = MethodStore(path)
            record = store.read(f"critiques/{store.key(report_id)}/report.json")
            if record is None:
                raise KeyError(report_id)
            report = record["data"]
            artifacts = ArtifactStore(path)
            revision = artifacts.get_revision(report["revision_id"])
            if revision.sha256 != report["candidate_sha256"] or revision.chapter != report["chapter"]:
                raise MethodConflict("reviewed source identity differs")
            return {"revision_id": revision.revision_id, "sha256": revision.sha256,
                    "text": artifacts.read_text(revision.revision_id)}

    @router.get("/projects/{project_id}/method-policy")
    def policy(project_id: str, svc: ProjectService = Depends(get_service)):
        with _errors():
            return MethodStore(svc.project_path(project_id)).policy()

    @router.put("/projects/{project_id}/method-policy")
    def save_policy(project_id: str, body: PolicyWrite, svc: ProjectService = Depends(get_service)):
        with _errors():
            return MethodStore(svc.project_path(project_id)).set_policy(
                MethodPolicy.from_dict(body.policy), body.expected_revision,
            )

    @router.get("/projects/{project_id}/runs/{run_id}/method-lock")
    def method_lock(project_id: str, run_id: str, svc: ProjectService = Depends(get_service)):
        with _errors():
            return MethodReviews(svc.project_path(project_id)).lock(run_id)

    @router.get("/projects/{project_id}/chapters/{number}/method-reviews")
    def method_reviews(project_id: str, number: int, revision_id: str | None = None,
                       svc: ProjectService = Depends(get_service)):
        with _errors():
            svc.chapter_detail(project_id, number)
            path = svc.project_path(project_id)
            runtime = MethodReviews(path)
            runs = []
            root = runtime.store.path("runs")
            if root.exists():
                for directory in sorted(root.iterdir()):
                    runtime.store.key(directory.name)
                    record = runtime.store.read(f"runs/{directory.name}/lock.json")
                    if record:
                        data = record["data"]
                        runs.append({"run_id": data["run_id"], "mode": data["policy"]["mode"],
                                     "status": data["status"], "reason": data["reason"],
                                     "free_trial_end": data["free_trial_end"], "reviewer": data["reviewer"]})
            revisions = [r.to_dict() for r in ArtifactStore(path).history(chapter=number)
                         if r.kind in {"draft", "revised", "final"}]
            reports = runtime.list_reports(number, revision_id)
            decisions = []
            root = runtime.store.path("decisions")
            if root.exists():
                report_ids = {r["report_id"] for r in reports}
                for file in sorted(root.iterdir()):
                    record = runtime.store.read(f"decisions/{file.name}")
                    if record and record["data"]["report_id"] in report_ids:
                        decisions.append(record["data"])
            return {"reports": reports, "runs": runs, "revisions": revisions, "decisions": decisions}

    @router.post("/projects/{project_id}/chapters/{number}/method-review", status_code=202)
    def review(project_id: str, number: int, body: ReviewWrite,
               svc: ProjectService = Depends(get_service)):
        with _errors():
            svc.chapter_detail(project_id, number)
            path = svc.project_path(project_id)
            runtime = MethodReviews(path)
            lock = runtime.lock(body.run_id)["data"]
            if lock["policy"]["mode"] != "advisory" or lock["status"] != "ready":
                raise MethodConflict("run is not configured for advisory review")
            if not lock["free_trial_end"] or number > lock["free_trial_end"]:
                raise MethodConflict("chapter is outside the frozen review scope")
            artifacts = ArtifactStore(path)
            revision = artifacts.get_revision(body.revision_id)
            if revision.chapter != number or revision.kind not in {"draft", "revised", "final"}:
                raise MethodConflict("revision does not belong to this chapter prose")
            contracts = {}
            if revision.chapter_contract_revision_id:
                contracts = {"chapter_contract_revision_id": revision.chapter_contract_revision_id,
                             "chapter_contract": artifacts.read_text(revision.chapter_contract_revision_id)}
            request = ReviewInput(body.revision_id, number, artifacts.read_text(body.revision_id), "", None, contracts)

            def work():
                # Job registry fences deletion. Never hold the ordinary HTTP
                # mutation lease while waiting on a model (writers may keep editing).
                svc.project_path(project_id)
                return runtime.review(body.run_id, request, retry_of=body.retry_of)

            job_id = runner.submit("method_review", work,
                                   {"project_id": project_id, "project_path": str(path), "chapter": number},
                                   unique_key=f"methods:{path}:{body.run_id}:{body.revision_id}:{body.retry_of or ''}",
                                   result_mapper=lambda result: {"report_id": result["report_id"], "review_status": result["status"]})
            return runner.get(job_id)

    @router.post("/projects/{project_id}/method-reviews/{report_id}/keep")
    def keep(project_id: str, report_id: str, body: KeepWrite,
             svc: ProjectService = Depends(get_service)):
        with _errors():
            return MethodReviews(svc.project_path(project_id)).keep(
                report_id, body.finding_index, expected_revision=body.revision_id, note=body.note,
            )
