"""Story-route contracts: source observations and proposed fiction never share authority."""
from __future__ import annotations

import json
from typing import Annotated, Literal

from copy import deepcopy
from pydantic import Field, ValidationError, create_model

from .creative_types import StrictModel, digest

VERSION = "source-story-design.v1"
Text = Annotated[str, Field(min_length=1)]


class SourceRouteError(ValueError):
    """A recoverable design error. Complete inputs and replies stay in the store."""
    status = "failed"


class SourceRouteCapacityError(SourceRouteError):
    status = "needs_capacity"


class SourceRouteUnknownError(SourceRouteError):
    status = "pending_unknown"


class SourceRouteConflict(SourceRouteError):
    status = "conflict"


class SourceRouteProtocolError(SourceRouteError):
    pass


class SourceRouteIncompleteError(SourceRouteProtocolError):
    """An incomplete output is paged as whole artifacts, never JSON-spliced."""
    pass


class CausalArc(StrictModel):
    opening: Text
    development: Text
    turning_point: Text
    climax: Text
    ending: Text


class Emotion(StrictModel):
    expectation: Text
    scene: Text
    choice: Text
    cost: str = ""
    change: Text
    payoff: Text


class Highlight(StrictModel):
    scene: Text
    story_value: Text


class StoryPromise(StrictModel):
    question: Text
    development: Text
    payoff: Text


class TypeSuggestion(StrictModel):
    audience: Literal["female", "male"]
    genre_id: Text
    tag_ids: list[Text] = Field(min_length=1, max_length=2)
    reason: Text


class RouteContent(StrictModel):
    slot: Text
    title: Text
    reader_promise: Text
    continuity_basis: Text
    source_refs: list[Text] = Field(min_length=1)
    causal_arc: CausalArc
    character_relationship_arcs: list[Text] = Field(min_length=1)
    emotional_design: list[Emotion] = Field(min_length=1)
    highlights: list[Highlight] = Field(min_length=1)
    long_hooks_and_payoffs: list[StoryPromise] = Field(min_length=1)
    type_suggestion: TypeSuggestion
    proposed_assumptions: list[Text]
    tradeoffs: list[Text]
    difference_from_existing: Text
    changes_from_parent: str = ""


class Observation(StrictModel):
    local_id: Text
    kind: Literal["observed_event", "character_statement", "synopsis_promise",
                  "uncertain_inference", "open_thread", "editorial_interpretation"]
    statement: Text
    source_refs: list[Text] = Field(min_length=1)
    # IDs refer to existing observations, not claims that they were author-approved.
    supersedes: list[Text] = []


class UnderstandingContent(StrictModel):
    story_summary: Text
    narrative_endpoint: Text
    opening_analysis: Text
    open_threads: list[Text]
    observations: list[Observation]


class FollowupAction(StrictModel):
    operation: Literal["initial_routes", "revise_routes", "expand_routes", "revisit_source"]
    target_route_ids: list[Text] = []
    requested_count: int | None = Field(default=None, ge=1)
    author_quote: Text


class RouteResponse(StrictModel):
    routes: list[RouteContent]
    recommended_slot: str = ""
    recommendation_reason: str = ""
    followup_actions: list[FollowupAction] = []
    defer_current: bool = False


class InitialResponse(RouteResponse):
    understanding: UnderstandingContent


class IntegrationResponse(RouteResponse):
    """Empty routes means the exact supplied candidates need no change."""
    evidence_requests: list[Text] = []


class ReadingResponse(StrictModel):
    understanding: UnderstandingContent
    more: bool = False
    continuation: str = ""


ROUTE_PAGES = [
    ("RouteIdentityPage", ["slot", "title", "reader_promise", "continuity_basis", "source_refs", "type_suggestion", "proposed_assumptions", "tradeoffs", "difference_from_existing", "changes_from_parent"]),
    ("RouteCausalPage", ["slot", "causal_arc", "character_relationship_arcs"]),
    ("RouteEmotionPage", ["slot", "emotional_design", "highlights", "long_hooks_and_payoffs"]),
]


