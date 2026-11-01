"""Reader review contracts, real token segmentation, evidence and durable control."""
import json
import threading
import time
from pathlib import Path

import pytest
from core.reader_experience import ReaderReviews, ReviewOptions, ReviewConflict
from core.reader_experience.models import DIMENSIONS, digest, text_sha
from core.reader_experience.sources import snapshot, units_for, resolve_quote, tokens
from core.usage_tracking import record_usage, read_records
from core.usage_costs import costs_payload, default_pricing


def project(tmp_path, count=2):
    root = tmp_path / "book"
    (root / "outputs/state").mkdir(parents=True)
    (root / "outputs/manuscript").mkdir()
    state = {"metadata": {"title": "Public Title", "author": "Writer", "target_chapters": count},
             "chapters": {str(i): {"number": i, "title": f"Chapter {i}", "status": "final", "secret": "PRIVATE ENDING"} for i in range(1, count + 1)}}
    (root / "outputs/state/story_state.json").write_text(json.dumps(state))
    for i in range(1, count + 1):
        (root / f"outputs/manuscript/chapter_{i:03d}_final.md").write_text(
            f"<!-- CHAPTER: {i} - Secret heading -->\n\n她🐺说：‘不。’ Chapter {i}.\n\n她选择离开，并付出代价。\n[SCRIBE_STATE_UPDATE]\nPRIVATE ENDING\n[/SCRIBE_STATE_UPDATE]")
    (root / "outputs/input").mkdir()
    (root / "outputs/input/prompt.md").write_text("PRIVATE SKELETON NEVER SEND")
    return root


MODEL = {"provider": "test", "model": "fake", "max_tokens": 6000, "timeout_seconds": 300,
         "base_url": "", "configured_base_url": "", "connection_id": "", "reasoning_effort": ""}


class FakeClient:
    def __init__(self):
        self.requests = []
        self.break_next = False

    def complete(self, system, user):
        data = json.loads(user)
        self.requests.append(data)
        assert "PRIVATE ENDING" not in user and "PRIVATE SKELETON" not in user
        record_usage(provider="test", model="fake", usage={"input_tokens": 100, "output_tokens": 30},
                     request_id=__import__('core.usage_tracking', fromlist=['current_request'])._request_identity.get())
        if self.break_next:
            self.break_next = False
            raise RuntimeError("provider interrupted")
        if data.get("stage") == "reading":
            unit = data["unit"]
            q = {"unit_id": unit["id"], "quote": unit["text"][:15], "start": unit["start"]}
            return json.dumps({"facts": [{"text": "She leaves at a cost.", "evidence": [q]}],
                               "strengths": [{"dimension": "character", "observation": "A concrete choice", "reader_impact": "A reason to care", "evidence": [q]}],
                               "issues": [], "continuity_summary": "She chose to leave."}, ensure_ascii=False)
        if data.get("stage") == "digest":
            from core.reader_experience.runtime import _evidence_ids
            return json.dumps({"covered_units": data["covered_units"], "facts": "Choices cost her something.",
                               "observations": "A concrete choice", "evidence_ids": sorted(_evidence_ids(data["records"])), "uncertainties": []})
        ids = list(data["evidence_catalog"])
        return json.dumps({"strengths": [{"dimension": "character", "scope": "local", "observation": "Concrete choice",
                             "reader_impact": "I want to learn its consequences", "preserve": "Her agency", "evidence_ids": ids[:1]}],
                           "issues": [], "dimensions": [{"dimension": d, "status": "insufficient", "explanation": "More evidence needed"} for d in DIMENSIONS],
                           "uncertainties": ["Simulated reading, not measured retention"]})


def service(root, fake=None, **kwargs):
    fake = fake or FakeClient()
    return ReaderReviews(root, root.name, client_factory=lambda model, cancel: fake, model_factory=lambda: MODEL.copy(), **kwargs), fake


