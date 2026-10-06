from __future__ import annotations

import json

import pytest

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


def test_legacy_project_recovers_identity_from_json_character_ledger(tmp_path) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    occupation = "Music teacher pursuing a community choir teaching role."
    prompt = "# Canonical character ledger\n```json\n" + json.dumps({
        "id": "claire_bennett", "name": "Claire Bennett",
        "public_identity": occupation, "age_at_opening": 30,
    }) + "\n```\n"

    result = normalize_legacy_project(project, prompt)

    assert result.principal_characters[0].occupation_and_status == occupation
    assert result.pending_critical_assumptions() == ()


def test_legacy_project_does_not_use_another_json_characters_identity(tmp_path) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt = "```json\n" + json.dumps({
        "id": "someone_else", "public_identity": "Retired photographer.",
    }) + "\n```\n```json\n{invalid json}\n```\n"

    result = normalize_legacy_project(project, prompt)

    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].occupation_and_status",
    }


def test_legacy_project_recovers_character_array_identity_without_borrowing_reader_age(
    tmp_path,
) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=False)
    occupation = "Returning interior designer rebuilding her independent practice."
    prompt = "# Character ledger\n```json\n" + json.dumps([
        {
            "id": "audience",
            "age_band": "women ages 30-50",
            "public_identity": "English-language domestic-drama readers",
        },
        {
            "id": "someone_else",
            "age": 44,
            "public_identity": "Veteran architect",
        },
        {
            "id": "claire_bennett",
            "name": "Claire Bennett",
            "public_identity": occupation,
        },
    ]) + "\n```\n"

    result = normalize_legacy_project(project, prompt)

    claire = result.principal_characters[0]
    assert claire.occupation_and_status == occupation
    assert claire.age is None
    assert claire.age_band == ""
    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].age",
        "lived_environment.primary_spaces",
    }


def test_legacy_project_accepts_explicit_character_age_band_from_array(tmp_path) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=False)
    prompt = "```json\n" + json.dumps([
        {"id": "audience", "age_band": "women ages 30-50"},
        {
            "id": "claire_bennett",
            "age_band": "early thirties",
            "occupation_and_status": "Independent interior designer",
        },
    ]) + "\n```\n"

    result = normalize_legacy_project(project, prompt)

    assert result.principal_characters[0].age is None
    assert result.principal_characters[0].age_band == "early thirties"
    assert {item.field for item in result.pending_critical_assumptions()} == {
        "lived_environment.primary_spaces",
    }


@pytest.mark.parametrize("identity", [
    "Claire was an excellent university graduate and teacher who stepped behind her husband's career.",
    "Claire Bennett is a retired nurse who now cares for her daughter.",
    "She works as a botanical illustrator for a local publisher.",
])
def test_legacy_project_recovers_explicit_identity_from_character_biography(
    tmp_path, identity,
) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt = (
        "# Story\n\n## Character Core\n\n"
        "### Claire Bennett, 30 — the family foundation\n\n"
        f"{identity} She wants to recover her independence.\n\n"
        "### Richard Bennett, 55 — the husband\n\nRichard is a sales executive.\n"
    )

    result = normalize_legacy_project(project, prompt)

    assert result.principal_characters[0].occupation_and_status == identity
    assert "outputs/input/prompt.md:7" in result.principal_characters[0].source_refs
    assert result.pending_critical_assumptions() == ()


@pytest.mark.parametrize("biography", [
    "Claire wants to become a teacher.",
    "Claire was never a teacher.",
    "Claire was a woman married to a teacher.",
    "Claire was a teacher's daughter.",
    "Richard is a teacher. Claire wants independence.",
    "Claire was a woman who hoped to become a teacher.",
    "Claire was a pretend teacher in a school play.",
    "Claire was a teacher in the school play.",
    "Claire was a teacher in name only.",
])
def test_legacy_project_does_not_confirm_ambiguous_biography_identity(
    tmp_path, biography,
) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt = (
        "## Character Core\n\n### Claire Bennett\n\n"
        f"{biography}\n\n### Richard Bennett\n\nRichard is a teacher.\n"
    )

    result = normalize_legacy_project(project, prompt)

    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].occupation_and_status",
    }


