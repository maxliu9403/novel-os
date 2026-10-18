"""Read-only historical usage projection and configurable reference estimates."""
from __future__ import annotations

from collections import defaultdict
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile

from .usage_tracking import STAGES, POSTER_ACTIVITIES, normalize_usage, read_records


def pricing_path():
    return Path(os.environ.get("NOVEL_OS_PRICING_PATH") or
                Path(os.environ.get("NOVEL_OS_SETTINGS_PATH", "./studio_settings.json")).with_name("pricing.json"))


DEEPSEEK_REFERENCE_DATE = "2026-10-18"
DEEPSEEK_REFERENCE_MODELS = (
    {
        "provider": "deepseek",
        "model": "deepseek-reasoner",
        "input_usd_per_million": 0.55,
        "cached_input_usd_per_million": 0.14,
        "output_usd_per_million": 2.19,
        "image_usd_per_image": None,
        "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
        "reference_date": DEEPSEEK_REFERENCE_DATE,
        "note": "DeepSeek 官方参考价；缓存命中与未命中的口径不同，实际以账单为准。",
    },
    {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "input_usd_per_million": 0.27,
        "cached_input_usd_per_million": 0.07,
        "output_usd_per_million": 1.10,
        "image_usd_per_image": None,
        "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
        "reference_date": DEEPSEEK_REFERENCE_DATE,
        "note": "DeepSeek 官方参考价；缓存命中与未命中的口径不同，实际以账单为准。",
    },
)


def deepseek_reference_pricing():
    """Editable reference rows offered by the DeepSeek pricing preset."""
    return {"models": [dict(item) for item in DEEPSEEK_REFERENCE_MODELS]}


def default_pricing():
    models = [{"provider": provider, "model": "gpt-5.6-sol",
               "input_usd_per_million": 4.0, "cached_input_usd_per_million": .4,
               "output_usd_per_million": 20.0, "image_usd_per_image": None,
               "source_url": "https://developers.openai.com/api/docs/pricing",
               "reference_date": "2026-10-11",
               "note": "标准短上下文参考价；长上下文与服务档位请按实际调整，不代表订阅扣款。"}
              for provider in ("codex", "openai", "openai_compatible")]
    models.extend(dict(item) for item in DEEPSEEK_REFERENCE_MODELS)
    return {"schema_version": 1, "usd_to_cny": 7.0, "exchange_rate_status": "assumed",
            "models": models}


def validate_pricing(value):
    if not isinstance(value, dict) or value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported pricing schema")
    def number(value, name, positive=False):
        if value is None:
            return None
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
            raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
        if value > 1_000_000:
            raise ValueError(f"{name} is outside the supported range")
        return float(value)
    rate = number(value.get("usd_to_cny"), "usd_to_cny", True)
    status = value.get("exchange_rate_status", "configured") if rate is not None else "unset"
    if status not in {"assumed", "configured", "unset"} or (status == "unset" and rate is not None):
        raise ValueError("Invalid exchange rate status")
    models = value.get("models", [])
    if not isinstance(models, list) or len(models) > 200:
        raise ValueError("models must contain at most 200 price entries")
    result, seen = [], set()
    for item in models:
        if not isinstance(item, dict):
            raise ValueError("Each model price must be an object")
        entry = {}
        for key in ("provider", "model"):
            text = item.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > 200 or any(ord(c) < 32 for c in text):
                raise ValueError(f"Invalid pricing {key}")
            entry[key] = text.strip()
        identity = entry["provider"], entry["model"]
        if identity in seen:
            raise ValueError("Duplicate provider/model pricing")
        seen.add(identity)
        for key in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million", "image_usd_per_image"):
            entry[key] = number(item.get(key), key)
        for key in ("source_url", "reference_date", "note"):
            if key in item:
                if not isinstance(item[key], str) or len(item[key]) > 500:
                    raise ValueError(f"Invalid pricing {key}")
                entry[key] = item[key]
        result.append(entry)
    return {"schema_version": 1, "usd_to_cny": rate, "exchange_rate_status": status, "models": result}


def load_pricing():
    path = pricing_path()
    return validate_pricing(json.loads(path.read_text())) if path.is_file() else default_pricing()


