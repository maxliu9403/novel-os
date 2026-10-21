"""Complete frozen narrative accompanies compact analysis without rewriting it."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import capture_source, CoverSourceError
from core.cover_store import CoverStore
from core.cover_workflow import CoverWorkflow
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import source
from tests.test_cover_workflow import FullStoryFixture

TAIL = "Oren calls from the doorway that the property tax notice has arrived."


@pytest.fixture
def bound(source):
    context, old_snapshot, project = source
    path = project / "outputs/input/prompt.md"
    original = path.read_bytes()
    path.write_bytes(original.replace("她关上了门。".encode(), ("她关上了门。 " + TAIL).encode()))
    snapshot = capture_source(context, old_snapshot.selector)
    fixture = FullStoryFixture()
    def extract_short_quote(request, result):
        result = fixture._with_events(request, result)
        if request["phase"] == "segment" and "Mara removes Oren's key." in request["segment"]["text"]:
            result["claims"][0]["quote"] = "Mara removes Oren's key."
        return result
    fixture.extractor.mutate = extract_short_quote
    analysis = CoverStoryAnalyzer(complete=fixture._complete, model=fixture.model,
                                  max_repair_attempts=0).analyze(context, snapshot)
    brief, ledger = derive_analysis_context(context, analysis)
    data = analysis.to_dict()
    assert all(TAIL not in item["exact_quote"] for item in data["evidence"])
    payload = fixture.plan(brief, count=4, evidence_ledger=ledger, story_analysis=data).to_dict()
    return context, snapshot, analysis, brief, ledger, payload, project


@pytest.mark.parametrize("repair", ["schema", "semantic"])
def test_complete_paragraph_reaches_design_all_repairs_and_independent_review(bound, repair):
    context, snapshot, analysis, brief, ledger, payload, _ = bound
    data = analysis.to_dict()
    frozen = deepcopy(data)
    analysis_path = context.cover_root / "analyses" / analysis.analysis_id / "analysis.json"
    original_bytes = analysis_path.read_bytes()
    calls, design_calls, review_calls = [], [], []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        assert "data" in system
        if request["frozen_source_context"] is not None:
            assert "instructions" in (system + str(request["frozen_source_context"]))
        if request["task"] == "review_campaign_semantics":
            review_calls.append(request)
            if repair == "semantic" and len(review_calls) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{"concept_id": "concept-1",
                    "message": "Make the evidenced doorway response legible.",
                    "evidence_refs": [ledger.allowed_refs[0]]}]))
            return json.dumps(supported_review(request))
        design_calls.append(request)
        return '{}' if repair == 'schema' and len(design_calls) == 1 else json.dumps(payload)
    # v9->v10 delegation must forward the request-local snapshot too.
    director = CoverArtDirector(complete=complete)
    result = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=data,
                           source_snapshot=snapshot)
    assert result.profile_version == "cover-profiles.v10"
    assert len(design_calls) == 2
    assert len(review_calls) == (2 if repair == "semantic" else 1)
    contexts = []
    for call in calls:
        context = deepcopy(call["frozen_source_context"])
        view = context["evidence_spans"]
        if isinstance(view, dict):
            from core.cover_campaign_transport import decode_evidence_registry
            assert view["registry"] == "/cover_evidence_registry" and view["rows"] == "all"
            records = decode_evidence_registry(call["cover_evidence_registry"])
            context["evidence_spans"] = [{key: item[key] for key in view["fields"]} for item in records]
        contexts.append(context)
    assert all(item == contexts[0] for item in contexts)
    full = contexts[0]
    assert full["scope"] == "complete_documents"
    assert full["snapshot_sha256"] == snapshot.snapshot_sha256
    document = full["documents"][0]
    assert document["text"].encode() == snapshot.primary_bytes
    assert TAIL in document["text"]
    assert document["byte_end"] == len(snapshot.primary_bytes)
    for span in full["evidence_spans"]:
        evidence = next(item for item in data["evidence"] if 'evidence:' + item['evidence_id'] == span['evidence_ref'])
        assert snapshot.primary_bytes[span['byte_start']:span['byte_end']].decode() == evidence['exact_quote']
    assert data == frozen and analysis_path.read_bytes() == original_bytes
    assert not hasattr(director, "source_snapshot") and not hasattr(director, "source_context")
    # Same shared director, legacy caller: no cross-request source contamination.
    calls.clear()
    director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=data)
    assert all(call["frozen_source_context"] is None for call in calls)


@pytest.mark.parametrize("tamper", ["body", "manifest", "snapshot_id", "owner_scope", "span", "quote", "quote_hash", "document_hash"])
def test_tampered_snapshot_or_evidence_blocks_before_any_complete(bound, tamper):
    _, snapshot, analysis, brief, ledger, _, _ = bound
    data = analysis.to_dict()
    if tamper == "body":
        snapshot = replace(snapshot, _bodies={"primary": snapshot.primary_bytes + b" altered"})
    elif tamper == "manifest":
        snapshot = replace(snapshot, created_at="changed")
    elif tamper in {"snapshot_id", "owner_scope"}:
        data[tamper] = "another"
    elif tamper == "span":
        data["evidence"][0]["byte_start"] += 1
    elif tamper == "quote":
        data["evidence"][0]["exact_quote"] += " fabricated"
    elif tamper == "quote_hash":
        data["evidence"][0]["quote_sha256"] = "0" * 64
    else:
        data["evidence"][0]["document_sha256"] = "0" * 64
    calls = []
    with pytest.raises(CoverDirectionError, match="frozen source context is invalid"):
        CoverArtDirector(complete=lambda *args: calls.append(args)).plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=data, source_snapshot=snapshot)
    assert calls == []


def test_full_source_is_charged_to_request_budget_without_truncation(bound):
    _, snapshot, analysis, brief, ledger, _, _ = bound
    data = analysis.to_dict()
    bare = CoverArtDirector._user_prompt(brief, 4, profile_version="cover-profiles.v10",
        evidence_ledger=ledger, story_analysis=data)
    bare_bytes = len((CoverArtDirector._system_prompt("cover-profiles.v10") + "\n" + bare).encode())
    calls = []
    director = CoverArtDirector(complete=lambda *args: calls.append(args))
    director.analysis_configuration = {"max_request_bytes": bare_bytes + 100}
    with pytest.raises(CoverDirectionError, match="no story facts or evidence were truncated"):
        director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=data, source_snapshot=snapshot)
    assert calls == []


def test_workflow_forwards_verified_snapshot_without_breaking_old_directors(bound):
    context, snapshot, analysis, _, _, payload, project = bound
    # The fixture's source directory is different from this independently opened store.
    flow = CoverWorkflow(context, CoverStore(project))
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps(supported_review(request)) if request["task"] == "review_campaign_semantics" else json.dumps(payload)
    flow.plan(analysis.analysis_id, CoverArtDirector(complete=complete))
    assert calls and all(call["frozen_source_context"]["snapshot_id"] == snapshot.snapshot_id for call in calls)
    assert all(TAIL in call["frozen_source_context"]["documents"][0]["text"] for call in calls)
    flow.plan(analysis.analysis_id, FullStoryFixture())  # Existing fixed signature remains usable.
    raw = context.cover_root / "sources" / snapshot.snapshot_id / "documents/primary.md"
    raw.write_bytes(raw.read_bytes() + b" tampered")
    calls.clear()
    with pytest.raises((CoverSourceError, ValueError)):
        flow.plan(analysis.analysis_id, CoverArtDirector(complete=complete))
    assert calls == []