@pytest.mark.parametrize("prompt", [
    "### Claire Bennett and Richard Bennett\n\nShe is a nurse.\n",
    "### Claire Bennett Jr.\n\nShe is a nurse.\n",
    "### Someone Else\n\nShe is a nurse.\n",
    "```markdown\n### Claire Bennett\n\nShe is a nurse.\n```\n",
    "### Claire Bennett\n\nShe is a nurse.\n\n### Claire Bennett\n\nShe is a lawyer.\n",
])
def test_legacy_project_requires_unambiguous_character_biography_heading(tmp_path, prompt) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt = "## Character Core\n\n" + prompt

    result = normalize_legacy_project(project, prompt)

    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].occupation_and_status",
    }


def test_legacy_project_prefers_structured_identity_over_prose(tmp_path) -> None:
    project, prompt = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt += "\n## Character Core\n\n### Claire Bennett\n\nClaire was a teacher.\n"

    result = normalize_legacy_project(project, prompt)

    assert result.principal_characters[0].occupation_and_status == "accounts-payable clerk"


@pytest.mark.parametrize("section", ["Chapter 7", "Future Possibilities"])
def test_legacy_project_does_not_take_scene_or_alternative_identity_as_biography(tmp_path, section) -> None:
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    prompt = f"## {section}\n\n### Claire Bennett\n\nClaire was a teacher.\n"

    result = normalize_legacy_project(project, prompt)

    assert {item.field for item in result.pending_critical_assumptions()} == {
        "principal_characters[0].occupation_and_status",
    }


def _write_six_character_legacy_project(tmp_path):
    project, _ = _write_legacy_project(tmp_path, include_confirmed_facts=True)
    foundation_path = project / "outputs" / "input" / "foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    foundation["characters"] = [
        foundation["characters"][0],
        {
            "id": "eleanor_shaw",
            "name": "Eleanor Shaw",
            "role": "co-protagonist",
            "age": 67,
            "physical_description": "Silver-haired, upright, with a weathered canvas satchel.",
            "external_goal": "Recover the letters before the estate is sold.",
            "arc": "Moves from guarded witness to public truth-teller.",
        },
        {
            "id": "richard_bennett",
            "name": "Richard Bennett",
            "role": "antagonist",
            "age": 55,
            "physical_description": "Immaculate navy suits and a rigid public smile.",
            "external_goal": "Keep control of the estate records.",
            "arc": "His control collapses when the complete record becomes public.",
        },
        {
            "id": "maya_bennett",
            "name": "Maya Bennett",
            "role": "adult daughter",
            "age": 24,
            "physical_description": "Close-cropped curls and a paint-stained denim jacket.",
            "external_goal": "Choose work without becoming the family mediator.",
        },
        {
            "id": "omar_reed",
            "name": "Omar Reed",
            "role": "archivist",
            "age_band": "early forties",
            "physical_description": "Round glasses and ink-marked cuffs.",
            "occupation": "Municipal archivist",
            "external_goal": "Keep the record chain intact.",
        },
        {
            "id": "june_ellis",
            "name": "June Ellis",
            "role": "supporting friend",
            "physical_description": "Broad-shouldered with a bright green scarf.",
        },
    ]
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")

    state_path = project / "outputs" / "state" / "story_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["characters"].update({
        item["id"]: {
            "id": item["id"],
            "full_name": item["name"],
            "role": item["role"],
            "age": item.get("age"),
            "current_location": f"Ending location for {item['name']}",
            "emotional_state": f"Ending emotion for {item['name']}",
            "arc_stage": "resolution",
        }
        for item in foundation["characters"][1:]
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return project