def route_page_contract(name: str, fields: list[str]):
    metadata = ({"followup_actions": (list[FollowupAction], Field(default_factory=list)),
                 "defer_current": (bool, False)} if name == "RouteIdentityPage" else {})
    return create_model(name, __base__=StrictModel,
        **{field: (RouteContent.model_fields[field].annotation, deepcopy(RouteContent.model_fields[field])) for field in fields},
        **metadata)


def parse_response(raw: str, contract) -> dict:
    """Decode a single intact JSON value; never splice or repair literary text."""
    if not isinstance(raw, str):
        raise SourceRouteProtocolError("模型没有返回文本；原始响应已保存。")
    body = raw.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise SourceRouteIncompleteError("模型回复尚不完整；原始响应已保存，按完整产物分页恢复。")
        body = "\n".join(lines[1:-1])
    if body.startswith("{") and not body.endswith("}"):
        raise SourceRouteIncompleteError("模型回复尚不完整；原始响应已保存，按完整产物分页恢复。")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise SourceRouteProtocolError("模型回复包含重复字段；原始响应已保存。")
            obj[key] = value
        return obj

    def invalid(_):
        raise SourceRouteProtocolError("模型回复包含非法数值；原始响应已保存。")
    try:
        value = json.loads(body, object_pairs_hook=unique, parse_constant=invalid)
        return contract.model_validate(value).model_dump()
    except (json.JSONDecodeError, ValidationError, RecursionError) as exc:
        if isinstance(exc, ValidationError):
            first = exc.errors(include_input=False, include_url=False)[0]
            detail = ".".join(map(str, first["loc"])) + ": " + first["type"]
        else:
            detail = "需要一个完整 JSON 对象"
        raise SourceRouteProtocolError(f"路线响应结构不完整（{detail}）；原始响应已保存。") from exc


def validate_routes(routes: list[dict], *, slots: list[str], source_ids: set[str],
                    catalog: list[dict], complete: bool = True) -> list[dict]:
    actual = [route["slot"] for route in routes]
    if len(set(actual)) != len(actual) or not set(actual) <= set(slots):
        raise SourceRouteProtocolError("路线输出位置重复或超出本轮授权范围。")
    if complete and set(actual) != set(slots):
        raise SourceRouteProtocolError("路线尚未全部返回；保留已收到内容，不能宣称完成。")
    kinds = {item["id"]: item.get("kind", item.get("type")) for item in catalog}
    records = {item["id"]: item for item in catalog}
    for route in routes:
        if not set(route["source_refs"]) <= source_ids:
            raise SourceRouteProtocolError("路线引用了未提供的来源证据。")
        suggestion = route["type_suggestion"]
        if kinds.get(suggestion["genre_id"]) != "genre" or any(kinds.get(i) != "tag" for i in suggestion["tag_ids"]):
            raise SourceRouteProtocolError("推荐题材不属于本轮有效目录。")
        if len(set(suggestion["tag_ids"])) != len(suggestion["tag_ids"]):
            raise SourceRouteProtocolError("推荐标签不能重复。")
        if any("audience_channels" in records[identifier]
               and suggestion["audience"] not in records[identifier]["audience_channels"]
               for identifier in [suggestion["genre_id"], *suggestion["tag_ids"]]):
            raise SourceRouteProtocolError("推荐题材尚未启用所推荐的读者方向。")
    return routes


def route_version(content: dict, route_id: str, *, parent_sha256: str | None = None) -> dict:
    value = {k: v for k, v in content.items() if k not in {"slot", "route_id", "sha256", "parent_sha256"}}
    value.update(route_id=route_id, parent_sha256=parent_sha256)
    return {**value, "sha256": digest(value)}


def content_view(route: dict, slot: str | None = None) -> dict:
    return {"slot": slot or route["route_id"], **{k: v for k, v in route.items()
            if k not in {"route_id", "sha256", "parent_sha256", "slot"}}}
