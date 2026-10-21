import json

import pytest

from narrative_methods import MethodPolicy, NarrativeMethods, ReviewInput


def sample(**overrides):
    return ReviewInput(**{
        "revision_id": "revision-one", "chapter": 1,
        "text": "Mara closed the door. She closed the door. 🌿 Then she spoke.",
        "language": "English", "free_trial_end": 3,
        "contracts": {"story": "approved-story"}, **overrides,
    })


def test_advisory_compiles_only_review_guidance_and_validates_exact_evidence():
    methods = NarrativeMethods()
    packet = methods.compile(sample(), MethodPolicy())
    assert packet.production_guidance == ""
    assert packet.status == "ready"
    raw = json.dumps({
        "summary": "Repeated action may not change the scene.",
        "reviewed_rules": list(packet.rule_ids),
        "findings": [{
            "rule_id": "nonfunctional_reaction", "severity": "major",
            "explanation": "The second action adds no new consequence.",
            "suggestion": "Check whether the repetition has a deliberate function.",
            "evidence": [{"start": 0, "end": 22, "quote": "Mara closed the door."}],
        }],
    })
    result = methods.validate(sample(), packet, raw)
    assert result["status"] == "valid"
    finding = result["findings"][0]
    assert finding["evidence"][0]["end"] == 21
    assert finding["evidence"][0]["location_corrected"] is True
    assert finding["gate_disposition"] == "advisory"
    assert result["blocking"] is False


def test_off_does_not_load_resources_or_compile_review(tmp_path):
    packet = NarrativeMethods(tmp_path / "absent").compile(sample(), MethodPolicy(mode="off"))
    assert packet.status == "off"
    assert packet.review_system == packet.review_user == packet.production_guidance == ""


@pytest.mark.parametrize("value", ["enforced", "other"])
def test_unimplemented_active_modes_are_rejected(value):
    with pytest.raises(ValueError):
        MethodPolicy(mode=value)


def test_review_is_persisted_reused_and_keeps_manuscript_unchanged(tmp_path):
    from narrative_methods.runtime import MethodReviews

    from project_identity import ensure_project_instance_id
    ensure_project_instance_id(tmp_path)
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("Original novel.\n")

    class Client:
        provider_name = "test"
        model = "fake"
        reasoning_effort = "low"
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            rules = [r for m in payload["methods"] for r in m["rules"]]
            return json.dumps({"summary": "No suggestions.", "reviewed_rules": rules, "findings": []})

    client = Client()
    runtime = MethodReviews(tmp_path, client_factory=lambda: client)
    lock = runtime.prepare("run1", MethodPolicy(), language="English", free_trial_end=3)
    first = runtime.review("run1", sample())
    second = MethodReviews(tmp_path, client_factory=lambda: client).review("run1", sample())
    assert first == second
    assert first["status"] == "valid" and first["blocking"] is False
    assert first["method_lock_sha256"] == lock["sha256"]
    assert first["usage"]["model_calls"] == 1
    assert first["usage"]["tokens"] is None
    assert client.calls == 1
    assert manuscript.read_text() == "Original novel.\n"
    assert not (tmp_path / "outputs/artifacts").exists()
    assert not (tmp_path / "outputs/state/story_state.json").exists()


class FakeReviewer:
    provider_name = "test"
    model = "fake"
    reasoning_effort = "low"

    def __init__(self, outputs=None):
        self.calls = 0
        self.outputs = outputs

    def complete(self, system, user):
        self.calls += 1
        if self.outputs is not None:
            value = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
            if isinstance(value, BaseException):
                raise value
            return value
        payload = json.loads(user)
        return json.dumps({"summary": "No suggestions.", "reviewed_rules": [r for m in payload["methods"] for r in m["rules"]], "findings": []})


def runtime_at(path, client=None, **policy):
    from project_identity import ensure_project_instance_id
    from narrative_methods.runtime import MethodReviews
    ensure_project_instance_id(path)
    client = client or FakeReviewer()
    runtime = MethodReviews(path, client_factory=lambda: client)
    runtime.prepare("run1", MethodPolicy(**policy), language="English", free_trial_end=3)
    return runtime, client