def test_legacy_project_preserves_the_complete_durable_cast_without_cross_borrowing(
    tmp_path,
) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    prompt = "# Character ledger\n```json\n" + json.dumps([
        {
            "id": "audience",
            "age_band": "women ages 30-50",
            "public_identity": "Domestic-drama readers",
        },
        {
            "id": "eleanor_shaw",
            "occupation_and_status": "Retired court reporter preserving a private archive.",
            "current_location": "Ending location from the structured manuscript state.",
        },
        {
            "id": "someone_else",
            "age": 39,
            "public_identity": "Emergency physician",
        },
    ]) + "\n```\n"

    result = normalize_legacy_project(project, prompt)
    cast = {character.character_id: character for character in result.principal_characters}

    assert list(cast) == [
        "claire_bennett", "eleanor_shaw", "richard_bennett",
        "maya_bennett", "omar_reed", "june_ellis",
    ]
    assert cast["claire_bennett"].must_appear is True
    assert all(not cast[character_id].must_appear for character_id in list(cast)[1:])
    assert cast["eleanor_shaw"].age == 67
    assert cast["eleanor_shaw"].physical_identity == (
        "Silver-haired, upright, with a weathered canvas satchel."
    )
    assert cast["eleanor_shaw"].occupation_and_status == (
        "Retired court reporter preserving a private archive."
    )
    assert cast["eleanor_shaw"].lived_environment == ""
    assert cast["omar_reed"].age is None
    assert cast["omar_reed"].age_band == "early forties"
    assert cast["omar_reed"].occupation_and_status == "Municipal archivist"
    assert cast["june_ellis"].age is None
    assert cast["june_ellis"].occupation_and_status == ""
    assert cast["june_ellis"].agency_signal == "supporting friend"
    assert cast["june_ellis"].lived_environment == ""
    assert cast["june_ellis"].current_emotional_state == ""
    assert "outputs/input/foundation.json:characters.june_ellis" in cast["june_ellis"].source_refs
    assert "outputs/state/story_state.json:characters.june_ellis" in cast["june_ellis"].source_refs
    assert "outputs/input/prompt.md:structured_character_facts" in cast["eleanor_shaw"].source_refs
    assert not any("principal_characters[1]" in item.field for item in result.visual_assumptions)


def test_legacy_project_restores_explicit_structured_relationship_map(tmp_path) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    relationship = {
        "from_character_id": "claire_bennett",
        "to_character_id": "eleanor_shaw",
        "relationship": "Co-investigators with different stakes in the archive",
        "power_balance": "Claire controls the deed; Eleanor controls her private letters.",
        "visible_tension": "Each needs evidence the other is reluctant to release.",
        "shared_risk": "The estate sale could erase the complete record.",
    }
    prompt = "```json\n" + json.dumps({"relationship_map": [relationship]}) + "\n```\n"

    result = normalize_legacy_project(project, prompt)

    assert [item.to_dict() for item in result.relationship_map] == [relationship]


def test_legacy_project_adapts_durable_foundation_relationships_without_inventing_labels(
    tmp_path,
) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    foundation_path = project / "outputs" / "input" / "foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    foundation["relationships"] = [{
        "id": "rel_claire_richard",
        "characters": ["claire_bennett", "richard_bennett"],
        "opening_state": "They contest who controls the estate record.",
        "leverage": "Richard controls access; Claire holds the signed deed.",
        "final_state": "The complete record becomes public.",
    }]
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")

    result = normalize_legacy_project(project, "# Approved story\n")

    assert [item.to_dict() for item in result.relationship_map] == [{
        "from_character_id": "claire_bennett",
        "to_character_id": "richard_bennett",
        "relationship": "They contest who controls the estate record.",
        "power_balance": "Richard controls access; Claire holds the signed deed.",
        "visible_tension": "They contest who controls the estate record.",
        "shared_risk": "The complete record becomes public.",
    }]


