"""A complete source and many evidence records must survive later design stages."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_director_source_context import bound  # noqa: F401


class ModelBoundaryReached(BaseException):
    """Stop an offline replay before supplying a second model response."""


@pytest.fixture
def dense_source(bound):
    context, snapshot, analysis, brief, ledger, payload, _ = bound
    data = analysis.to_dict()
    template = data["evidence"][0]
    additions = [{**deepcopy(template), "evidence_id": f"evidence-regression-{index:04d}"}
                 for index in range(200)]
    data["evidence"].extend(additions)
    ledger = replace(ledger, items=(*ledger.items, *[
        replace(ledger.items[0], evidence_id=item["evidence_id"]) for item in additions]))
    data["story"]["last_chapter_reversal"] = "The final source fact remains available to every direction."
    for plan in payload["plans"]:
        plan["story_evidence_refs"] = list(ledger.allowed_refs)
    path = context.cover_root / "analyses" / analysis.analysis_id / "analysis.json"
    return snapshot, data, brief, ledger, payload, path


def assert_complete_context(request, snapshot, data):
    assert request["frozen_source_context"]["documents"][0]["text"].encode() == snapshot.primary_bytes
    assert request["story_analysis"]["story"] == data["story"]
    for key in ("characters", "relationships", "events", "suspense", "visual_assets", "critical_claims"):
        assert request["story_analysis"][key] == data[key]
    if "cover_evidence_registry" in request:
        from core.cover_campaign_transport import decode_evidence_registry
        records = decode_evidence_registry(request["cover_evidence_registry"])
        assert records == [{**item, "evidence_ref": "evidence:" + item["evidence_id"]}
                           for item in data["evidence"]]
    else:
        assert [row["evidence_id"] for row in request["story_analysis"]["evidence"]] == [
            row["evidence_id"] for row in data["evidence"]]


@pytest.mark.parametrize("repair", ["schema", "semantic"])
def test_complete_source_repair_fits_without_dropping_response_or_evidence(dense_source, repair):
    snapshot, data, brief, ledger, payload, path = dense_source
    before = deepcopy(data)
    file_before = path.read_bytes()
    original_prompt = CoverArtDirector._user_prompt(brief, 4, profile_version="cover-profiles.v10",
        evidence_ledger=ledger, story_analysis=data,
        source_context=CoverArtDirector._frozen_source_context(snapshot, data))
    # Reproduce the same limited headroom as the user's 340 KB initial request.
    initial_bytes = len((CoverArtDirector._system_prompt("cover-profiles.v10") + "\n" + original_prompt).encode())
    budget = min(384000, initial_bytes + 44000)
    assert initial_bytes < budget
    raw_response = "x" * 70000
    if repair == "semantic":
        payload["plans"][0]["campaign"]["cover_epoch"] = "wrong time requiring repair"
        payload["director_notes"] = "Complete design rationale: " + raw_response
        raw_response = json.dumps(payload, ensure_ascii=False)
    calls = []
    def complete(system, user):
        request = json.loads(user)
        assert len((system + "\n" + user).encode()) <= budget
        assert_complete_context(request, snapshot, data)
        calls.append(request)
        if len(calls) == 1:
            return raw_response
        if repair == "schema":
            assert request["previous_response"] == raw_response
        else:
            assert request["previous_response"] == json.loads(raw_response)
            assert request["validation_findings"]
        raise ModelBoundaryReached()
    director = CoverArtDirector(complete=complete)
    director.analysis_configuration = {"max_request_bytes": budget}
    with pytest.raises(ModelBoundaryReached):
        director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=data, source_snapshot=snapshot)
    assert len(calls) == 2
    assert data == before and path.read_bytes() == file_before


def test_full_portfolio_review_keeps_all_selected_evidence_under_budget(dense_source):
    snapshot, data, brief, ledger, payload, path = dense_source
    before, file_before = deepcopy(data), path.read_bytes()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger,
                                                           story_analysis=data, source_snapshot=snapshot)
    calls = []
    def complete(system, user):
        request = json.loads(user)
        assert len((system + "\n" + user).encode()) <= 230000
        assert_complete_context(request, snapshot, data)
        assert request["plans"] == [plan.to_dict() for plan in direction.plans]
        calls.append(request)
        return json.dumps(supported_review(request))
    director = CoverArtDirector(complete=complete)
    director.analysis_configuration = {"max_request_bytes": 230000}
    assert director._review_campaign_semantics(direction, data, brief=brief,
        source_context=director._frozen_source_context(snapshot, data)) == ()
    assert len(calls) == 1
    assert data == before and path.read_bytes() == file_before


def test_oversized_original_story_still_blocks_without_a_model_call(dense_source):
    snapshot, data, brief, ledger, _, _ = dense_source
    data["story"]["unabridged_fact"] = "真実" * 100000
    calls = []
    with pytest.raises(CoverDirectionError, match="exceeds its byte budget"):
        CoverArtDirector(complete=lambda *args: calls.append(args)).plan(brief, count=4,
            evidence_ledger=ledger, story_analysis=data, source_snapshot=snapshot)
    assert calls == []