def create(review, options=None, key="first"):
    options = options or ReviewOptions()
    quote = review.preview(options)
    rid, created = review.create(options, key, quote["proposal_sha256"])
    assert created
    return rid


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_end_to_end_and_read_only_gets(tmp_path):
    root = project(tmp_path)
    before = tree(root)
    review, fake = service(root)
    rid = create(review)
    result = review.run(rid, review.claim(rid))
    assert result["status"] == "completed"
    assert result["completed_units"] == 2
    assert result["evidence_status"] == "verified"
    assert len(fake.requests) == 3
    assert "public_expectations" not in fake.requests[0]
    assert "strengths" not in fake.requests[1]["previous_facts"]["summary"]
    assert fake.requests[-1]["public_expectations"]["title"] == "Public Title"
    for path, content in before.items():
        assert (root / path).read_bytes() == content
    persisted = tree(root)
    review.list(); review.get(rid); review.source(rid, 1)
    assert tree(root) == persisted
    assert not (root / "outputs/artifacts").exists()
    assert review.get(rid)["source_current"] is True
    span = next(iter(result["report"]["evidence"].values()))
    body = review.source(rid, span["chapter"])["body"]
    assert body[span["start"]:span["end"]] == span["quote"]
    records = list(read_records(root).values())
    assert len(records) == 3 and all(r["stage"] == "manuscript" and r["activity"] == "reader_review" for r in records)
    costs = costs_payload(records, default_pricing())
    assert costs["totals"]["total_tokens"] == 390
    assert next(r for r in costs["manuscript_activities"] if r["activity"] == "reader_review")["total_tokens"] == 390
    assert costs["totals"]["estimated_cost_cny"] is None


def test_idempotency_binding_and_stale_source(tmp_path):
    root = project(tmp_path)
    review, _ = service(root)
    options = ReviewOptions()
    preview = review.preview(options)
    rid, _ = review.create(options, "once", preview["proposal_sha256"])
    review.run(rid, review.claim(rid))
    (root / "outputs/manuscript/chapter_001_final.md").write_text("New manuscript")
    assert review.create(options, "once", preview["proposal_sha256"]) == (rid, False)
    assert review.get(rid)["source_current"] is False
    assert "她" in review.source(rid, 1)["body"]
    with pytest.raises(ReviewConflict):
        review.create(options, "second", preview["proposal_sha256"])
    with pytest.raises(ReviewConflict):
        review.create(ReviewOptions(audience="changed"), "once", preview["proposal_sha256"])


def test_resume_only_missing_work_and_unknown_failure_counted(tmp_path):
    review, fake = service(project(tmp_path))
    original = fake.complete
    def fail_second(system, user):
        if len(fake.requests) == 1:
            fake.break_next = True
        return original(system, user)
    fake.complete = fail_second
    rid = create(review)
    first = review.run(rid, review.claim(rid))
    assert first["status"] == "failed" and first["completed_units"] == 1
    fake.complete = original
    result = review.run(rid, review.claim(rid))
    assert result["status"] == "completed" and result["call_count"] == 4
    assert [r.get("unit", {}).get("id") for r in fake.requests] == ["u0001", "u0002", "u0002", None]


def test_pause_waits_for_worker_drain_then_reuses_saved_response(tmp_path):
    review, fake = service(project(tmp_path))
    started, release = threading.Event(), threading.Event()
    original = fake.complete
    def slow(system, user):
        started.set(); assert release.wait(5)
        return original(system, user)
    fake.complete = slow
    rid = create(review)
    fd = review.claim(rid)
    worker = threading.Thread(target=lambda: review.run(rid, fd))
    worker.start(); assert started.wait(5)
    result = review.pause(rid)
    assert result["pause_requested"] and result["worker_active"]
    with pytest.raises(ReviewConflict, match="仍在执行"):
        review.claim(rid)
    release.set(); worker.join(5)
    assert review.get(rid)["status"] == "paused"
    fake.complete = original
    result = review.run(rid, review.claim(rid))
    assert result["status"] == "completed" and len(fake.requests) == 3


def test_restart_read_only_interrupted_and_original_deadline(tmp_path):
    now = [1000.0]
    review, _ = service(project(tmp_path), clock=lambda: now[0])
    rid = create(review)
    before = tree(review.project)
    assert review.get(rid)["status"] == "interrupted"
    assert tree(review.project) == before
    now[0] += 1801
    with pytest.raises(ReviewConflict, match="时长预算"):
        review.claim(rid)


def test_partial_and_empty_final_do_not_resurrect_old_text(tmp_path):
    root = project(tmp_path, 3)
    (root / "outputs/manuscript/chapter_002_final.md").write_text("<!-- erased -->")
    (root / "outputs/manuscript/chapter_002_draft.md").write_text("OLD PROSE")
    review, _ = service(root)
    preview = review.preview(ReviewOptions())
    assert preview["missing_chapters"] == [2] and not preview["complete_book"]
    rid = create(review)
    assert review.run(rid, review.claim(rid))["status"] == "completed"
    with pytest.raises(KeyError):
        review.source(rid, 2)


