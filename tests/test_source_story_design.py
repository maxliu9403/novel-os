"""Source design regression using isolated bundles and deterministic model fixtures."""
from copy import deepcopy
import json
import threading

import pytest

from core.creative_types import digest
from core.reference_assets import atomic_json
from core.source_library import SourceLibrary
from core.llm_client import LLMInputLimitError
from core.source_routes import SourceRouteConflict
from core.source_story_design import SourceStoryDesign


CATALOG = [{"id": "genre.romance", "kind": "genre", "name": "Romance", "version": 1, "sha256": "a" * 64},
           {"id": "tag.trust", "kind": "tag", "name": "Trust", "version": 1, "sha256": "b" * 64}]


def bundle(size=1):
    chapters = [{"index": n, "path": f"chapters/{n}.md", "title": f"Chapter {n}"} for n in (1, 2)]
    return {"metadata.json": json.dumps({"novel_name": "The Returned Letter"}).encode(),
            "chapters.json": json.dumps(chapters).encode(), "synopsis.md": b"A promotional promised reunion, not an occurred event.",
            "chapters/1.md": ("The first promise remains unresolved.\n" * size).encode(),
            "chapters/2.md": ("She receives the letter and must choose.\n" * size).encode(),
            "chapters/appendix.md": b"Preview: someone is crowned. This is not the narrative endpoint."}


def service(tmp_path, size=1):
    library = SourceLibrary(tmp_path / "sources")
    manifest = library.import_files(bundle(size))
    return SourceStoryDesign(library, manifest["id"])


def route(slot, refs, *, suffix=""):
    return {"slot": slot, "title": "A costly choice " + suffix,
            "reader_promise": "Whether she can regain trust without surrendering her livelihood.",
            "continuity_basis": "The letter forces an answer to the first promise.", "source_refs": refs,
            "causal_arc": {"opening": "She opens the letter at the auction.", "development": "She funds her own bid.",
                "turning_point": "Her ally refuses the secret agreement.", "climax": "She publicly accepts the debt.",
                "ending": "Her shop opens, and she chooses whether to invite him."},
            "character_relationship_arcs": ["An unequal bargain becomes a voluntary partnership."],
            "emotional_design": [{"expectation": "She deserves an answer.", "scene": "A letter is read in public.",
                "choice": "She risks rejection.", "cost": "Her reputation.", "change": "Readers see her agency.",
                "payoff": "The partnership is freely chosen."}],
            "highlights": [{"scene": "The auction confrontation.", "story_value": "She gains agency."}],
            "long_hooks_and_payoffs": [{"question": "Will the promise be kept?", "development": "A costly test.", "payoff": "A public answer."}],
            "type_suggestion": {"audience": "female", "genre_id": "genre.romance", "tag_ids": ["tag.trust"], "reason": "Trust changes the decisive bargain."},
            "proposed_assumptions": ["The auction is newly invented."], "tradeoffs": [],
            "difference_from_existing": "A public bargain resolves the relationship.", "changes_from_parent": suffix}


class Model:
    provider = "fixture"
    model = "deterministic"
    planning_input_bytes = None

    def __init__(self):
        self.calls = []

    def complete(self, system, user):
        value = json.loads(user)
        self.calls.append((system, value))
        return json.dumps(self.response(value))

    def response(self, value):
        title = value["response_schema"]["title"]
        refs = [r["id"] for r in value["source_view"]]
        if not refs:
            refs = [ref for r in value["comparison_routes"] for ref in r["source_refs"]]
        content = {"story_summary": "All supplied events remain separate from proposals.",
                   "narrative_endpoint": "CROWNED PREVIEW" if value["source_view"] and all(s.get("scope") != "listed_chapter" for s in value["source_view"]) else "LETTER RECEIVED",
                   "opening_analysis": "A promise creates a concrete emotional expectation.",
                   "open_threads": ["The first promise."],
                   "observations": [{"local_id": str(n), "kind": "observed_event" if s.get("scope") == "listed_chapter" else "uncertain_inference",
                       "statement": s["text"].splitlines()[0], "source_refs": [s["id"]], "supersedes": []}
                       for n, s in enumerate(value["source_view"])]}
        if title == "ReadingResponse":
            return {"understanding": content, "more": False, "continuation": ""}
        if title == "IntegrationResponse":
            return {"routes": [], "recommended_slot": "", "recommendation_reason": "", "evidence_requests": []}
        slots = value["task"]["allowed_output_slots"]
        result = {"routes": [route(slot, refs, suffix=value.get("feedback", "")) for slot in slots],
                  "recommended_slot": slots[0], "recommendation_reason": "The earliest promise gets a concrete payoff."}
        if title == "InitialResponse":
            result["understanding"] = content
        return result


