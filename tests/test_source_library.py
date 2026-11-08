"""Intake, complete reading and recovery with generated fixtures; no network."""

import json
import stat
from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest
from test_workshop_api import client  # noqa: F401

from core.source_library import SourceLibrary, inspect_bundle, unpack_zip
from core.source_study import Analysis, ContinuationState, SourceStudy, batches


def bundle(*, large=False, extra=True):
    chapters = [
        {"index": n, "path": f"chapters/{n}.md", "title": f"Chapter {n}"}
        for n in (1, 2)
    ]
    files = {
        "metadata.json": json.dumps(
            {"novel_name": "Source Story", "book_id": "book1", "chapters": chapters}
        ).encode(),
        "chapters.json": json.dumps(chapters).encode(),
        "synopsis.md": b"Official reader promise.",
        "chapters/1.md": ("First scene.\n" * (24000 if large else 3)).encode(),
        "chapters/2.md": b"Second scene, a choice changes trust.",
    }
    if extra:
        files["chapters/appendix.md"] = b"Announcement is not a story ending."
    return files


def plan():
    return {
        "premise": "She returns to negotiate her own future.",
        "overall_arc": "An uneasy alliance changes who can make the decision.",
        "proposed_ending": "She keeps her autonomy and chooses the relationship.",
        "cards": [{"title": f"Proposed unit {n}", "pov": "Amelia", "source_anchor": "chapters/2.md: trust changes",
                   "core_plot": ["She offers a new condition.", "He refuses the old arrangement."],
                   "reader_payoff": "Her decision matters.", "foreshadowing": "A promise becomes a test.",
                   "ending_hook": "An ally arrives with a competing proposal."} for n in range(3)],
        "major_choices": ["Keep the alliance or leave?"], "source_conflicts": [],
    }


def response(*, planned=False):
    return json.dumps(
        {
            "story_summary": "Only supplied scenes are known.",
            **{k: [] for k in Analysis.model_fields if k not in {"story_summary", "continuation"}},
            "continuation": {k: [] for k in ContinuationState.model_fields},
            **({"continuation_plan": plan()} if planned else {}),
        }
    )


def test_import_directory_and_zip_are_identical_and_all_supplied_text_is_sent(tmp_path):
    files = bundle(large=True)
    stream = BytesIO()
    with ZipFile(stream, "w") as z:
        for path, body in files.items():
            z.writestr("Book__1/" + path, body)
    library = SourceLibrary(tmp_path / "source")
    imported = library.import_files(files)
    assert library.import_files(unpack_zip(stream)) == imported
    assert imported["supplement_count"] == 1 and imported["chapter_count"] == 2
    groups = batches(library, imported["id"])
    parts = [p for group in groups for p in group]
    assert len(groups) > 1
    for path in [
        "synopsis.md",
        "chapters/1.md",
        "chapters/2.md",
        "chapters/appendix.md",
    ]:
        assert (
            "".join(p["text"] for p in parts if p["path"] == path).encode()
            == files[path]
        )
    assert parts[-1]["scope"] == "unlisted_supplement_not_ordered_chapter"
    study = SourceStudy(library, imported["id"])
    study.prepare({"model": "fixture"})
    calls = []

    def complete(system, user):
        calls.append(json.loads(user))
        return response()

    receipt = study.run(complete)
    assert len(calls) == 2 * len(groups) - 1
    assert receipt["coverage"]["files"] == 4
    assert study.result() == receipt
    assert study.prepare({"model": "fixture"})["status"] == "complete"


def test_pause_resumes_saved_segments_without_resending(tmp_path):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle(large=True))
    study = SourceStudy(library, book["id"])
    study.prepare({"model": "fixture"})
    calls = []

    def complete(*args):
        calls.append(args)
        return response()

    with pytest.raises(InterruptedError):
        study.run(complete, lambda: len(calls) == 1)
    assert study.status()["completed"] == 1
    study.prepare({"model": "fixture"})
    study.run(complete)
    assert len(calls) == study.status()["total"]


def test_explicit_retry_replaces_only_invalid_response_and_retains_evidence(tmp_path):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle())
    study = SourceStudy(library, book["id"])
    study.prepare({"model": "fixture"})
    with pytest.raises(ValueError, match="格式无效"):
        study.run(lambda *_: "invalid response")
    assert len(list(study.root.glob("0000.json"))) == 1
    study.prepare({"model": "fixture"})
    assert len(list(study.root.glob("failed-*.json"))) == 1
    study.run(lambda *_: response())
    assert study.status()["status"] == "complete"


@pytest.mark.parametrize(
    "bad",
    [
        "../metadata.json",
        "/metadata.json",
        "book/../metadata.json",
        "book\\metadata.json",
    ],
)
def test_reject_unsafe_names_before_writes(tmp_path, bad):
    files = bundle()
    files[bad] = files.pop("metadata.json")
    with pytest.raises(ValueError):
        SourceLibrary(tmp_path).import_files(files)
    assert not list(tmp_path.iterdir())


def test_source_content_or_manifest_tamper_blocks_resume(tmp_path):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle())
    root = library.path(book["id"])
    (root / "original/chapters/1.md").write_text("tampered")
    with pytest.raises(ValueError):
        batches(library, book["id"])
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["units"] = []
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="身份"):
        library.get(book["id"])


def test_symlink_and_duplicate_zip_rejected():
    stream = BytesIO()
    with ZipFile(stream, "w") as z:
        entry = ZipInfo("cover.jpg")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(entry, "/etc/passwd")
    with pytest.raises(ValueError, match="符号链接"):
        unpack_zip(stream)
    files = bundle()
    files["Metadata.json"] = files["metadata.json"]
    with pytest.raises(ValueError, match="重复"):
        inspect_bundle(files)


