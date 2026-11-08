"""Malformed model structures must reach bounded repair, not escape as KeyError."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_delivery import DeliveryError, parse_stage
from core.narrative_format import infer_narrative_format
from core.novel_classification import infer_classification
from test_story_first_engine import design  # noqa: F401
from test_workshop_contract_repair import stub_model
from test_workshop_api import client, create, finish  # noqa: F401
from test_workshop_contract_repair import submit


def complex_design(design, mode="multi_volume"):
    from api.workshop_design import new_design
    session, story, chapters = deepcopy(design)
    session["input"]["mode"] = story["narrative_mode"] = chapters["narrative_mode"] = mode
    session["creative_design"] = new_design(session["input"], guided=False)
    structure = infer_narrative_format(
        infer_classification(genre="Romance"), chapters=2, target_words=1600,
        mode=mode, volume_count=2, explicit_length=True,
        **({"series_id": "test-series", "series_title": "Test Series",
            "series_book_number": 1, "planned_books": 2} if mode == "series_installment" else {}),
    ).to_dict()
    structure.pop("format_id")
    story["narrative_format"] = structure
    return session, story, chapters


@pytest.mark.parametrize("mode", ["multi_volume", "series_installment"])
@pytest.mark.parametrize("mutate,path", [
    (lambda d: d.pop("target_words"), "narrative_format.target_words"),
    (lambda d: d["volumes"][0].pop("structural_role"), "narrative_format.volumes[0].structural_role"),
    (lambda d: d["long_form_suitability"].pop("score"), "narrative_format.long_form_suitability.score"),
    (lambda d: d.update(volumes=None), "narrative_format.volumes"),
    (lambda d: d["volumes"].__setitem__(0, "not an object"), "narrative_format.volumes[0]"),
    (lambda d: d["volumes"][0].update(structural_role=None), "narrative_format.volumes[0].structural_role"),
    (lambda d: d["long_form_suitability"].update(dimensions=[]), "narrative_format.long_form_suitability.dimensions"),
])
def test_nested_missing_or_wrong_type_is_repairable(design, mode, mutate, path):
    _, story, _ = complex_design(design, mode)
    mutate(story["narrative_format"])
    with pytest.raises(DeliveryError) as caught:
        parse_stage(json.dumps(story), "story_design", mode, story_first=True)
    assert path in str(caught.value)
    assert "检查头脑风暴模型配置" not in workshop._failure(caught.value, timeout_seconds=900, has_prompt=False)


def test_nested_errors_are_reported_together_without_echoing_values(design):
    _, story, _ = complex_design(design)
    structure = story["narrative_format"]
    structure.pop("target_words")
    structure["volumes"][0].pop("structural_role")
    structure["long_form_suitability"]["score"] = "private model text"
    with pytest.raises(DeliveryError) as caught:
        parse_stage(json.dumps(story), "story_design", "multi_volume", story_first=True)
    assert all(key in str(caught.value) for key in ("target_words", "structural_role", "score"))
    assert "private model text" not in str(caught.value)


def test_bounded_repair_preserves_story_and_advances_to_chapters(design, monkeypatch):
    session, story, chapters = complex_design(design)
    broken = deepcopy(story)
    broken["narrative_format"]["volumes"][0].pop("structural_role")
    replies = [broken, {'narrative_format': story['narrative_format']}, chapters]
    writes = []
    session["_save_delivery_checkpoint"] = lambda record: writes.append(deepcopy(record))
    calls = stub_model(monkeypatch, lambda n, *_: json.dumps(replies[n - 1]))
    result, _ = workshop._complete(session, "Story design")
    assert len(calls) == 3
    repair = json.loads(calls[1][1])
    assert "structural_role" in repair["validation_error"]
    assert repair['operation'] == 'repair_narrative_format'
    assert repair['candidate_format'] == broken['narrative_format']
    assert repair['story_design'] == broken['story_design']
    assert "previous_model_response" not in json.loads(calls[2][1])
    assert 'ENGINE NARRATIVE FORMAT REFERENCE' not in calls[2][0]
    assert writes[-1]["completed"]["story_design"]["content"]["story_design"] == story["story_design"]
    assert "[NARRATIVE_FORMAT_JSON]" in result["prompt_md"]


def test_persistent_malformed_reply_stops_at_existing_global_limit(design, monkeypatch):
    session, story, _ = complex_design(design)
    story["narrative_format"]["volumes"][0].pop("structural_role")
    calls = stub_model(monkeypatch, lambda n, *_: json.dumps(story if n == 1 else {'narrative_format': story['narrative_format']}))
    writes = []
    session["_save_delivery_checkpoint"] = lambda record: writes.append(deepcopy(record))
    with pytest.raises(DeliveryError, match="structural_role"):
        workshop._complete(session, "Story design")
    assert len(calls) == 3
    assert all(not record["completed"] for record in writes)


def test_failed_stage_replies_are_private_durable_and_retry_keeps_conversation(client, design, monkeypatch, tmp_path):
    import hashlib
    import stat
    _, story, chapters = complex_design(design)
    broken = deepcopy(story)
    broken["narrative_format"]["volumes"][0].pop("structural_role")
    raw = "  " + json.dumps(broken, ensure_ascii=False) + "\n"
    repair_raw = "  " + json.dumps({'narrative_format': broken['narrative_format']}, ensure_ascii=False) + "\n"
    calls = stub_model(monkeypatch, lambda n, *_: raw if n == 1 else repair_raw)
    data = create(client, mode="multi_volume", chapters=2, words_per_chapter=800, language="English")
    submit(client, data, "保留我们的故事，生成完整骨架")
    failed = finish(client, data["id"])
    assert failed["status"] == "error" and len(calls) == 3
    assert "narrative_format.volumes[0].structural_role" in failed["last_error"]
    assert "检查头脑风暴模型配置" not in failed["last_error"]
    root = tmp_path / "workshop_v2" / data["id"]
    paths = sorted((root / "stage-responses").glob("*.json"))
    assert len(paths) == 3
    for path in paths:
        record = json.loads(path.read_text())
        expected_raw = raw if record['attempt'] == 1 else repair_raw
        assert record["raw"] == expected_raw
        assert record["response_sha256"] == hashlib.sha256(expected_raw.encode()).hexdigest()
        assert record["response_bytes"] == len(expected_raw.encode())
        assert record['part'] == (None if record['attempt'] == 1 else 'narrative_format')
        assert record["validation"]["status"] == "invalid"
        assert "structural_role" in record["validation"]["detail"]
        assert record["job_id"] == failed["job_id"]
        assert record["system_sha256"] == hashlib.sha256(calls[record["attempt"] - 1][0].encode()).hexdigest()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
    assert "response_sha256" not in json.dumps(failed)
    assert "An ally asks for the key" not in "".join(p.read_text() for p in (root / "diagnostics").glob("*.jsonl"))
    before = {path: path.read_bytes() for path in paths}
    retries = stub_model(monkeypatch, lambda n, *_: json.dumps(story if n == 1 else chapters))
    submit(client, failed, "继续生成完整骨架")
    ready = finish(client, data["id"])
    assert ready["status"] == "ready", ready["last_error"]
    assert len(retries) == 2
    assert ready["messages"][:len(failed["messages"])] == failed["messages"]
    assert all(path.read_bytes() == content for path, content in before.items())


def test_completed_global_design_survives_timeout_and_resume(client, design, monkeypatch, tmp_path):
    _, story, chapters = complex_design(design)
    def respond(n, *_):
        if n == 2:
            raise TimeoutError("fake timeout")
        return json.dumps(story)
    stub_model(monkeypatch, respond)
    data = create(client, mode="multi_volume", chapters=2, words_per_chapter=800, language="English")
    submit(client, data, "生成完整骨架")
    failed = finish(client, data["id"])
    assert failed["delivery_progress"]["completed_stages"] == ["story_design"]
    calls = stub_model(monkeypatch, lambda *_: json.dumps(chapters))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": failed["revision"]})
    assert response.status_code == 202, response.text
    ready = finish(client, data["id"])
    assert ready["status"] == "ready", ready["last_error"]
    assert len(calls) == 1
    assert json.loads(calls[0][1])["delivery_stage"] == "chapter_plan"


def test_raw_response_is_saved_before_unexpected_parser_failure(client, design, monkeypatch, tmp_path):
    _, story, _ = complex_design(design)
    stub_model(monkeypatch, lambda *_: json.dumps(story))
    def fail(*_, **kwargs):
        raise KeyError("PRIVATE_KEY_NAME")
    monkeypatch.setattr("api.workshop_delivery.parse_stage", fail)
    data = create(client, mode="multi_volume", chapters=2, words_per_chapter=800)
    submit(client, data, "生成完整骨架")
    failed = finish(client, data["id"])
    assert "内部错误" in failed["last_error"]
    assert "PRIVATE_KEY_NAME" not in json.dumps(failed)
    root = tmp_path / "workshop_v2" / data["id"] / "stage-responses"
    paths = list(root.glob("*.json"))
    assert len(paths) == 1
    receipt = json.loads(paths[0].read_text())
    assert json.loads(receipt["raw"]) == story
    assert receipt["validation"]["error_type"] == "KeyError"
    assert receipt["validation"]["frames"][-1]["function"] == "fail"


def test_storage_failure_does_not_buy_another_model_request(design, monkeypatch):
    from api.workshop_responses import ResponseStorageError
    session, story, _ = complex_design(design)
    calls = stub_model(monkeypatch, lambda *_: json.dumps(story))
    def fail(_):
        raise OSError("disk full")
    session["_save_delivery_response"] = fail
    with pytest.raises(ResponseStorageError, match="未能安全保存"):
        workshop._complete(session, "Story design")
    assert len(calls) == 1


@pytest.mark.parametrize("change,match", [
    (lambda d: d["volumes"][1].update(chapter_start=1), "contiguous"),
    (lambda d: d["volumes"][1].update(chapter_end=3), "every chapter"),
    (lambda d: d.update(format_id="invented"), "format_id"),
])
def test_shape_validation_does_not_bypass_domain_invariants(design, change, match):
    _, story, _ = complex_design(design)
    change(story["narrative_format"])
    with pytest.raises(DeliveryError, match=match):
        parse_stage(json.dumps(story), "story_design", "multi_volume", story_first=True)