def create(study, model=None):
    model = model or Model()
    state = study.prepare({"operation_id": "initial"}, model={"model": "fixture", "api_key": "must-not-persist"}, catalog=CATALOG)
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    return study.get_route_set(state["route_set_sha256"]), state, model


def test_full_source_one_call_private_evidence_and_old_analysis_untouched(tmp_path):
    study = service(tmp_path)
    legacy = study.library.path(study.source_id) / "study" / "result.json"
    atomic_json(legacy, {"legacy": "saved paid analysis"})
    original = legacy.read_bytes()
    result, state, model = create(study)
    assert len(model.calls) == 1
    assert len(result["routes"]) == 3
    assert legacy.read_bytes() == original
    evidence = study.export_evidence(result["understanding_sha256"])
    assert len(evidence["segments"]) == 4
    assert evidence["coverage"]["all_supplied_text_sent"]
    assert "must-not-persist" not in "".join(p.read_text() for p in study.root.rglob("*.json"))
    assert "顶级商业小说" in model.calls[0][0]
    assert "前几章的代入感与张力机制" in model.calls[0][0]
    assert "不可逆倾斜" not in json.dumps(model.calls[0][1]["response_schema"])
    before = {p: p.read_bytes() for p in study.root.rglob("*.json")}
    assert study.get_run_context(state["run_id"])["request"]["operation_id"] == "initial"
    study.status(); study.list_runs(); study.list_route_sets(); study.get_route_set(result["sha256"])
    assert before == {p: p.read_bytes() for p in study.root.rglob("*.json")}


def test_revision_and_expansion_preserve_history_exact_feedback_and_selection(tmp_path):
    study = service(tmp_path)
    first, _, _ = create(study)
    original = deepcopy(first)
    text = "  Keep A's ending. Make B more moving, then add two routes.\n"
    body = {"operation_id": "mixed", "operation": "revise_routes", "base_route_set_sha256": first["sha256"],
            "feedback": text, "target_route_ids": [first["routes"][1]["route_id"]],
            "author_context": {"premise": "Preserve the shop", "creative_types": [{"id": "genre.romance", "version": 1}]},
            "requested_actions": [{"operation": "revise_routes", "target_route_ids": [first["routes"][1]["route_id"]]},
                                  {"operation": "expand_routes", "requested_count": 2}]}
    state = study.prepare(body, model={}, catalog=CATALOG)
    model = Model()
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    revised = study.get_route_set(state["route_set_sha256"])
    assert len(revised["routes"]) == 5
    assert revised["routes"][0] == first["routes"][0]
    assert revised["routes"][1]["route_id"] == first["routes"][1]["route_id"]
    assert revised["routes"][1]["parent_sha256"] == first["routes"][1]["sha256"]
    assert revised["feedback_history"][-1]["exact_text"] == text
    assert study.get_route_set(first["sha256"]) == original
    assert all(call[1]["history"][-1]["exact_text"] == text for call in model.calls)
    assert study.prepare(body, model={"model": "switched"}, catalog=[]) == state
    with pytest.raises(SourceRouteConflict):
        study.prepare({**body, "feedback": "different"}, model={}, catalog=CATALOG)


def test_cancel_after_paid_response_recovers_without_another_call(tmp_path):
    study = service(tmp_path)
    state = study.prepare({"operation_id": "cancel"}, model={}, catalog=CATALOG)
    cancel = threading.Event()
    class PaidThenCancel(Model):
        def complete(self, system, user):
            raw = super().complete(system, user)
            cancel.set()
            return raw
    model = PaidThenCancel()
    assert study.run(state["run_id"], model, cancel)["status"] == "paused"
    cancel.clear()
    assert study.run(state["run_id"], model, cancel)["status"] == "ready"
    assert len(model.calls) == 1