def test_import_api_does_not_call_model_or_require_overview(client):
    response = client.post(
        "/api/source-books",
        files=[
            ("files", (path, body, "application/octet-stream"))
            for path, body in bundle().items()
        ],
    )
    assert response.status_code == 201, response.text
    book = response.json()
    assert book["study"]["status"] == "pending"
    data = client.get("/api/source-books/" + book["id"]).json()
    assert data["synopsis"] == "Official reader promise." and data["result"] is None
    assert client.get("/api/source-books").json()["items"][0]["id"] == book["id"]


def test_explicit_new_model_study_archives_previous_results(tmp_path):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle())
    study = SourceStudy(library, book["id"])
    study.prepare({"model": "first"})
    old = study.run(lambda *_: response())
    study.prepare({"model": "second"}, restart=True)
    assert study.status()["completed"] == 0
    assert len(list(study.root.glob("history/*/result.json"))) == 1
    new = study.run(lambda *_: response())
    assert old["binding_sha256"] != new["binding_sha256"]
    assert old["source_sha256"] == new["source_sha256"]


@pytest.mark.parametrize("mode", ["source_development", "original_rewrite", "reference_creation"])
def test_source_analysis_freezes_into_discussion_and_source_cover_reaches_studio(
    client, monkeypatch, tmp_path, mode
):
    import time

    from test_poster_references import png
    from test_workshop_api import create

    from api import source_library
    from api.jobs import JobRunner
    from api.services import ProjectService
    from core.reference_assets import ReferenceAssets

    files = bundle()
    files["cover.jpg"] = png()
    imported = client.post(
        "/api/source-books",
        files=[("files", (p, b, "application/octet-stream")) for p, b in files.items()],
    ).json()
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        def complete(self, system, user):
            calls.append(json.loads(user))
            return response(planned=True)

    import core.llm_client
    from core import provider_settings

    monkeypatch.setattr(core.llm_client, "LLMClient", Model)
    # This integration fixture supplies a fake model; it must not depend on a
    # developer's saved route or inherit production connection settings.
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda _: {
        "provider": "codex", "model": "source-test", "base_url": "", "api_key": "",
        "reasoning_effort": "medium", "max_tokens": 8192, "timeout_seconds": 900,
    })
    monkeypatch.setattr(source_library, "runner", JobRunner())
    assert (
        client.post("/api/source-books/" + imported["id"] + "/analyze").status_code
        == 202
    )
    for _ in range(100):
        detail = client.get("/api/source-books/" + imported["id"]).json()
        if detail["study"]["status"] == "complete":
            break
        time.sleep(0.01)
    assert detail["study"]["status"] == "complete", detail
    assert len(calls) == 1
    selection = {
        "source_id": imported["id"],
        "analysis_sha256": detail["result"]["analysis_sha256"],
        "mode": mode,
        "use_cover": True,
    }
    data = create(client, source_book=selection)
    assert data["source_context"]["analysis"]["story_summary"]
    from api.workshop_delivery import author_context

    assert author_context(data)["source_reference"]["mode"] == mode
    from api.workshop_request import discussion_context
    discussion = discussion_context(author_context(data))
    assert discussion["source_reference"]["continuation_plan"] == plan()
    assert "not adopted canon" in discussion["source_reference_policy"]
    from api.workshop_design import execution_handoff
    from core.creative_execution import CreativeExecution
    from core.creation_mode import source_policy
    data["prompt_md"] = "# Source continuation\nThe last known choice changes trust."
    handoff = execution_handoff(data)
    assert handoff["source_context"] == data["source_context"]
    execution = tmp_path / "execution"
    (execution / "outputs/input").mkdir(parents=True)
    (execution / "outputs/input/prompt.md").write_text(data["prompt_md"])
    frozen = CreativeExecution.open(project=execution, run_id="test", handoff=handoff)
    for stage in ("story_design", "chapter_plan", "draft", "review"):
        context = frozen.design_context(stage)
        assert context["source_context"] == data["source_context"]
        assert context["source_policy"] == source_policy(data["source_context"])
    projects = ProjectService(tmp_path / "projects")
    target = tmp_path / "projects/new-story"
    source_library.carry_cover(projects, target, data["source_context"])
    refs = ReferenceAssets(target / "outputs/private/references")
    assert refs.selection()["mode"] == ("visual_inspiration" if mode == "reference_creation" else "adaptation_reference")
    assert refs.read(refs.selection()["asset_id"]) == files["cover.jpg"]
    assert client.post("/api/reader-reviews").status_code == 404


def test_genre_is_required_and_unrelated_or_unread_input_cannot_start(client):
    response = client.post(
        "/api/workshop/sessions", json={"title": "Missing selection"}
    )
    assert response.status_code == 422
    imported = client.post(
        "/api/source-books",
        files=[
            ("files", (p, b, "application/octet-stream")) for p, b in bundle().items()
        ],
    ).json()
    from test_creative_types import default_refs

    payload = {
        "audience_channel": "female",
        "creative_types": [r.model_dump() for r in default_refs()],
        "source_book": {
            "source_id": imported["id"],
            "analysis_sha256": "a" * 64,
            "mode": "reference_creation",
            "use_cover": True,
        },
    }
    response = client.post("/api/workshop/sessions", json=payload)
    assert response.status_code in (409, 422)