def test_legacy_project_uses_only_narrative_roles_for_relationship_fallback(tmp_path) -> None:
    project = _write_six_character_legacy_project(tmp_path)

    result = normalize_legacy_project(project, "# Approved story\n")

    links = [item.to_dict() for item in result.relationship_map]
    assert links == [
        {
            "from_character_id": "claire_bennett",
            "to_character_id": "eleanor_shaw",
            "relationship": "co-protagonist",
            "power_balance": "Recover the letters before the estate is sold.",
            "visible_tension": "Moves from guarded witness to public truth-teller.",
            "shared_risk": "",
        },
        {
            "from_character_id": "claire_bennett",
            "to_character_id": "richard_bennett",
            "relationship": "antagonist",
            "power_balance": "Keep control of the estate records.",
            "visible_tension": (
                "His control collapses when the complete record becomes public."
            ),
            "shared_risk": "",
        },
    ]
    assert not any(
        family_label in item["relationship"].casefold()
        for item in links
        for family_label in ("spouse", "wife", "husband", "mother", "daughter", "parent")
    )


def test_legacy_project_recovers_optional_identity_from_nested_character_ledger(
    tmp_path,
) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    identity = "Retired court reporter preserving a private archive."
    prompt = "```yaml\ncharacters:\n  - character_id: eleanor_shaw\n" + (
        f"    occupation_and_status: {identity}\n"
    ) + "```\n"

    result = normalize_legacy_project(project, prompt)
    cast = {character.character_id: character for character in result.principal_characters}

    assert cast["eleanor_shaw"].occupation_and_status == identity
    assert cast["richard_bennett"].occupation_and_status == ""


def test_legacy_project_matches_structured_optional_identity_by_exact_name(tmp_path) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    identity = "Retired court reporter preserving a private archive."
    prompt = "```json\n" + json.dumps({
        "name": "Eleanor Shaw",
        "occupation_and_status": identity,
    }) + "\n```\n"

    result = normalize_legacy_project(project, prompt)
    cast = {character.character_id: character for character in result.principal_characters}

    assert cast["eleanor_shaw"].occupation_and_status == identity
    assert cast["richard_bennett"].occupation_and_status == ""


def test_legacy_project_recovers_optional_identity_from_named_biography(tmp_path) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    prompt = (
        "## Character Core\n\n### Eleanor Shaw\n\n"
        "Eleanor was a retired nurse who preserved the archive.\n"
    )

    result = normalize_legacy_project(project, prompt)
    cast = {character.character_id: character for character in result.principal_characters}

    assert cast["eleanor_shaw"].occupation_and_status == (
        "Eleanor was a retired nurse who preserved the archive."
    )
    assert "outputs/input/prompt.md:5" in cast["eleanor_shaw"].source_refs


def test_legacy_project_appends_a_durable_state_only_character(tmp_path) -> None:
    project = _write_six_character_legacy_project(tmp_path)
    state_path = project / "outputs" / "state" / "story_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["characters"]["state_witness"] = {
        "full_name": "Tessa Ward",
        "role": "supporting witness",
        "age": 45,
        "physical_description": "A precise woman with a battered document case.",
        "external_goal": "Authenticate the original record.",
        "current_location": "Ending courthouse steps",
        "emotional_state": "Relieved after the verdict",
        "arc_stage": "resolution",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = normalize_legacy_project(project, "# Approved story\n")
    cast = {character.character_id: character for character in result.principal_characters}

    assert len(cast) == 7
    assert cast["state_witness"].name == "Tessa Ward"
    assert cast["state_witness"].must_appear is False
    assert cast["state_witness"].physical_identity == (
        "A precise woman with a battered document case."
    )
    assert cast["state_witness"].agency_signal == "Authenticate the original record."
    assert cast["state_witness"].lived_environment == ""
    assert cast["state_witness"].current_emotional_state == ""