def test_unknown_remote_result_is_not_blindly_resent(tmp_path):
    study = service(tmp_path)
    state = study.prepare({"operation_id": "unknown"}, model={}, catalog=CATALOG)
    class Disconnected(Model):
        def complete(self, system, user):
            self.calls.append(user)
            raise ConnectionError("reply lost")
    model = Disconnected()
    assert study.run(state["run_id"], model)["status"] == "pending_unknown"
    assert study.run(state["run_id"], model)["status"] == "pending_unknown"
    assert len(model.calls) == 1


def test_invalid_reference_repairs_only_received_artifact_and_preserves_raw(tmp_path):
    study = service(tmp_path)
    class WrongReference(Model):
        def response(self, payload):
            response = super().response(payload)
            if "protocol_correction" not in payload:
                response["routes"][0]["source_refs"] = ["made-up-id"]
            return response
    result, state, model = create(study, WrongReference())
    assert len(model.calls) == 2
    receipts = list(study.root.glob(f"runs/{state['run_id']}/requests/*.json"))
    assert len(receipts) == 2
    assert any("made-up-id" in json.loads(p.read_text())["raw"] for p in receipts)
    assert all("made-up-id" not in r["source_refs"] for r in result["routes"])


def test_insufficient_core_capacity_preserves_source_and_inputs(tmp_path):
    study = service(tmp_path)
    model = Model()
    model.planning_input_bytes = 100
    state = study.prepare({"operation_id": "small"}, model={}, catalog=CATALOG)
    result = study.run(state["run_id"], model)
    assert result["status"] == "needs_capacity"
    assert not model.calls
    inputs = study.store.read("runs", state["run_id"], "inputs.json")
    assert len(inputs["snapshot"]["segments"]) == 4
    model.planning_input_bytes = None
    assert study.run(state["run_id"], model)["status"] == "ready"


def test_partitioned_reading_preserves_every_character_and_ignores_preview_endpoint(tmp_path):
    study = service(tmp_path, size=900)
    model = Model()
    model.planning_input_bytes = 36_000
    state = study.prepare({"operation_id": "partition", "requested_count": 2}, model={}, catalog=CATALOG)
    result = study.run(state["run_id"], model)
    assert result["status"] == "ready", result
    artifact = study.get_route_set(result["route_set_sha256"])
    evidence = study.export_evidence(artifact["understanding_sha256"])
    assert evidence["narrative_endpoint"] == "LETTER RECEIVED"
    for original in study.library.texts(study.source_id):
        pieces = sorted((s for s in evidence["segments"] if s["path"] == original["path"]), key=lambda s: s["start"])
        assert "".join(s["text"] for s in pieces) == original["text"]
    sent = json.dumps([v for _, v in model.calls], ensure_ascii=False)
    assert "The first promise remains unresolved" in sent
    assert len(evidence["ledger"]) > 4
    integrations = [v for _, v in model.calls if v["task"]["operation"] == "integrate"]
    assert integrations
    assert any(len(v["comparison_routes"]) == 2 for v in integrations), "Capacity permits the routes to share an evidence sweep."
    assert all(len(system.encode()) + len(json.dumps(v, ensure_ascii=False).encode()) + 5120 <= model.planning_input_bytes for system, v in model.calls)


def test_natural_mixed_feedback_revises_then_adds_without_classifier_call(tmp_path):
    study = service(tmp_path)
    first, _, _ = create(study)
    target = first["routes"][1]["route_id"]
    feedback = "完善 B 的事业看点，再给两个不同方向。不要修改 A。"
    class Mixed(Model):
        def response(self, value):
            response = super().response(value)
            if value["task"]["operation"] == "revise_routes":
                response["followup_actions"] = [{"operation": "expand_routes", "requested_count": 2,
                    "target_route_ids": [], "author_quote": "再给两个不同方向"}]
            return response
    state = study.prepare({"operation_id": "natural-mixed", "operation": "revise_routes", "feedback": feedback,
        "base_route_set_sha256": first["sha256"], "target_route_ids": [target]}, model={}, catalog=CATALOG)
    model = Mixed()
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    assert [v["task"]["operation"] for _, v in model.calls] == ["revise_routes", "expand_routes"]
    revised = study.get_route_set(state["route_set_sha256"])
    assert len(revised["routes"]) == 5
    assert revised["routes"][0] == first["routes"][0]
    assert revised["routes"][1]["route_id"] == target
    plans = list(study.root.glob(f"runs/{state['run_id']}/action-plans/*.json"))
    assert any(json.loads(p.read_text())["author_text"] == feedback for p in plans)
    assert [a["operation"] for a in revised["executed_actions"]] == ["revise_routes", "expand_routes"]


