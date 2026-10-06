from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector
from core.cover_models_v2 import ArtDirectionSet
from core.cover_prompt_compiler import compile_cover_prompt, compile_repair_prompt
from core.cover_validator import validate_direction
from core.cover_render_policy import photographic_medium_issue
from tests.test_cover_director import _brief, adaptive_director_fixture


@pytest.mark.parametrize("medium", [
    "Textured linocut-and-gouache editorial illustration with realistic mature faces",
    "Painterly contemporary realism with egg-tempera figures",
    "Tactile letterpress, shallow paper relief, and naturalistically painted human figures",
    "Photorealistic CGI with 3D rendered figures",
])
def test_saved_nonphotographic_direction_requires_replanning_before_render(medium):
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    scene = replace(direction.plans[0], art_style=medium)
    with pytest.raises(ValueError, match="photographic replanning"):
        compile_cover_prompt(brief, scene, visual_identity=direction.visual_identity,
                             evidence_ledger=direction.evidence_ledger,
                             conflict_contract=direction.core_conflict_visual_contract)


def test_default_director_contract_keeps_diversity_within_photography():
    director = CoverArtDirector(complete=lambda s, u: "{}")
    assert director.profile_version == "cover-profiles.v9"
    request = json.loads(director._user_prompt(_brief(), 4))
    assert request["render_policy"]["medium"] == "live_action_photography"
    assert "photographic" in request["response_contract"]["plans"]["field_rules"]["art_style"]
    assert "oil painting" in director._system_prompt()
    assert "film" in director._system_prompt()


def test_v6_rejects_painted_medium_while_preserving_legacy_direction_readability():
    data = adaptive_director_fixture()
    data["plans"][1]["art_style"] = "Naturalistically painted people in a gouache illustration"
    historical = ArtDirectionSet.from_dict(data, brief_sha256="a" * 64)
    assert historical.plans[1].art_style == data["plans"][1]["art_style"]
    current = replace(historical, profile_version="cover-profiles.v6")
    findings = validate_direction(_brief(), current)
    assert any(f.code == "non_photographic_medium" and "concept-2" in f.evidence for f in findings)


def test_photography_contract_is_protected_from_prompt_compaction():
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    scene = replace(direction.plans[0], design_rationale="Book-specific compositional reasoning. " * 500)
    compiled = compile_cover_prompt(brief, scene, visual_identity=direction.visual_identity,
                                    evidence_ledger=direction.evidence_ledger,
                                    conflict_contract=direction.core_conflict_visual_contract)
    assert "PHOTOGRAPHIC RENDER CONTRACT" in compiled.modules
    assert "No oil painting" in compiled.modules["PHOTOGRAPHIC RENDER CONTRACT"]
    assert "professional film campaign photography" in compiled.text
    assert "For illustration," not in compiled.text
    assert len(compiled.text) <= 12000


@pytest.mark.parametrize("medium", [
    "Natural-light editorial photography",
    "Live-action film publicity photograph with mature skin and subtle grain",
    "Photographic realism, no oil painting, watercolor or CGI",
    "Photography without illustration; natural lens optics",
])
def test_photo_medium_allows_explicit_negative_style_rules(medium):
    assert photographic_medium_issue(medium) == ""


def test_director_repairs_medium_before_persisting_plan_without_changing_cast():
    bad = adaptive_director_fixture()
    bad["plans"][1]["art_style"] = "Gouache illustration"
    good = adaptive_director_fixture()
    responses = iter((json.dumps(bad), json.dumps(good)))
    calls = []

    def complete(system, user):
        calls.append((system, user))
        return next(responses)

    result = CoverArtDirector(complete=complete, model="test-director", profile_version="cover-profiles.v8").plan(_brief(), count=4)
    assert result.profile_version == "cover-profiles.v8"
    assert len(calls) == 2
    assert "non_photographic_medium" in calls[1][1]
    assert "not just art_style" in calls[1][1]
    assert result.plans[1].cast == tuple(good["plans"][1]["cast"])
    assert all(not photographic_medium_issue(plan.art_style) for plan in result.plans)


def test_art_materials_in_story_props_are_not_confused_with_rendering_medium():
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    scene = replace(direction.plans[0], primary_prop="an oil painting restored by the protagonist")
    compiled = compile_cover_prompt(brief, scene)
    assert scene.primary_prop in compiled.text


def test_painterly_quality_failure_repair_targets_the_medium_contract():
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    prior = compile_cover_prompt(brief, direction.plans[0])
    repaired = compile_repair_prompt(brief, direction.plans[0], prior, ["genre_drift"])
    assert "Repair focus: genre drift." in repaired.modules["PHOTOGRAPHIC RENDER CONTRACT"]
    assert "Repair focus: genre drift." in repaired.modules["MEDIUM FIDELITY REQUIREMENTS"]
