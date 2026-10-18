"""Unspecified story locations are valid facts, not fabricated cover settings."""
from __future__ import annotations

import pytest

import core.cover_analysis as analysis_module
from core.cover_analysis import CoverAnalysisError, load_analysis
from tests.test_cover_analysis_snapshot import ModelFixture, analyzer, source


def _location_model(value="", *, omit=False):
    model = ModelFixture()

    def mutate(request, result):
        if request["phase"] == "global":
            event = result["events"][0]
            if omit:
                event.pop("location")
            else:
                event["location"] = value
        return result

    model.mutate = mutate
    return model


def test_unspecified_event_location_survives_analysis_review_and_reload(source):
    context, snapshot, _ = source
    model = _location_model()
    result = analyzer(model).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    assert result.events[0]["location"] == ""
    assert len([c for c in model.calls if c["phase"] == "global"]) == 1
    assert any(c.get("mode") == "source_segment" for c in model.calls)
    assert any(c.get("mode") == "global_cross_segment" for c in model.calls)
    loaded = load_analysis(context, result.analysis_id)
    assert loaded.events == result.events
    assert loaded.analysis_sha256 == result.analysis_sha256


@pytest.mark.parametrize("value,omit", [(None, False), ([], False), ({}, False), (42, False), ("", True)])
def test_event_location_requires_a_string_even_if_unspecified(source, value, omit):
    context, snapshot, _ = source
    with pytest.raises(CoverAnalysisError, match="Event requires location"):
        analyzer(_location_model(value, omit=omit)).analyze(context, snapshot)


def test_legacy_location_failure_reuses_exact_response_and_keeps_independent_review(source, monkeypatch):
    context, snapshot, _ = source
    model = _location_model()
    current_validator = analysis_module._validate_global

    def old_validator(raw, *args, **kwargs):
        if any(not event.get("location", "").strip() for event in raw["events"]):
            raise ValueError("Event requires location")
        return current_validator(raw, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(analysis_module, "_validate_global", old_validator)
        with pytest.raises(CoverAnalysisError, match="Event requires location") as error:
            analyzer(model).analyze(context, snapshot)
    partial = load_analysis(context, error.value.analysis_id)
    directory = context.cover_root / "analyses" / partial.analysis_id / "attempts"
    original_attempts = {p.name: p.read_bytes() for p in directory.glob("*.json")}
    model.calls.clear()
    result = analyzer(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert result.events[0]["location"] == ""
    assert model.calls and all(c["phase"] == "verify" for c in model.calls)
    assert len(result.response_revalidations) == 1
    assert result.attempts[:len(partial.attempts)] == partial.attempts
    assert all((directory / name).read_bytes() == body for name, body in original_attempts.items())