def test_deferred_source_correction_precedes_revision_and_retains_old_understanding(tmp_path):
    study = service(tmp_path)
    first, _, _ = create(study)
    before = study.get_understanding(first["understanding_sha256"])
    feedback = "先核对人物说谎这件事，再完善全部路线。"
    class CorrectFirst(Model):
        def response(self, value):
            response = super().response(value)
            if value["task"]["operation"] == "revise_routes" and not value["task"]["completed_actions"]:
                response.update(routes=[], recommended_slot="", defer_current=True, followup_actions=[
                    {"operation": "revisit_source", "author_quote": "先核对人物说谎这件事"},
                    {"operation": "revise_routes", "author_quote": "再完善全部路线"}])
            return response
    state = study.prepare({"operation_id": "correct-first", "operation": "revise_routes", "feedback": feedback,
                           "base_route_set_sha256": first["sha256"]}, model={}, catalog=CATALOG)
    model = CorrectFirst()
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    operations = [v["task"]["operation"] for _, v in model.calls]
    assert operations == ["revise_routes", "revisit_source", "revise_routes"]
    revised = study.get_route_set(state["route_set_sha256"])
    assert revised["understanding_sha256"] != first["understanding_sha256"]
    assert study.get_understanding(first["understanding_sha256"]) == before
    assert len(revised["routes"]) == 3


def test_model_cannot_invent_author_quote_or_out_of_scope_followup(tmp_path):
    study = service(tmp_path)
    first, _, _ = create(study)
    class Unrequested(Model):
        def response(self, value):
            response = super().response(value)
            response["followup_actions"] = [{"operation": "expand_routes", "requested_count": 20,
                                              "author_quote": "invented author approval"}]
            return response
    state = study.prepare({"operation_id": "bad-plan", "operation": "revise_routes", "feedback": "只完善 A。",
        "target_route_ids": [first["routes"][0]["route_id"]], "base_route_set_sha256": first["sha256"]}, model={}, catalog=CATALOG)
    model = Unrequested()
    assert study.run(state["run_id"], model)["status"] == "failed"
    assert len(model.calls) == 2, "One bounded protocol correction, never execute invented actions."
    assert len(study.list_route_sets()) == 1


def test_late_candidate_change_rechecks_early_source_against_current_version(tmp_path):
    study = service(tmp_path, size=900)
    class LateChange(Model):
        changed = False
        def response(self, value):
            response = super().response(value)
            if (value["task"]["operation"] == "integrate" and not self.changed
                    and any(s["path"] == "chapters/2.md" for s in value["source_view"])):
                candidate = deepcopy(value["comparison_routes"])
                candidate[0]["causal_arc"]["ending"] = "She answers the early promise by keeping the shop independent."
                response["routes"] = candidate
                self.changed = True
            return response
    model = LateChange()
    model.planning_input_bytes = 36_000
    state = study.prepare({"operation_id": "closure", "requested_count": 2}, model={}, catalog=CATALOG)
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    early_checks = [v for _, v in model.calls if v["task"]["operation"] == "integrate"
                    and any(s["path"] == "chapters/1.md" for s in v["source_view"])]
    assert any("keeping the shop independent" in v["comparison_routes"][0]["causal_arc"]["ending"] for v in early_checks)
    result = study.get_route_set(state["route_set_sha256"])
    assert "keeping the shop independent" in result["routes"][0]["causal_arc"]["ending"]