@pytest.mark.parametrize("change", [
    {"language": "Chinese"}, {"free_trial_end": None}, {"chapter": 4},
])
def test_methods_do_not_guess_language_or_free_trial_scope(change):
    packet = NarrativeMethods().compile(sample(**change), MethodPolicy())
    assert packet.status == "not_applicable" and not packet.review_user


def test_budget_overflow_is_incomplete_not_head_tail_review(tmp_path):
    runtime, client = runtime_at(tmp_path, review_max_chars=1000)
    result = runtime.review("run1", sample(text="a" * 1001))
    assert result["status"] == "incomplete"
    assert result["coverage"]["submitted_intervals"] == []
    assert client.calls == 0


@pytest.mark.parametrize("bad", ['{}', '[]', '{"summary":"a","summary":"b"}', 'not json'])
def test_invalid_output_has_at_most_one_structure_repair(tmp_path, bad):
    runtime, client = runtime_at(tmp_path, FakeReviewer([bad]))
    result = runtime.review("run1", sample())
    assert result["status"] == "invalid" and result["blocking"] is False
    assert result["usage"]["model_calls"] == client.calls == 2
    assert runtime.review("run1", sample()) == result
    assert client.calls == 2


@pytest.mark.parametrize("error, code", [
    (RuntimeError("401 Unauthorized: secret=SHOULD_NOT_PERSIST"), "reviewer_authentication"),
    (RuntimeError("Selected model is at capacity"), "reviewer_capacity"),
    (TimeoutError("timed out"), "reviewer_timeout"),
])
def test_transport_error_is_classified_without_literary_retries_or_secret_leak(tmp_path, error, code):
    runtime, client = runtime_at(tmp_path, FakeReviewer([error]))
    report = runtime.review("run1", sample())
    assert report["status"] == "unavailable" and report["error_code"] == code
    assert client.calls == 1
    assert all(b"SHOULD_NOT_PERSIST" not in p.read_bytes() for p in (tmp_path / "outputs/quality/methods").rglob('*.json'))


def test_interrupted_sent_request_is_not_blindly_resent(tmp_path):
    runtime, client = runtime_at(tmp_path, FakeReviewer([KeyboardInterrupt()]))
    with pytest.raises(KeyboardInterrupt):
        runtime.review("run1", sample())
    from narrative_methods.runtime import MethodReviews
    restored = MethodReviews(tmp_path, client_factory=lambda: client)
    report = restored.review("run1", sample())
    assert report["error_code"] == "interrupted_request_uncertain"
    assert report["usage"]["model_calls"] == 0
    assert report["usage"]["uncertain_model_calls"] == 1
    assert client.calls == 1


