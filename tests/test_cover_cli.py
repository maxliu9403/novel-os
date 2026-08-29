from __future__ import annotations

import json
from pathlib import Path

from core.cover_models import CoverBrief, CoverConcept, CoverSet
from core.cover_store import CoverStore
from core.orchestrator import main


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