def test_output_truncation_splits_complete_routes_and_keeps_original_replies(tmp_path):
    study = service(tmp_path)
    class ShortOutput(Model):
        def complete(self, system, user):
            raw = super().complete(system, user)
            payload = self.calls[-1][1]
            if payload["response_schema"]["title"] == "InitialResponse" or (payload["response_schema"]["title"] == "RouteResponse" and len(payload["task"]["allowed_output_slots"]) > 1):
                return raw[:100]
            return raw
    result, state, model = create(study, ShortOutput())
    assert len(result["routes"]) == 3
    assert all(r["causal_arc"]["ending"] for r in result["routes"])
    receipts = [json.loads(p.read_text()) for p in study.root.glob(f"runs/{state['run_id']}/requests/*.json")]
    assert any(len(r["raw"]) == 100 for r in receipts)
    assert not any("protocol_correction" in value for _, value in model.calls), "Incomplete output is paged, not guessed into JSON."


def test_single_route_output_pages_retain_causal_emotional_and_ending_fields(tmp_path):
    study = service(tmp_path)
    class PageOutput(Model):
        def complete(self, system, user):
            payload = json.loads(user)
            title = payload["response_schema"]["title"]
            if title in {"InitialResponse", "RouteResponse"}:
                self.calls.append((system, payload))
                return '{"routes": ['
            if title.startswith("Route") and title.endswith("Page"):
                self.calls.append((system, payload))
                content = route(payload["task"]["allowed_output_slots"][0], [s["id"] for s in payload["source_view"]])
                content.update(followup_actions=[], defer_current=False)
                return json.dumps({k: content[k] for k in payload["response_schema"]["properties"]})
            return super().complete(system, user)
    model = PageOutput()
    state = study.prepare({"operation_id": "output-pages", "requested_count": 1}, model={}, catalog=CATALOG)
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    content = study.get_route_set(state["route_set_sha256"])["routes"][0]
    assert content["causal_arc"]["ending"]
    assert content["emotional_design"][0]["payoff"]
    assert content["long_hooks_and_payoffs"][0]["payoff"]
    assert len([p for _, p in model.calls if "output_page" in p]) == 3


def test_invalid_complete_response_can_recover_with_current_model_without_discarding_work(tmp_path):
    study = service(tmp_path)
    class Broken(Model):
        model = "old"
        def response(self, value):
            response = super().response(value)
            response["routes"][0]["type_suggestion"]["genre_id"] = "genre.nonexistent"
            return response
    state = study.prepare({"operation_id": "switch-repair"}, model={}, catalog=CATALOG)
    broken = Broken()
    assert study.run(state["run_id"], broken)["status"] == "failed"
    previous = {p: json.loads(p.read_text())["raw"] for p in study.root.glob(f"runs/{state['run_id']}/requests/*.json")}
    good = Model(); good.model = "current"
    result = study.run(state["run_id"], good)
    assert result["status"] == "ready", result
    assert len(good.calls) == 1
    assert all(json.loads(p.read_text())["raw"] == raw for p, raw in previous.items())


def test_paid_complete_response_reused_when_new_connection_has_smaller_capacity(tmp_path):
    study = service(tmp_path)
    state = study.prepare({"operation_id": "paid-switch"}, model={}, catalog=CATALOG)
    cancel = threading.Event()
    class Paid(Model):
        def complete(self, system, user):
            raw = super().complete(system, user)
            cancel.set()
            return raw
    paid = Paid()
    assert study.run(state["run_id"], paid, cancel)["status"] == "paused"
    smaller = Model(); smaller.planning_input_bytes = 100
    assert study.run(state["run_id"], smaller)["status"] == "ready"
    assert not smaller.calls


def test_source_correction_pages_complete_old_whole_chapter_observations(tmp_path):
    study = service(tmp_path)
    class ManyObservations(Model):
        def response(self, value):
            response = super().response(value)
            if value["response_schema"]["title"] == "InitialResponse":
                span = next(s for s in value["source_view"] if s["path"] == "chapters/1.md")
                response["understanding"]["observations"].extend({"local_id": f"detail_{n}",
                    "kind": "uncertain_inference", "statement": f"Important detail {n}: " + "x" * 400,
                    "source_refs": [span["id"]], "supersedes": []} for n in range(80))
            return response
    first, _, _ = create(study, ManyObservations())
    prior = study.get_understanding(first["understanding_sha256"])
    state = study.prepare({"operation_id": "revisit-small", "operation": "revisit_source",
        "feedback": "核对第一章人物说法的可信度，保留所有未解决细节。", "base_route_set_sha256": first["sha256"]}, model={}, catalog=CATALOG)
    model = Model(); model.planning_input_bytes = 36_000
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    revisits = [v for _, v in model.calls if v["task"]["operation"] == "revisit_source"]
    checked = {r["id"] for value in revisits for r in value["prior_observations"]}
    assert {r["id"] for r in prior["ledger"]} <= checked
    new = study.get_understanding(state["understanding_sha256"])
    addresses = {s["id"] for s in new["segments"] + new["additional_segments"]}
    assert all(set(r["source_refs"]) <= addresses for r in new["ledger"])
    assert set(prior["open_threads"]) <= set(new["open_threads"])


