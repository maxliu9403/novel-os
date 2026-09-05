from __future__ import annotations

import json

from core.cover_models import CoverBrief
from core.cover_normalizer import normalize_cover_brief, normalize_legacy_project


def _v1_brief() -> CoverBrief:
    return CoverBrief.from_dict({
        "schema_version": 1,
        "title": "Her Name on the Deed",
        "author": "",
        "language": "English",
        "genre": "family drama",
        "target_audience": "women rebuilding after betrayal",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver claims an independent home.",
        "core_conflict": "Two families demand her labor and income.",
        "emotional_promise": "anger followed by earned independence",
        "protagonist": {
            "role": "caregiver",
            "visual_identity": "composed woman in practical work clothes",
            "agency_signal": "holds her own house key",
        },
        "relationship_or_power_contrast": "one woman facing two demanding households",
        "decisive_story_node": "her name appears alone on the deed",
        "secondary_task": {
            "story_function": "protect child",
            "visual_signal": "small backpack",
        },
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top"},
        "forbidden_elements": ["real cities"],
    }, source_prompt_sha256="a" * 64)


def test_normalizer_marks_v1_missing_identity_as_pending_assumptions() -> None:
    result = normalize_cover_brief(_v1_brief(), source_prompt_sha256="b" * 64)

    assert result.schema_version == 2
    assert result.principal_characters[0].character_id == "protagonist"
    assert any(
        item.status == "pending_confirmation"
        and item.field.startswith("principal_characters[0]")
        for item in result.visual_assumptions
    )


def test_normalizer_does_not_add_market_identity_to_v1_character() -> None:
    result = normalize_cover_brief(_v1_brief(), source_prompt_sha256="b" * 64)

    character = result.principal_characters[0]
    assert "American" not in character.physical_identity
    assert "white" not in character.physical_identity.lower()


def test_normalizer_accepts_v2_mapping_without_rewriting_facts() -> None:
    from tests.test_cover_models_v2 import two_character_fixture

    payload = two_character_fixture()
    result = normalize_cover_brief(payload, source_prompt_sha256="c" * 64)

    assert result.schema_version == 2
    assert result.principal_characters[1].age_band == "late thirties"
    assert result.lived_environment.fictional_place == "invented North American city"


def _write_legacy_project(tmp_path, *, include_confirmed_facts: bool):
    project = tmp_path / "legacy-project"
    inputs = project / "outputs" / "input"
    state_dir = project / "outputs" / "state"
    inputs.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    prompt = "# Legacy story prompt\n"
    character: dict[str, object] = {
        "id": "claire_bennett",
        "name": "Claire Bennett",
        "role": "protagonist",
        "physical_description": "A practical woman in late pregnancy.",
        "external_goal": "Build a dependable home for her daughter.",
        "strength": "Documents the truth and sets boundaries.",
    }
    setting: dict[str, object] = {}
    state_character: dict[str, object] = {
        "id": "claire_bennett",
        "full_name": "Claire Bennett",
        "role": "protagonist",
    }
    if include_confirmed_facts:
        character["age"] = 30
        setting = {
            "time_period": "August 2027 through June 2028",
            "primary_location": (
                "Linden Falls, a fictional Midwestern city centered on Willow Creek townhouse community"
            ),
        }
        state_character.update({
            "age": 30,
            "arc_stage": "resolution",
            "current_location": "Two-bedroom apartment near Little Harbor",
            "emotional_state": "hurt but self-directed",
        })
        prompt += (
            "\n```yaml\n"
            "financial_baseline:\n"
            "  claire:\n"
            "    occupation: accounts-payable clerk\n"
            "```\n"
            "\n```yaml\n"
            "id: claire_bennett\n"
            "public_identity: Dependable accounts-payable clerk, wife, and first-time mother.\n"
            "```\n"
        )
    inputs.joinpath("prompt.md").write_text(prompt, encoding="utf-8")
    inputs.joinpath("brief.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "genre": "domestic drama",
        "audience": "women ages 30-50",
        "language": "American English",
    }), encoding="utf-8")
    inputs.joinpath("foundation.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "premise": "Claire replaces family appearance with reliable care and truthful finances.",
        "characters": [character],
        "plot_threads": [{
            "id": "main",
            "name": "independence",
            "description": "Claire leaves an unrepentant husband and builds a stable home.",
            "type": "main",
        }],
        "setting": setting,
        "ending_contract": {
            "emotional_contract": {"reader_emotion": "anger resolved through independence"},
        },
    }), encoding="utf-8")
    state_dir.joinpath("story_state.json").write_text(json.dumps({
        "metadata": {
            "genre": "domestic drama",
            "language": "American English",
            "audience": "women ages 30-50",
        },
        "characters": {"claire_bennett": state_character},
        "story_bible": {"setting": setting},
    }), encoding="utf-8")
    return project, prompt


def test_legacy_project_promotes_confirmed_canon_into_v2_story_facts(tmp_path) -> None:
    project, prompt = _write_legacy_project(tmp_path, include_confirmed_facts=True)

    result = normalize_legacy_project(project, prompt)

    claire = result.principal_characters[0]
    assert claire.character_id == "claire_bennett"
    assert claire.name == "Claire Bennett"
    assert claire.age == 30
    assert claire.occupation_and_status == "accounts-payable clerk"
    assert "Willow Creek townhouse community" in result.lived_environment.primary_spaces[0]
    assert result.lived_environment.primary_spaces[1] == (
        "Two-bedroom apartment near Little Harbor"
    )
    assert (
        "Resolution-state location: Two-bedroom apartment near Little Harbor. "
        "Use it only for ending-era scenes."
    ) in result.lived_environment.environment_truths
    assert result.pending_critical_assumptions() == ()
    assert {
        item.field: item.status for item in result.visual_assumptions if item.critical
    } == {
        "principal_characters[0].age": "approved",
        "principal_characters[0].occupation_and_status": "approved",
        "lived_environment.primary_spaces": "approved",
    }


def test_legacy_project_keeps_missing_cover_facts_pending(tmp_path) -> None:
    project, prompt = _write_legacy_project(tmp_path, include_confirmed_facts=False)

    result = normalize_legacy_project(project, prompt)

    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].age",
        "principal_characters[0].occupation_and_status",
        "lived_environment.primary_spaces",
    }