def test_long_unicode_segmentation_no_gaps_and_duplicate_quotes(tmp_path):
    root = project(tmp_path, 1)
    body = ("她🐺不会退让。\n\n" * 1500) + "\n\nUnique ending."
    (root / "outputs/manuscript/chapter_001_final.md").write_text(body)
    source = snapshot(root, root.name, ReviewOptions())
    units = units_for(source, 1000)
    assert "".join(u["text"] for u in units) == source["chapters"][0]["body"]
    assert all(tokens(u["text"]) <= 1000 for u in units)
    assert all(a["end"] == b["start"] for a, b in zip(units, units[1:]))
    quote = {"unit_id": units[0]["id"], "quote": "她🐺不会退让。"}
    with pytest.raises(ValueError, match="重复"):
        resolve_quote(quote, source, units, {units[0]["id"]})
    span = resolve_quote({**quote, "start": 0}, source, units, {units[0]["id"]})
    assert span["end"] == len(quote["quote"])
    with pytest.raises(ValueError):
        resolve_quote({**quote, "start": 1}, source, units, {units[0]["id"]})
    chapter = source["chapters"][0]
    assert all(body[m["raw_start"]:m["raw_end"]] == chapter["body"][m["start"]:m["end"]] for m in chapter["mapping"])


def test_bad_evidence_never_certified_and_one_correction_cap(tmp_path):
    review, fake = service(project(tmp_path, 1))
    fake.complete = lambda system, user: json.dumps({"facts": [{"text": "invented", "evidence": [{"unit_id": "u0001", "quote": "NOT IN BOOK"}]}], "strengths": [], "issues": [], "continuity_summary": ""})
    rid = create(review)
    result = review.run(rid, review.claim(rid))
    assert result["status"] == "failed" and result["call_count"] == 2
    assert result["report"] is None and result["evidence_status"] == "pending"
    result = review.run(rid, review.claim(rid))
    assert result["call_count"] == 2  # saved bad correction doesn't cause new model calls


def test_budget_preflight_unknown_cost_and_call_limit(tmp_path):
    review, _ = service(project(tmp_path))
    with pytest.raises(ValueError, match="至少需要"):
        review.preview(ReviewOptions(max_calls=2))
    with pytest.raises(ValueError, match="没有完整单价"):
        review.preview(ReviewOptions(max_estimated_cny=10.0))


def test_snapshot_and_ids_cannot_escape_project(tmp_path):
    root = project(tmp_path)
    review, _ = service(root)
    with pytest.raises(ValueError):
        review.get("../outside")
    (root / "outputs/quality").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ReviewConflict):
        create(review)


def test_large_observations_are_hierarchically_summarized_without_losing_coverage(tmp_path):
    review, fake = service(project(tmp_path, 4))
    original = fake.complete
    def large(system, user):
        result = json.loads(original(system, user))
        if json.loads(user).get('stage') == 'reading':
            q = result['facts'][0]['evidence']
            result['facts'] = [{'text': f'Event {i} ' + '选择与代价并存。' * 95, 'evidence': q} for i in range(10)]
        return json.dumps(result, ensure_ascii=False)
    fake.complete = large
    rid = create(review, ReviewOptions(input_tokens=10000, segment_tokens=1000, output_tokens=12000))
    result = review.run(rid, review.claim(rid))
    assert result['status'] == 'completed', result['error']
    assert result['report']['covered_units'] == ['u0001', 'u0002', 'u0003', 'u0004']
    assert result['report']['lossy_summary_levels'] >= 1
    assert any(r['stage'] == 'digest' for r in fake.requests)


def test_price_and_model_are_frozen_and_known_spend_can_exhaust_budget(tmp_path, monkeypatch):
    from core.reader_experience import runtime
    pricing = default_pricing()
    pricing['models'].append({'provider': 'test', 'model': 'fake', 'input_usd_per_million': 1.0,
        'output_usd_per_million': 1.0, 'cached_input_usd_per_million': 1.0, 'image_usd_per_image': None})
    monkeypatch.setattr(runtime, 'load_pricing', lambda: pricing)
    review, fake = service(project(tmp_path, 1))
    rid = create(review, ReviewOptions(max_estimated_cny=1.0))
    before = review._manifest(rid)['binding']['pricing']
    pricing['usd_to_cny'] = 1000
    original = fake.complete
    def expensive(system, user):
        result = original(system, user)
        from core.usage_tracking import _request_identity
        record_usage(provider='test', model='fake', usage={'input_tokens': 1000000, 'output_tokens': 1000000}, request_id=_request_identity.get())
        return result
    fake.complete = expensive
    result = review.run(rid, review.claim(rid))
    assert result['status'] == 'budget_exhausted' and result['call_count'] == 1
    assert review._manifest(rid)['binding']['pricing'] == before
    assert result['usage']['estimated_cost_cny'] == 14.0