def test_historical_comparison_can_cite_the_supplied_old_route_evidence(tmp_path):
    study = service(tmp_path, size=900)
    first, _, _ = create(study)
    class Historical(Model):
        def response(self, value):
            response = super().response(value)
            if value.get("historical_comparison"):
                content = deepcopy(value["comparison_routes"][0])
                if content["source_refs"] != value["historical_comparison"][0]["source_refs"]:
                    content["source_refs"] = value["historical_comparison"][0]["source_refs"]
                    response["routes"] = [content]
            return response
    state = study.prepare({"operation_id": "compare-history", "operation": "expand_routes", "requested_count": 1,
                          "base_route_set_sha256": first["sha256"]}, model={}, catalog=CATALOG)
    model = Historical(); model.planning_input_bytes = 36_000
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    assert len(study.get_route_set(state["route_set_sha256"])["routes"]) == 4


def test_returned_502_recovers_once_automatically_with_attempt_evidence(tmp_path):
    study = service(tmp_path)
    class ReturnedHTTPError(Exception):
        status_code = 502
    class Transient(Model):
        def complete(self, system, user):
            if not self.calls:
                self.calls.append((system, json.loads(user)))
                raise ReturnedHTTPError()
            return super().complete(system, user)
    result, state, model = create(study, Transient())
    assert len(model.calls) == 2
    receipts = [json.loads(p.read_text()) for p in study.root.glob(f"runs/{state['run_id']}/requests/*.json")]
    assert len(receipts) == 1
    assert receipts[0]["attempts"][0]["outcome"]["http_status"] == 502
    assert receipts[0]["attempts"][1]["outcome"]["outcome"] == "response_received"


def test_returned_401_can_resume_after_configuration_fix(tmp_path):
    study = service(tmp_path)
    class Unauthorized(Exception):
        status_code = 401
    class BrokenConfiguration(Model):
        def complete(self, system, user):
            self.calls.append((system, json.loads(user)))
            raise Unauthorized()
    state = study.prepare({"operation_id": "config-fix"}, model={}, catalog=CATALOG)
    bad = BrokenConfiguration()
    assert study.run(state["run_id"], bad)["status"] == "failed"
    assert len(bad.calls) == 1
    fixed = Model()
    assert study.run(state["run_id"], fixed)["status"] == "ready"
    assert len(fixed.calls) == 1


def test_large_catalog_is_read_as_complete_records_instead_of_blocking_source_reading(tmp_path):
    study = service(tmp_path)
    catalog = deepcopy(CATALOG)
    catalog.extend({"id": f"tag.extra_{n}", "kind": "tag", "name": f"Additional mechanism {n}"} for n in range(7))
    for n, row in enumerate(catalog):
        row["description"] = f"Complete definition {n}: " + "mechanism " * 300
    state = study.prepare({"operation_id": "large-catalog", "requested_count": 2}, model={}, catalog=catalog)
    model = Model(); model.planning_input_bytes = 44_000
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    reads = [value for _, value in model.calls if value["task"]["operation"] == "read_source"]
    assert reads and all(not value["valid_type_catalog"] for value in reads)
    transmitted = {row["id"]: row for _, value in model.calls for row in value.get("catalog_records", [])}
    assert transmitted == {row["id"]: row for row in catalog}


def test_corrected_open_thread_is_historical_not_forced_into_current_design(tmp_path):
    study = service(tmp_path)
    first, _, _ = create(study)
    class Resolved(Model):
        def response(self, value):
            response = super().response(value)
            if "understanding" in response:
                response["understanding"]["open_threads"] = []
            return response
    state = study.prepare({"operation_id": "correct-open-thread", "operation": "revisit_source",
        "feedback": "原承诺已在第二章回应，请核对并修正。", "base_route_set_sha256": first["sha256"]}, model={}, catalog=CATALOG)
    model = Resolved()
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    current = study.get_understanding(state["understanding_sha256"])
    assert current["open_threads"] == []
    assert any(row["text"] == "The first promise." for row in current["historical_open_threads"])
    correction_calls = [value for _, value in model.calls if value["task"]["operation"] == "revisit_source"]
    assert any(value["prior_open_threads"] for value in correction_calls)


