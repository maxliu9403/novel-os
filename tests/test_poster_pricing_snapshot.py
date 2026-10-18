"""A new poster job's configured estimate never reprices its history."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy

import pytest

from core.usage_costs import costs_payload, default_pricing, summarize
from core.usage_tracking import pricing_scope, read_records, record_usage, usage_scope


MODEL = "gpt-5.6-sol"
TOKENS = {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 1_000_000}


def prices(*, fx=7, input_rate=2, output_rate=10, image_rate=.25):
    return {"schema_version": 1, "usd_to_cny": fx,
            "exchange_rate_status": "configured" if fx is not None else "unset",
            "models": [{"provider": "codex", "model": MODEL,
                        "input_usd_per_million": input_rate, "cached_input_usd_per_million": input_rate,
                        "output_usd_per_million": output_rate, "image_usd_per_image": None},
                       {"provider": "codex", "model": "codex_auto", "input_usd_per_million": None,
                        "cached_input_usd_per_million": None, "output_usd_per_million": None,
                        "image_usd_per_image": image_rate}]}


def emit(identity, **changes):
    values = {"provider": "codex", "model": MODEL, "usage": TOKENS, "request_id": identity}
    record_usage(**{**values, **changes})


def test_job_snapshot_is_private_and_later_settings_do_not_reprice_history(tmp_path):
    original = prices()
    with usage_scope(tmp_path, "cover"), pricing_scope(original):
        original["usd_to_cny"] = 100
        original["models"][0]["output_usd_per_million"] = 999
        emit("poster-request")
    records = list(read_records(tmp_path).values())
    result = summarize(records, prices(fx=10, input_rate=50, output_rate=70))
    assert result["estimated_cost_usd"] == 12
    assert result["estimated_cost_cny"] == 84
    assert result["total_tokens"] == 2_000_000
    assert result["cost_complete"]
    assert result["pricing_snapshot_requests"] == 1
    assert records[0]["pricing_snapshot"]["usd_to_cny"] == 7


def test_first_snapshot_survives_failure_retry_observation_and_late_usage(tmp_path):
    with usage_scope(tmp_path, "cover"):
        with pricing_scope(prices(fx=6)):
            emit("long-request", status="started", usage={})
        with pricing_scope(prices(fx=20)):
            emit("long-request", status="failed", usage={"input_tokens": 1_000_000, "cached_input_tokens": 0})
        emit("long-request", status="failed", usage={"output_tokens": 1_000_000})
    record = read_records(tmp_path)["long-request"]
    assert record["status"] == "failed"
    assert record["pricing_snapshot"]["usd_to_cny"] == 6
    result = summarize([record], prices())
    assert result["estimated_cost_cny"] == 72
    assert result["request_count"] == 1
    assert result["known_usage_requests"] == 1


def test_mixed_job_exchange_rates_sum_each_cny_estimate_before_rounding(tmp_path):
    with usage_scope(tmp_path, "cover"):
        with pricing_scope(prices(fx=6)):
            emit("job-a")
        with pricing_scope(prices(fx=8)):
            emit("job-b")
        emit("old-job")
    result = costs_payload(list(read_records(tmp_path).values()), prices(fx=9))
    assert result["totals"]["estimated_cost_usd"] == 36
    assert result["totals"]["estimated_cost_cny"] == 72 + 96 + 108
    assert result["totals"]["pricing_snapshot_requests"] == 2
    assert result["totals"]["current_pricing_requests"] == 1
    assert result["totals"]["mixed_exchange_rates"]
    assert result["pricing"]["request_snapshots_present"]
    assert result["estimate"] is True


@pytest.mark.parametrize("missing", ["price", "exchange", "usage", "observed_model"])
def test_missing_original_price_or_usage_stays_unknown_when_current_settings_are_complete(tmp_path, missing):
    snapshot = prices()
    kwargs = {}
    if missing == "price":
        snapshot["models"] = []
    elif missing == "exchange":
        snapshot.update(usd_to_cny=None, exchange_rate_status="unset")
    elif missing == "usage":
        kwargs["usage"] = {}
    elif missing == "observed_model":
        kwargs["observed_model"] = "actual-unpriced-model"
    with usage_scope(tmp_path, "cover"), pricing_scope(snapshot):
        emit("missing", **kwargs)
    current = prices()
    current["models"].append({**current["models"][0], "model": "actual-unpriced-model"})
    result = summarize(list(read_records(tmp_path).values()), current)
    assert result["estimated_cost_cny"] is None
    assert not result["cost_complete"]
    if missing == "usage":
        assert result["unknown_usage_requests"] == 1 and result["known_usage_requests"] == 0


def test_image_failure_keeps_partial_estimate_without_inventing_tokens(tmp_path):
    with usage_scope(tmp_path, "cover"), pricing_scope(prices(image_rate=.5, fx=6)):
        emit("image", model="codex_auto", kind="image", usage={}, status="failed", image_count=1)
    result = summarize(list(read_records(tmp_path).values()), prices(image_rate=20))
    assert result["estimated_cost_cny"] == 3
    assert result["images"] == 1
    assert not result["cost_complete"]
    assert result["unknown_usage_requests"] == 1
    assert result["usage_coverage"]["total_tokens"] == 0


def test_no_opt_in_keeps_legacy_projection_even_when_late_update_has_a_snapshot(tmp_path):
    with usage_scope(tmp_path, "cover"):
        emit("legacy", usage={}, status="started")
        with pricing_scope(prices(fx=6)):
            emit("legacy")
    record = read_records(tmp_path)["legacy"]
    assert "pricing_snapshot" not in record
    assert summarize([record], prices(fx=8))["estimated_cost_cny"] == 96
    assert summarize([record], prices(fx=9))["estimated_cost_cny"] == 108


def test_snapshot_context_restores_and_flows_to_explicit_copied_worker_context(tmp_path):
    with usage_scope(tmp_path, "cover"):
        with pricing_scope(prices(fx=6)):
            with pricing_scope(prices(fx=8)):
                emit("nested")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(copy_context().run, emit, "worker").result()
        emit("unscoped")
    records = read_records(tmp_path)
    assert records["nested"]["pricing_snapshot"]["usd_to_cny"] == 8
    assert records["worker"]["pricing_snapshot"]["usd_to_cny"] == 6
    assert "pricing_snapshot" not in records["unscoped"]


def test_invalid_frozen_record_does_not_fall_back_to_current_rates():
    record = {"request_id": "broken", "stage": "cover", "provider": "codex", "model": MODEL,
              "usage": TOKENS, "status": "completed", "pricing_snapshot": {"schema_version": 500}}
    result = summarize([record], prices())
    assert result["estimated_cost_usd"] is None
    assert result["estimated_cost_cny"] is None
    assert not result["cost_complete"]


def test_invalid_job_snapshot_is_rejected_before_entering_model_work():
    bad = deepcopy(default_pricing())
    bad["usd_to_cny"] = float("nan")
    with pytest.raises(ValueError):
        with pricing_scope(bad):
            pytest.fail("No request is allowed with an invalid pricing snapshot")