def test_resume_missing_lock_is_explicit_and_never_recreated(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    lock = tmp_path / "outputs/quality/methods/runs/run1/lock.json"
    lock.unlink()
    with pytest.raises(ValueError, match="missing"):
        runtime.prepare("run1", MethodPolicy(), language="English", free_trial_end=3, allow_create=False)
    assert not lock.exists()


def test_frozen_assets_work_after_catalog_changes_and_report_tampering_fails(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    runtime.methods.root = tmp_path / "removed-resource-directory"
    result = runtime.review("run1", sample())
    assert result["status"] == "valid"
    path = tmp_path / f"outputs/quality/methods/critiques/{result['report_id']}/report.json"
    wrapper = json.loads(path.read_text())
    wrapper["data"]["blocking"] = True
    path.write_text(json.dumps(wrapper))
    with pytest.raises(ValueError, match="checksum"):
        runtime.list_reports(1)


def test_resume_uses_frozen_client_configuration_not_new_global_judge(tmp_path, monkeypatch):
    runtime, client = runtime_at(tmp_path)
    from model_router import ModelRouter
    from narrative_methods.runtime import MethodReviews
    snapshots = []
    def restore(snapshot):
        snapshots.append(snapshot)
        return client
    monkeypatch.setattr(ModelRouter, "client_from_snapshot", staticmethod(restore))
    restored = MethodReviews(tmp_path)
    assert restored.review("run1", sample())["status"] == "valid"
    assert snapshots[0]["model"] == "fake"


def test_parallel_duplicate_review_calls_once_and_does_not_block_policy(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from narrative_methods.store import MethodStore
    entered, release = threading.Event(), threading.Event()
    class Slow(FakeReviewer):
        def complete(self, system, user):
            entered.set()
            assert release.wait(5)
            return super().complete(system, user)
    runtime, client = runtime_at(tmp_path, Slow())
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(runtime.review, "run1", sample())
        assert entered.wait(2)
        duplicate = pool.submit(runtime.review, "run1", sample())
        try:
            saved = pool.submit(MethodStore(tmp_path).set_policy, MethodPolicy(mode="off"), "").result(timeout=2)
            assert saved["data"]["policy"]["mode"] == "off"
        finally:
            release.set()
        assert first.result(timeout=3) == duplicate.result(timeout=3)
    assert client.calls == 1


def test_symlink_reports_cannot_read_or_write_outside_project(tmp_path):
    runtime, _ = runtime_at(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "outputs/quality/methods/critiques").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        runtime.review("run1", sample())
    assert list(outside.iterdir()) == []


def test_duplicate_quote_needs_valid_explicit_position_and_coverage_must_be_complete():
    methods = NarrativeMethods()
    request = sample(text="No. No.")
    packet = methods.compile(request, MethodPolicy())
    body = {"summary": "Check repetition.", "reviewed_rules": list(packet.rule_ids),
            "findings": [{"rule_id": "emotion_restatement", "severity": "major",
                          "explanation": "Check whether the second answer changes meaning.", "suggestion": "Retain if deliberate.",
                          "evidence": [{"start": 1, "end": 3, "quote": "No."}]}]}
    assert methods.validate(request, packet, json.dumps(body))["status"] == "invalid"
    body["findings"][0]["evidence"][0].update(start=4, end=7)
    assert methods.validate(request, packet, json.dumps(body))["status"] == "valid"
    body["reviewed_rules"].pop()
    assert methods.validate(request, packet, json.dumps(body))["status"] == "invalid"


def test_advisory_runtime_sidecars_do_not_change_public_zip_members_or_manifest(tmp_path):
    import zipfile
    from test_delivery_package import _payloads, _cover_set
    from core.delivery_package import build_delivery_package
    project = tmp_path / "book"
    _payloads(project)
    covers = _cover_set()
    before = build_delivery_package(project, cover_set=covers)
    manifest = before.manifest_path.read_bytes()
    with zipfile.ZipFile(before.archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    runtime, _ = runtime_at(project)
    assert runtime.review("run1", sample())["status"] == "valid"
    after = build_delivery_package(project, cover_set=covers)
    assert after.manifest_path.read_bytes() == manifest
    with zipfile.ZipFile(after.archive_path) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == members


def test_full_pipeline_advisory_failure_preserves_production_calls_and_final(tmp_path):
    from test_pipeline_runner import _commercial_factory, FakeOrchestrator
    from commercial_fixtures import commercial_story_fixture_variant
    from commercial_story import commercial_story_block
    from pipeline_models import RunSpec
    from pipeline_runner import PipelineRunner
    contract = commercial_story_fixture_variant()
    factory = _commercial_factory(contract)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test book\n\nA story.\n\n" + commercial_story_block(contract))
    results = []
    for mode in ("off", "advisory"):
        FakeOrchestrator.calls = []
        reviewer = FakeReviewer([RuntimeError("401 Unauthorized")])
        project = tmp_path / mode / "project"
        manifest = PipelineRunner(orchestrator_factory=factory, method_client_factory=lambda: reviewer).run(RunSpec(
            project_path=str(project), prompt_path=str(prompt), num_chapters=1, target_words=20,
            approval_policy="auto", output_formats=("markdown",), method_policy={"mode": mode},
        ))
        assert manifest.get("chapter.promote", 1), manifest.error
        assert manifest.get("chapter.promote", 1).status == "done", manifest.error
        results.append((list(FakeOrchestrator.calls), (project / "outputs/manuscript/chapter_001_final.md").read_bytes()))
        if mode == "off":
            assert reviewer.calls == 0 and not (project / "outputs/quality/methods").exists()
        else:
            assert reviewer.calls == 1
            from narrative_methods.runtime import MethodReviews
            assert MethodReviews(project).list_reports(1)[0]["status"] == "unavailable"
    assert results[0] == results[1]


def test_azure_snapshot_applies_endpoint_and_api_version_even_after_environment_changes(monkeypatch, use_frozen_router):
    import sys
    import types
    from llm_client import LLMClient
    from model_router import ModelRouter
    from narrative_methods.runtime import _provenance

    class FakeAzure:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.base_url = kwargs["azure_endpoint"].rstrip('/') + '/openai/'
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AzureOpenAI=FakeAzure))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://old.example")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "version-old")
    first = LLMClient(provider="azure", model="deployment-one")
    snapshot = _provenance(first)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://different.example")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "version-new")
    restored = ModelRouter.client_from_snapshot(snapshot)
    assert restored._backend.kwargs["azure_endpoint"] == "https://old.example"
    assert restored._backend.kwargs["api_version"] == "version-old"
    assert _provenance(restored) == snapshot


def test_missing_frozen_connection_key_does_not_fall_back_to_environment(monkeypatch, use_frozen_router):
    import provider_settings
    import model_router
    from model_router import ModelRouter
    connection = {"id": "original", "provider": "openai", "auth_type": "api_key", "capabilities": ["text_generation"], "base_url": "https://original.example/v1"}
    monkeypatch.setattr(provider_settings, "load_configuration", lambda: {})
    monkeypatch.setattr(provider_settings, "_connection", lambda config, id: connection)
    monkeypatch.setattr(provider_settings, "_connection_secret", lambda conn: "")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-environment-key")
    monkeypatch.setattr(model_router, "LLMClient", lambda **kwargs: pytest.fail("must not construct a fallback client"))
    with pytest.raises(provider_settings.ProviderSettingsError):
        ModelRouter.client_from_snapshot({"provider": "openai", "connection_id": "original",
                                          "configured_base_url": "https://original.example/v1"})


def test_corrupt_inherited_policy_does_not_prevent_baseline_run_manifest(tmp_path):
    from test_pipeline_runner import _factory
    from pipeline_models import RunSpec
    from pipeline_runner import PipelineRunner
    project = tmp_path / 'project'
    private = project / 'outputs/quality/methods'
    private.mkdir(parents=True)
    (private / 'policy.json').write_text('corrupt')
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('# A book\n\nLanguage: English\n\nA story.')
    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(project), prompt_path=str(prompt), num_chapters=1, target_words=20,
        dry_run=True, output_formats=('markdown',)))
    assert manifest.status == 'completed'
    assert manifest.spec.method_policy['mode'] == 'off'
    assert (project / 'outputs/runs' / manifest.run_id / 'run.json').exists()
    assert (private / 'policy.json').read_text() == 'corrupt'


def test_explicit_retry_keeps_old_failure_and_is_itself_idempotent(tmp_path):
    runtime, client = runtime_at(tmp_path, FakeReviewer([RuntimeError('401 unauthorized')]))
    failed = runtime.review('run1', sample())
    client.outputs = None
    retried = runtime.review('run1', sample(), retry_of=failed['report_id'])
    assert retried['status'] == 'valid'
    assert retried['retry_of'] == failed['report_id']
    assert len(runtime.list_reports(1)) == 2
    assert runtime.review('run1', sample(), retry_of=failed['report_id']) == retried
    assert client.calls == 2
    with pytest.raises(ValueError):
        runtime.review('run1', sample(revision_id='different'), retry_of=failed['report_id'])