def test_recommended_channel_must_be_supported_by_every_selected_catalog_item(tmp_path):
    study = service(tmp_path)
    catalog = [dict(row, audience_channels=["male"]) for row in CATALOG]
    state = study.prepare({"operation_id": "audience"}, model={}, catalog=catalog)
    model = Model()
    state = study.run(state["run_id"], model)
    assert state["status"] == "failed"
    assert "读者方向" in state["error"]
    assert len(model.calls) == 2


def test_trusted_capacity_rejection_repartitions_automatically_without_omitting_source(tmp_path):
    study = service(tmp_path, size=900)
    class Guarded(Model):
        rejections = 0
        def complete(self, system, user):
            actual = len(system.encode()) + len(user.encode()) + 5120
            if actual > 36_000:
                self.rejections += 1
                raise LLMInputLimitError(actual, 36_000)
            return super().complete(system, user)
    model = Guarded()
    state = study.prepare({"operation_id": "discovered-capacity", "requested_count": 2}, model={}, catalog=CATALOG)
    state = study.run(state["run_id"], model)
    assert state["status"] == "ready", state
    assert model.rejections == 1
    assert state["model_calls"] == len(model.calls)
    evidence = study.export_evidence(state["understanding_sha256"])
    for original in study.library.texts(study.source_id):
        pieces = sorted((s for s in evidence["segments"] if s["path"] == original["path"]), key=lambda s: s["start"])
        assert "".join(s["text"] for s in pieces) == original["text"]


@pytest.mark.parametrize('publication_interrupted', [False, True])
def test_incomplete_final_output_page_can_resume_without_regenerating_completed_pages(tmp_path, monkeypatch, publication_interrupted):
    study = service(tmp_path)
    class PageLimited(Model):
        def complete(self, system, user):
            payload = json.loads(user)
            title = payload["response_schema"]["title"]
            if title in {"InitialResponse", "RouteResponse"}:
                self.calls.append((system, payload))
                return '{"routes": ['
            if title.startswith("Route") and title.endswith("Page"):
                self.calls.append((system, payload))
                if title == "RouteEmotionPage" and not payload["output_page"].get("recovery_revision"):
                    return '{"emotional_design": ['
                content = route(payload["task"]["allowed_output_slots"][0], [s["id"] for s in payload["source_view"]])
                content.update(followup_actions=[], defer_current=False)
                return json.dumps({k: content[k] for k in payload["response_schema"]["properties"]})
            return super().complete(system, user)
    state = study.prepare({"operation_id": "page-recovery", "requested_count": 1}, model={}, catalog=CATALOG)
    first = PageLimited()
    assert study.run(state["run_id"], first)["status"] == "failed"
    saved = {p: json.loads(p.read_text())["raw"] for p in study.root.glob(f"runs/{state['run_id']}/requests/*.json")}
    current = PageLimited(); current.model = "larger-output-model"
    if publication_interrupted:
        from core.source_design_store import SourceDesignStore
        original = SourceDesignStore.put_artifact
        def fail_publish(store, kind, body):
            if kind == 'route-sets':
                raise OSError('simulated publication interruption')
            return original(store, kind, body)
        monkeypatch.setattr(SourceDesignStore, 'put_artifact', fail_publish)
        with pytest.raises(OSError, match='publication interruption'):
            study.run(state['run_id'], current)
        monkeypatch.setattr(SourceDesignStore, 'put_artifact', original)
        third = PageLimited()
        assert study.run(state['run_id'], third)['status'] == 'ready'
        assert third.calls == []
    else:
        result = study.run(state["run_id"], current)
        assert result["status"] == "ready", result
    assert len(current.calls) == 1
    assert current.calls[0][1]["response_schema"]["title"] == "RouteEmotionPage"
    assert all(json.loads(path.read_text())["raw"] == raw for path, raw in saved.items())
