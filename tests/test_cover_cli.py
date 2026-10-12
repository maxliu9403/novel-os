from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.cover_director import CoverArtDirector
from core.cover_handoff import parse_cover_handoff_v2
from core.cover_models import CoverBrief, CoverConcept, CoverSet
from core.cover_store import CoverStore
from core.orchestrator import main
from tests.test_cover_director import director_fixture
from tests.test_cover_models_v2 import two_character_fixture


SHA = "a" * 64


def _set(project: Path) -> CoverSet:
    brief = CoverBrief.from_dict({
        "schema_version": 1, "title": "CLI Cover", "language": "English",
        "genre": "revenge", "target_audience": "women 25-44",
        "market_scope": "English serialized fiction", "core_task": "Reclaim a company.",
        "core_conflict": "A former partner controls the board.",
        "emotional_promise": "earned reversal",
        "protagonist": {"role": "founder", "visual_identity": "executive", "agency_signal": "reveals contract"},
        "relationship_or_power_contrast": "founder against board",
        "decisive_story_node": "the original vote is revealed",
        "secondary_task": {"story_function": "prove fraud", "visual_signal": "contract"},
        "world_signals": ["fictional finance capital"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": ["real cities"],
    }, source_prompt_sha256=SHA)
    concepts = [CoverConcept(
        concept_id=f"concept-{i}", visual_strategy=f"strategy-{i}", focal_scene=f"scene-{i}",
        composition="portrait", palette="red", secondary_signal="contract",
        title_treatment="large", generation_prompt=f'Render exact title "CLI Cover" once. {i}',
    ) for i in range(1, 5)]
    return CoverStore(project).create(CoverSet.new(project.name, brief, concepts))


def test_cover_list_outputs_safe_json(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project-one"
    cover_set = _set(project)

    code = main(["cover", "list", "--project", str(project)])

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body[0]["cover_set_id"] == cover_set.cover_set_id
    assert "api_key" not in repr(body)


def test_cover_generate_rejects_invalid_count_before_provider_call(tmp_path: Path, capsys) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("no provider call", encoding="utf-8")

    code = main([
        "cover", "generate", "--project", str(tmp_path / "project"),
        "--prompt", str(prompt), "--count", "2",
    ])

    assert code == 2
    assert "between 3 and 5" in capsys.readouterr().err


def test_cover_generate_marks_direction_stale_from_current_prompt_before_provider_call(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    project = tmp_path / "project"
    original_payload = two_character_fixture()
    original_text = (
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(original_payload, ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n"
    )
    brief = parse_cover_handoff_v2(original_text)
    store = CoverStore(project)
    direction = store.create_direction(
        CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4),
        brief=brief.to_dict(),
    )
    direction = store.approve_direction(
        direction.direction_id,
        expected_brief_sha256=direction.brief_sha256,
        approved_direction_sha256=direction.direction_sha256,
    )
    changed_payload = two_character_fixture()
    changed_payload["core_conflict"] = "The current story now turns on another rupture."
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(changed_payload, ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOVEL_OS_COVER_API_KEY", "unused-cover-key")

    code = main([
        "cover", "generate", "--project", str(project), "--prompt", str(prompt),
        "--direction-id", direction.direction_id,
        "--approved-direction-sha256", direction.direction_sha256,
    ])

    assert code == 2
    assert "stale" in capsys.readouterr().err
    assert store.load_direction(direction.direction_id).status == "stale"


def test_cover_select_requires_revision_arguments(tmp_path: Path) -> None:
    project = tmp_path / "project"
    cover_set = _set(project)

    try:
        main([
            "cover", "select", "--project", str(project),
            "--cover-set", cover_set.cover_set_id,
            "--candidate", cover_set.candidates[0].candidate_id,
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("argparse should require both revisions")


def _review_cli_args(project, review_path):
    return ["cover", "--workspace-id", "editorial", "review-analysis",
        "--project", str(project), "--analysis-id", "analysis-" + "1" * 32,
        "--expected-analysis-sha256", SHA, "--review-json", str(review_path)]


def _review_cli_stubs(monkeypatch, review_result):
    from core import cover_cli, cover_analysis
    from api import routes

    context, analyzer = object(), object()
    calls = {}
    director = SimpleNamespace(_complete=object(), model="configured-review-model",
        analysis_configuration={"max_segment_bytes": 16000, "segmenter_version": "cover-segments.v2"})

    def workflow(project, workspace_id):
        calls["workflow"] = (project, workspace_id)
        return SimpleNamespace(context=context)

    def make_analyzer(**kwargs):
        calls["configuration"] = kwargs
        return analyzer

    def review(context_arg, **kwargs):
        calls["review"] = (context_arg, kwargs)
        if isinstance(review_result, Exception):
            raise review_result
        return SimpleNamespace(to_dict=lambda: review_result)

    monkeypatch.setattr(cover_cli, "_workflow", workflow)
    monkeypatch.setattr(routes, "get_cover_art_director", lambda: director)
    monkeypatch.setattr(cover_analysis, "CoverStoryAnalyzer", make_analyzer)
    monkeypatch.setitem(sys.modules, "core.cover_analysis_reviews", SimpleNamespace(start_review_revision=review))
    return calls, context, analyzer, director


def test_review_analysis_uses_current_context_and_configured_analyzer(tmp_path, monkeypatch, capsys):
    project, path = tmp_path / "novel", tmp_path / "independent-review.json"
    review = {"review_kind": "agent", "reviewer_id": "独立审查", "issues": [{"quote": "Julianne is his mistress."}]}
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    expected = {"analysis_id": "analysis-" + "2" * 32, "status": "complete"}
    calls, context, analyzer, director = _review_cli_stubs(monkeypatch, expected)

    assert main(_review_cli_args(project, path)) == 0

    assert json.loads(capsys.readouterr().out) == expected
    assert calls["workflow"] == (project.resolve(), "editorial")
    assert calls["configuration"] == {"complete": director._complete,
        "model": director.model, **director.analysis_configuration}
    assert calls["review"] == (context, {"parent_analysis_id": "analysis-" + "1" * 32,
        "expected_analysis_sha256": SHA, "external_review": review, "analyzer": analyzer})


@pytest.mark.parametrize("contents", ["{invalid", "[]", "null"])
def test_review_analysis_rejects_invalid_review_before_constructing_analyzer(tmp_path, monkeypatch, capsys, contents):
    path = tmp_path / "invalid.json"
    path.write_text(contents, encoding="utf-8")
    calls, *_ = _review_cli_stubs(monkeypatch, {})

    assert main(_review_cli_args(tmp_path / "project", path)) == 2

    assert "Cover command failed:" in capsys.readouterr().err
    assert "configuration" not in calls
    assert "review" not in calls


def test_review_analysis_reports_missing_review_file(tmp_path, monkeypatch, capsys):
    calls, *_ = _review_cli_stubs(monkeypatch, {})

    assert main(_review_cli_args(tmp_path / "project", tmp_path / "missing.json")) == 2

    assert "missing.json" in capsys.readouterr().err
    assert "configuration" not in calls
    assert "review" not in calls


def test_review_analysis_reports_helper_validation_failure(tmp_path, monkeypatch, capsys):
    from core.cover_analysis import CoverAnalysisError

    path = tmp_path / "review.json"
    path.write_text("{}", encoding="utf-8")
    _review_cli_stubs(monkeypatch, CoverAnalysisError("analysis_changed", "Parent analysis hash mismatch"))

    assert main(_review_cli_args(tmp_path / "project", path)) == 2

    output = capsys.readouterr()
    assert not output.out
    assert "Parent analysis hash mismatch" in output.err


@pytest.mark.parametrize("missing", ["--project", "--analysis-id", "--expected-analysis-sha256", "--review-json"])
def test_review_analysis_requires_explicit_parent_hash_and_review_file(tmp_path, missing):
    args = _review_cli_args(tmp_path, tmp_path / "review.json")
    offset = args.index(missing)
    del args[offset:offset + 2]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