def save_pricing(value):
    value = validate_pricing(value)
    path = pricing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd, temp = tempfile.mkstemp(prefix=".pricing-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return value


def _owned_file(path, owner):
    """Check the entire resolved path, not just a possibly innocent leaf."""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(Path(owner).resolve()) and path.is_file()
    except (OSError, ValueError):
        return False


def _json(path, owner):
    if not _owned_file(path, owner):
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def diagnostic_records(owner):
    """Repeated CLI heartbeat counters are snapshots of one request, not spend."""
    records = {}
    turns = {}
    for path in sorted((Path(owner) / "diagnostics").glob("*.jsonl")):
        if not _owned_file(path, owner) or path.stat().st_size > 6 * 1024 * 1024:
            continue
        for line in path.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            identity = row.get("request_id")
            if not identity or not isinstance(identity, str):
                continue
            record = records.setdefault(identity, {
                "request_id": identity, "stage": "brainstorming", "provider": "", "model": "",
                "kind": "text", "status": "unknown", "usage": {}, "historical": True,
            })
            state = turns.setdefault(identity, {"index": 0, "last": None, "values": {}})
            if row.get("last_cli_event") == "turn.started" and state["last"] != "turn.started":
                state["index"] += 1
            if row.get("last_cli_event"):
                state["last"] = row["last_cli_event"]
            for name in ("provider", "model"):
                if row.get(name):
                    record[name] = row[name]
            # Only CLI/provider usage envelopes. OTEL counters lack a stable
            # upstream response id and must not be added to the same turn.
            if isinstance(row.get("usage"), dict):
                usage = normalize_usage(row["usage"], record["provider"])
                if usage:
                    if row.get("usage_scope") == "request_total":
                        record["usage"] = usage
                        record["usage_incomplete_fields"] = row.get("usage_incomplete_fields", [])
                    elif row.get("last_cli_event") in {None, "turn.completed"}:
                        state["values"][state["index"]] = usage
                        values = list(state["values"].values())
                        keys = set.union(*(set(item) for item in values))
                        record["usage"] = {key: sum(item.get(key, 0) for item in values) for key in keys}
                        record["usage_incomplete_fields"] = [key for key in keys if not all(key in item for item in values)]
            if row.get("event") in {"request.completed", "request.failed", "request.timeout", "request.cancelled"}:
                record["status"] = row["event"].split(".")[-1]
    return records


def owner_records(owner, *, project=False):
    owner = Path(owner).resolve()
    records = diagnostic_records(owner) if not project else {}
    live = read_records(owner)
    records.update(live)
    if project:
        # Old analysis attempts are verifiable calls, but legacy code discarded
        # their token usage. Cached/copied attempts share ids and deduplicate.
        cutoff = min((r.get("first_observed_at", r.get("recorded_at", "")) for r in live.values()), default="9999")
        for path in (owner / "outputs/covers/analyses").glob("*/analysis.json"):
            payload = _json(path, owner)
            config = payload.get("configuration", {})
            for attempt in payload.get("attempts", []):
                identity = attempt.get("attempt_id")
                if not identity:
                    continue
                records.setdefault(identity, {"request_id": identity, "stage": "cover", "kind": "text",
                    "provider": config.get("provider", ""), "model": config.get("model", ""),
                    "status": attempt.get("status", "unknown"), "usage": {}, "historical": True})
        for path in (owner / "outputs/covers/sets").glob("cover-*.json"):
            payload = _json(path, owner)
            if any("_cover_import" in concept.get("scene_plan", {}) for concept in payload.get("concepts", [])):
                continue
            for candidate in payload.get("candidates", []):
                for attempt in candidate.get("attempt_history", []):
                    identity = attempt.get("attempt_id")
                    if (not identity or attempt.get("operation") == "typography_recomposition"
                            or attempt.get("created_at", "9999") >= cutoff):
                        continue
                    records.setdefault(identity, {"request_id": identity, "stage": "cover", "kind": "image",
                        "provider": candidate.get("provider", ""), "model": attempt.get("model", ""),
                        "status": "completed" if attempt.get("image_sha256") else "failed",
                        "image_count": 1 if attempt.get("image_sha256") else None, "usage": {}, "historical": True})
    return list(records.values())


def _estimate(record, prices):
    model = record.get("observed_model") or record.get("model")
    price = prices.get((record.get("provider"), model))
    if price is None:
        return None, False
    usage = record.get("usage", {})
    if record.get("kind") == "image":
        # Image usage has different modality rates: never apply text token
        # prices to it. Per-image estimate is an explicit separate price basis.
        count = record.get("image_count")
        if count and price.get("image_usd_per_image") is not None:
            return count * price["image_usd_per_image"], record.get("status") == "completed"
        return None, False
    incomplete = set(record.get("usage_incomplete_fields", ()))
    amounts, complete = [], not bool(incomplete)
    count, rate = usage.get("output_tokens"), price.get("output_usd_per_million")
    if count is not None and rate is not None:
        amounts.append(count * rate / 1_000_000)
    else:
        complete = False
    count, cached, rate = usage.get("input_tokens"), usage.get("cached_input_tokens"), price.get("input_usd_per_million")
    cache_rate = price.get("cached_input_usd_per_million")
    if count is not None and rate is not None:
        if cached is not None and not incomplete.intersection({"input_tokens", "cached_input_tokens"}):
            cache_write = usage.get("cache_creation_input_tokens", 0)
            amounts.append(max(0, count - cached - cache_write) * rate / 1_000_000)
            if not cached or cache_rate is not None:
                amounts.append(cached * (cache_rate or 0) / 1_000_000)
            else:
                complete = False
        elif cache_rate == rate and not usage.get("cache_creation_input_tokens"):
            amounts.append(count * rate / 1_000_000)
        else:
            complete = False
            if cached is not None and cache_rate is not None:
                amounts.append(cached * cache_rate / 1_000_000)
    else:
        complete = False
        if cached is not None and cache_rate is not None:
            amounts.append(cached * cache_rate / 1_000_000)
    if usage.get("cache_creation_input_tokens"):
        complete = False  # No cache-write price in v1; do not claim full billing.
    return (sum(amounts) if amounts else None), complete


def summarize(records, pricing):
    totals = {k: 0 for k in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens", "images",
                            "request_count", "known_usage_requests", "unknown_usage_requests")}
    prices = {(p["provider"], p["model"]): p for p in pricing["models"]}
    coverage = {k: 0 for k in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens")}
    amounts, cny_amounts, complete = [], [], True
    snapshot_count = 0
    exchange_rates = set()
    for record in records:
        usage = record.get("usage", {})
        totals["request_count"] += 1
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens"):
            totals[key] += usage.get(key, 0)
            coverage[key] += key in usage
        known = ("input_tokens" in usage and "output_tokens" in usage
                 and not {"input_tokens", "output_tokens"}.intersection(record.get("usage_incomplete_fields", ())))
        totals["known_usage_requests" if known else "unknown_usage_requests"] += 1
        totals["images"] += record.get("image_count") or 0
        effective_prices = prices
        fx = pricing.get("usd_to_cny")
        if "pricing_snapshot" in record:
            snapshot_count += 1
            try:
                snapshot = validate_pricing(record["pricing_snapshot"])
            except (TypeError, ValueError):
                # A damaged/unknown frozen record cannot be silently repriced.
                snapshot = {"models": [], "usd_to_cny": None}
            effective_prices = {(p["provider"], p["model"]): p for p in snapshot["models"]}
            fx = snapshot.get("usd_to_cny")
        exchange_rates.add(fx)
        amount, exact = _estimate(record, effective_prices)
        if amount is not None:
            amounts.append(amount)
            if fx is not None:
                cny_amounts.append(amount * fx)
        complete = complete and exact and fx is not None
    usd = sum(amounts) if amounts else None
    cny = sum(cny_amounts) if cny_amounts else None
    totals.update(usage_coverage=coverage, estimated_cost_usd=round(usd, 8) if usd is not None else None,
                  estimated_cost_cny=round(cny, 6) if cny is not None else None,
                  cost_complete=bool(records) and complete,
                  pricing_snapshot_requests=snapshot_count, current_pricing_requests=len(records) - snapshot_count,
                  mixed_exchange_rates=len(exchange_rates) > 1)
    return totals


def costs_payload(records, pricing, **identity):
    groups = defaultdict(list)
    for record in records:
        groups[(record["stage"], record.get("provider", ""), record.get("observed_model") or record.get("model", ""))].append(record)
    return {"schema_version": 1, **identity, "currency": "CNY", "estimate": True,
        "pricing": {**{k: pricing[k] for k in ("usd_to_cny", "exchange_rate_status")},
                    "request_snapshots_present": any("pricing_snapshot" in r for r in records)},
        "totals": summarize(records, pricing),
        "stages": [{"stage": stage, **summarize([r for r in records if r["stage"] == stage], pricing)} for stage in STAGES],
        "cover_activities": [{"activity": activity, **summarize([r for r in records
            if r["stage"] == "cover" and r.get("activity", "legacy") == activity], pricing)}
            for activity in (*POSTER_ACTIVITIES, "legacy")],
        "models": [{"stage": s, "provider": p, "model": m, **summarize(rows, pricing)} for (s, p, m), rows in sorted(groups.items())],
        "notes": ["仅统计服务商实际返回的 token；历史未保存的用量为未知，不按字数推算。",
                  "费用为可配置单价与汇率的参考估算，不是实际账单或订阅扣款；缺少用量或报价时仅显示可统计小计。",
                  "缓存 token 已包含在输入中，推理 token 已包含在输出中，不重复相加；图片仅按单独配置的每张参考价估算。",
                  "新版封面任务冻结发起时的单价与汇率；后续修改不会重算这些历史费用。未保存价格快照的旧请求仍按当前配置估算。",
                  "历史未保存请求记录的调用、CLI 内部无法辨认的上游重试可能未覆盖；默认汇率是可修改换算值，非实时汇率。"]}