def test_local_sdk_drain_retains_lease_until_worker_really_exits(tmp_path):
    review, fake = service(project(tmp_path))
    draining, release = threading.Event(), threading.Event()
    def failed(system, user): raise TimeoutError('SDK local wait ended')
    def drain(timeout_seconds=None):
        draining.set()
        return release.wait(timeout_seconds)
    fake.complete, fake.wait_for_local_completion = failed, drain
    rid = create(review)
    fd = review.claim(rid)
    worker = threading.Thread(target=lambda: review.run(rid, fd))
    worker.start(); assert draining.wait(5)
    with pytest.raises(ReviewConflict): review.claim(rid)
    assert review.get(rid)['worker_active']
    release.set(); worker.join(5)
    assert not worker.is_alive()
    assert review.get(rid)['status'] == 'failed'


def test_exact_cleaning_map_repeated_text_embedded_blocks_and_unicode():
    from core.prose_sanitize import readable_prose_mapping
    from core.manuscript_reader import _clean_prose
    raw = '  🐺same<!-- CHAPTER: 1 - title -->same\n[EDITOR_ANALYSIS]\nsecret\n[/EDITOR_ANALYSIS] same🐺  '
    body, mapping = readable_prose_mapping(raw)
    assert body == _clean_prose(raw)[0]
    assert ''.join(raw[m['raw_start']:m['raw_end']] for m in mapping) == body
    assert mapping[0]['start'] == 0 and mapping[-1]['end'] == len(body)
    assert all(a['end'] == b['start'] for a, b in zip(mapping, mapping[1:]))


def test_legacy_review_survives_normal_project_identity_initialization(tmp_path):
    from core.project_identity import ensure_project_instance_id
    root = project(tmp_path)
    review, _ = service(root)
    rid = create(review)
    ensure_project_instance_id(root)
    result = review.run(rid, review.claim(rid))
    assert result['status'] == 'completed'
    assert review.source(rid, 1)['body']


def test_project_instance_fork_cannot_reuse_an_existing_review(tmp_path):
    from core.project_identity import ensure_project_instance_id, fork_project_instance_id
    root = project(tmp_path)
    ensure_project_instance_id(root)
    review, _ = service(root)
    rid = create(review)
    fork_project_instance_id(root)
    with pytest.raises(ReviewConflict, match='实例'):
        review.get(rid)


def test_concurrent_idempotency_and_cross_process_lease(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import subprocess, sys, os
    root = project(tmp_path)
    review, _ = service(root)
    options = ReviewOptions()
    proposal = review.preview(options)['proposal_sha256']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: review.create(options, 'same-key', proposal), range(4)))
    assert len({rid for rid, created in results}) == 1
    assert sum(created for rid, created in results) == 1
    rid = results[0][0]
    fd = review.claim(rid)
    try:
        code = '''import sys, os
from core.reader_experience.store import ReviewStore, ReviewConflict
try:
    fd = ReviewStore(sys.argv[1]).acquire_worker(sys.argv[2])
except ReviewConflict:
    sys.exit(0)
os.close(fd)
sys.exit(1)
'''
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1]) + os.pathsep + str(Path(__file__).parents[1] / 'core')}
        result = subprocess.run([sys.executable, '-c', code, str(root), rid], env=env, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr.decode()
    finally:
        os.close(fd)
    assert review.get(rid)['status'] == 'interrupted'


def test_history_index_never_loads_full_manuscripts(tmp_path, monkeypatch):
    review, _ = service(project(tmp_path))
    rid = create(review)
    original = review.store.read
    def read(path):
        assert not path.endswith('/snapshot.json')
        return original(path)
    monkeypatch.setattr(review.store, 'read', read)
    assert review.list()[0]['id'] == rid


def test_real_adapter_cancellation_exposes_local_drain_without_changing_old_wait_behavior():
    from llm_client import LLMClient, LLMCancelledError
    adapter = object.__new__(LLMClient)
    adapter._backend = None
    adapter.cancellation_event = threading.Event()
    adapter.timeout_seconds = 5
    started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def request():
        started.set(); release.wait(5); return 'late'
    def caller():
        try: adapter._call_http(request)
        except LLMCancelledError: cancelled.set()
    worker = threading.Thread(target=caller)
    worker.start(); assert started.wait(5)
    adapter.cancellation_event.set()
    assert cancelled.wait(5)
    assert not adapter.wait_for_local_completion(0)
    release.set(); worker.join(5)
    assert adapter.wait_for_local_completion(5)
