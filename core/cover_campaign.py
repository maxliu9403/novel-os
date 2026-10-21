"""Versioned, evidence-bound scene and film-campaign composition contract.

The contract is kept separate from the historical direction serializer.  Merely
reading an old direction must not add new fields or change its approval hash.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

CAMPAIGN_PROFILE = "cover-profiles.v10"
COMMERCIAL_PROFILE = "cover-profiles.v11"
CAMPAIGN_SCHEMA_VERSION = 1


def uses_campaign(profile: str) -> bool:
    return profile.casefold() in {CAMPAIGN_PROFILE, COMMERCIAL_PROFILE}


def uses_commercial_expression(profile: str) -> bool:
    return profile.casefold() == COMMERCIAL_PROFILE


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campaign.{key} must be non-empty text")
    return value.strip()


def _texts(data: Mapping[str, Any], key: str, *, empty: bool = False) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, (list, tuple)) or (not value and not empty):
        raise ValueError(f"campaign.{key} must be a {'possibly empty ' if empty else 'non-empty '}array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"campaign.{key} must contain non-empty strings")
    return tuple(item.strip() for item in value)


def _objects(data: Mapping[str, Any], key: str, *, empty: bool = False) -> tuple[dict[str, Any], ...]:
    value = data.get(key)
    if not isinstance(value, (list, tuple)) or (not value and not empty):
        raise ValueError(f"campaign.{key} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"campaign.{key} must contain objects")
    return tuple(dict(item) for item in value)


def _region(value: Any, name: str) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} requires [x,y,width,height]")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"{name} must use numeric normalized coordinates")
    x, y, width, height = value
    # Compare endpoint sums: subtraction (e.g. 1 - .56) can round below .44.
    # The absolute tolerance covers binary roundoff, far below a rendered pixel.
    if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0
            and x + width <= 1 + 1e-12 and y + height <= 1 + 1e-12):
        raise ValueError(f"{name} must fit inside the normalized canvas")


@dataclass(frozen=True)
class CoverCampaignPlan:
    composition_mode: str
    cover_epoch: str
    event_ids: tuple[str, ...]
    interaction_edges: tuple[dict[str, Any], ...]
    hook: dict[str, Any]
    layers: tuple[dict[str, Any], ...]
    layer_relationships: tuple[dict[str, Any], ...]
    read_order: tuple[str, ...]
    title_render_mode: str
    title_layout: dict[str, Any]
    schema_version: int = CAMPAIGN_SCHEMA_VERSION
    # Absent on historical campaigns, including their serialized approval bytes.
    expression_mode: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverCampaignPlan":
        if not isinstance(data, Mapping) or data.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("campaign.schema_version must be 1")
        expression = data.get("expression_mode", "")
        if "expression_mode" in data and expression not in ("literal_scene", "editorial"):
            raise ValueError("campaign.expression_mode must be literal_scene or editorial")
        editorial = expression == "editorial"
        mode = _text(data, "composition_mode")
        if mode not in {"scene", "campaign_composite"}:
            raise ValueError("campaign.composition_mode must be scene or campaign_composite")
        title_mode = _text(data, "title_render_mode")
        if title_mode not in {"composed", "native"}:
            raise ValueError("campaign.title_render_mode must be composed or native")
        hook = data.get("hook")
        layout = data.get("title_layout")
        if not isinstance(hook, Mapping) or not isinstance(layout, Mapping):
            raise ValueError("campaign.hook and title_layout must be objects")
        for field in ("visible_fact", "unexpected_contradiction", "visual_trigger", "open_question",
                      "withheld_answer", "reader_promise", "spoiler_ceiling"):
            _text(hook, field)
        _texts(hook, "evidence_refs")
        _text(layout, "title")
        _region(layout.get("region"), "title_layout.region")
        edges = _objects(data, "interaction_edges", empty=editorial)
        for edge in edges:
            for field in ("actor_id", "action", "target_id", "visible_reaction", "layer_id"):
                _text(edge, field)
            _texts(edge, "affected_character_ids")
            _texts(edge, "evidence_refs")
        layers = _objects(data, "layers")
        for layer in layers:
            for field in ("layer_id", "type", "epoch", "location", "function"):
                _text(layer, field)
            if layer["type"] not in {"story_moment", "character_portrait", "story_object"}:
                raise ValueError("campaign layer type is invalid")
            _texts(layer, "cast", empty=True)
            _texts(layer, "evidence_refs")
            _region(layer.get("region"), "campaign.layer.region")
            weight = layer.get("visual_weight")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= 1:
                raise ValueError("campaign.layer.visual_weight must be in (0,1]")
            if layer["type"] == "story_moment":
                _text(layer, "event_id")
        links = _objects(data, "layer_relationships", empty=True)
        for link in links:
            for field in ("from_layer", "to_layer", "relationship", "temporal_relationship", "truth_boundary"):
                _text(link, field)
            if link["relationship"] not in {"conflict", "alliance", "barrier", "cause", "visual_echo"}:
                raise ValueError("campaign.layer relationship is invalid")
            if link["temporal_relationship"] not in {"same_moment", "cross_time", "timeless_portrait"}:
                raise ValueError("campaign.temporal_relationship is invalid")
            _texts(link, "evidence_refs", empty=link["relationship"] == "visual_echo")
        return cls(mode, _text(data, "cover_epoch"), _texts(data, "event_ids", empty=editorial), edges,
                   dict(hook), layers, links, _texts(data, "read_order"), title_mode, dict(layout),
                   expression_mode=expression)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "composition_mode": self.composition_mode,
            "cover_epoch": self.cover_epoch, "event_ids": list(self.event_ids),
            "interaction_edges": list(self.interaction_edges), "hook": dict(self.hook),
            "layers": list(self.layers), "layer_relationships": list(self.layer_relationships),
            "read_order": list(self.read_order), "title_render_mode": self.title_render_mode,
            "title_layout": dict(self.title_layout),
            **({"expression_mode": self.expression_mode} if self.expression_mode else {}),
        }


@dataclass(frozen=True)
class CampaignFinding:
    code: str
    message: str
    evidence: str = ""
    severity: str = "blocker"


_GENERIC_QUESTION = re.compile(
    r"^(?:what (?:will )?happens? next|what now|how does (?:she|he) feel|"
    r"will (?:she|he) be happy|接下来会怎样|她会怎样|他会怎样|她很伤心[，,]?会怎样)[?？.。!！ ]*$", re.I)
_STATIC_ACTION = re.compile(
    r"^(?:(?:stands?|poses?|looks?) (?:together|at (?:the )?camera)|"
    r"sad portrait|group portrait|站在一起|看向镜头|一起站着|合影|悲伤肖像)[.。 ]*$", re.I)


def validate_campaign(
    campaign: CoverCampaignPlan, *, cast: Sequence[str], title: str,
    allowed_refs: set[str], story_analysis: Mapping[str, Any] | None = None,
    approved_claim_paths: set[str] | None = None,
    defer_evidence_review: bool = False,
) -> tuple[CampaignFinding, ...]:
    """Check reference integrity and concrete counterexamples, not pretend to prove semantics."""
    findings: list[CampaignFinding] = []
    def fail(code: str, message: str, evidence: str = "") -> None:
        findings.append(CampaignFinding(code, message, evidence))
    def refs(item: Mapping[str, Any], label: str) -> None:
        for ref in item.get("evidence_refs", ()):
            if ref not in allowed_refs:
                fail("campaign_unknown_evidence", "Campaign evidence must resolve in frozen sources", f"{label}: {ref}")
    layer_map = {layer["layer_id"]: layer for layer in campaign.layers}
    if len(layer_map) != len(campaign.layers):
        fail("campaign_duplicate_layer", "Layer IDs must be unique")
    if set(campaign.read_order) != set(layer_map) or len(campaign.read_order) != len(layer_map):
        fail("campaign_read_order", "Read order must name every layer exactly once")
    if campaign.title_layout["title"] != title:
        fail("campaign_title_mismatch", "Title layout must preserve the exact approved title")
    editorial = campaign.expression_mode == "editorial"
    if campaign.composition_mode == "scene" and not editorial:
        if len(campaign.layers) != 1 or campaign.layers[0]["type"] != "story_moment":
            fail("campaign_scene_space", "Scene requires one story moment layer")
        if len(campaign.event_ids) != 1 or any(layer["epoch"] != campaign.cover_epoch for layer in campaign.layers):
            fail("campaign_scene_time", "Scene must depict one event at one declared epoch", json.dumps({
                "path": "campaign.cover_epoch / campaign.event_ids", "cover_epoch": campaign.cover_epoch,
                "event_ids": list(campaign.event_ids),
                "layers": [{"path": f"campaign.layers[{index}].epoch", "layer_id": layer["layer_id"],
                            "epoch": layer["epoch"]} for index, layer in enumerate(campaign.layers)],
                "required": "Exactly one event_id; cover_epoch must exactly equal the story layer's source event epoch.",
            }, ensure_ascii=False, sort_keys=True))
    elif campaign.composition_mode == "campaign_composite" and (len(campaign.layers) < 2 or not campaign.layer_relationships):
        fail("campaign_composite_structure", "Composite requires multiple connected layers")
    elif editorial and campaign.composition_mode == "scene" and len(campaign.layers) != 1:
        fail("campaign_scene_space", "One editorial visual field uses one layer; use campaign_composite for separate layers")
    layer_cast: set[str] = set()
    for layer in campaign.layers:
        refs(layer, layer["layer_id"])
        layer_cast.update(layer["cast"])
        if layer["type"] == "story_moment" and layer["event_id"] not in campaign.event_ids:
            fail("campaign_event_binding", "Story layer must cite a selected event", layer["layer_id"])
    if layer_cast != set(cast):
        fail("campaign_cast_binding", "Layer cast must match approved plan cast")
    for link_index, link in enumerate(campaign.layer_relationships):
        refs(link, "layer relationship")
        if link["from_layer"] not in layer_map or link["to_layer"] not in layer_map or link["from_layer"] == link["to_layer"]:
            fail("campaign_layer_link", "Layer relationship must connect two existing distinct layers")
            continue
        left, right = layer_map[link["from_layer"]], layer_map[link["to_layer"]]
        if link["temporal_relationship"] == "same_moment" and (
            left["epoch"] != right["epoch"] or left["location"] != right["location"]
            or not left["location"].strip() or not right["location"].strip()
        ):
            fail("campaign_false_simultaneity", "Same-moment layers require matching time and location", json.dumps({
                "path": f"campaign.layer_relationships[{link_index}]", "temporal_relationship": link["temporal_relationship"],
                "from_layer": {key: left[key] for key in ("layer_id", "epoch", "location")},
                "to_layer": {key: right[key] for key in ("layer_id", "epoch", "location")},
                "required": "same_moment requires identical epoch and location; preserve source facts and declare cross_time or timeless_portrait when appropriate, never merge separate scenes.",
            }, ensure_ascii=False, sort_keys=True))
        if link["relationship"] == "cause" and (left["type"] != "story_moment" or right["type"] != "story_moment"):
            fail("campaign_false_causality", "Portraits or object motifs cannot establish event causation", json.dumps({
                "path": f"campaign.layer_relationships[{link_index}].relationship", "relationship": link["relationship"],
                "from_layer": {key: left[key] for key in ("layer_id", "type")},
                "to_layer": {key: right[key] for key in ("layer_id", "type")},
                "required": "cause requires two source-supported story_moment endpoints; portraits and objects may use an appropriate non-causal link such as visual_echo.",
            }, ensure_ascii=False, sort_keys=True))
    for edge_index, edge in enumerate(campaign.interaction_edges):
        refs(edge, edge["actor_id"])
        layer = layer_map.get(edge["layer_id"])
        involved = {edge["actor_id"], *edge["affected_character_ids"]}
        if edge["target_id"] in cast:
            involved.add(edge["target_id"])
        if not layer or not involved.issubset(set(layer["cast"])):
            visible_cast = set(layer["cast"]) if layer else set()
            fail("campaign_interaction_cast", "An interaction must involve visible cast within its layer", json.dumps({
                "path": f"campaign.interaction_edges[{edge_index}]", "layer_id": edge["layer_id"],
                "layer_exists": layer is not None, "actor_id": edge["actor_id"], "target_id": edge["target_id"],
                "affected_character_ids": list(edge["affected_character_ids"]),
                "layer_cast": sorted(visible_cast), "missing_cast": sorted(involved - visible_cast),
                "required": "Every involved character must be visible in this story_moment layer and supported by its source event; do not add absent characters or imply cross-layer physical interaction.",
            }, ensure_ascii=False, sort_keys=True))
        elif layer["type"] != "story_moment":
            fail("campaign_portrait_as_event", "Physical interaction requires an evidenced story moment layer")
        if _STATIC_ACTION.fullmatch(edge["action"].strip()):
            fail("campaign_static_interaction", "Posing or looking at camera is not a story interaction")
    refs(campaign.hook, "hook")
    hook = campaign.hook
    if _GENERIC_QUESTION.fullmatch(hook["open_question"].strip()):
        fail("campaign_generic_hook", "Open question must arise from a specific visible conflict")
    if hook["visible_fact"].strip().casefold() == hook["withheld_answer"].strip().casefold():
        fail("campaign_answer_revealed", "The withheld answer cannot already be the visible fact")
    if hook["visual_trigger"].strip().casefold() == hook["open_question"].strip().casefold():
        fail("campaign_missing_visual_trigger", "A question is not a visible trigger")
    if story_analysis is not None:
        if story_analysis.get("coverage_status") != "complete":
            fail("campaign_analysis_incomplete", "Campaign planning requires complete verified analysis")
        if editorial and not defer_evidence_review and "campaign" not in (approved_claim_paths or set()):
            fail("campaign_expression_unreviewed", "Editorial story meaning requires an independent source-bound evidence review")
        events = {item.get("event_id"): item for item in story_analysis.get("events", ())}
        for event_id in campaign.event_ids:
            if event_id not in events:
                fail("campaign_unknown_event", "Event must exist in the frozen story analysis", event_id)
        for layer_index, layer in enumerate(campaign.layers):
            if layer["type"] != "story_moment" or layer.get("event_id") not in events:
                continue
            event = events[layer["event_id"]]
            if not set(layer["cast"]).issubset(set(event.get("participants", ()))):
                fail("campaign_invented_encounter", "Layer cannot combine people absent from the cited event", layer["layer_id"])
            source_location = event.get("location")
            location_unspecified = not isinstance(source_location, str) or not source_location.strip()
            if location_unspecified:
                fail("campaign_event_location_unspecified",
                     "The analyzed event has no established single location for a literal story moment; choose an event with a source location or use an editorial portrait/object without claiming this encounter",
                     json.dumps({
                         "path": f"campaign.layers[{layer_index}].location",
                         "layer_id": layer["layer_id"], "event_id": layer["event_id"],
                         "source_location": source_location, "proposed_location": layer["location"],
                         "required": "Do not invent a source location. Event participants may be remote, absent or affected later; they do not establish co-presence. Choose a located source event for a story_moment, or an editorial portrait/object with source-supported meaning and no invented encounter.",
                     }, ensure_ascii=False, sort_keys=True))
            if (layer["epoch"] != event.get("epoch")
                    or (not location_unspecified and layer["location"] != source_location)):
                fail("campaign_event_time_place", "Event layer must preserve the source event's epoch and location", layer["layer_id"])
            claim_path = f"campaign.layers[{layer_index}]"
            if (not set(layer["evidence_refs"]).intersection(event.get("evidence_refs", ()))
                    and not defer_evidence_review and claim_path not in (approved_claim_paths or set())):
                fail("campaign_event_evidence", "Layer evidence association requires source-event verification", json.dumps({
                    "path": claim_path + ".evidence_refs", "layer_index": layer_index,
                    "layer_id": layer["layer_id"], "event_id": layer["event_id"],
                    "layer_evidence_refs": list(layer["evidence_refs"]),
                    "event_evidence_refs": list(event.get("evidence_refs", ())),
                    "required": "Keep the claim's own supporting evidence. Disjoint references need a complete independent source-bound evidence review; do not append unrelated event anchors merely to satisfy overlap.",
                }, ensure_ascii=False, sort_keys=True))
        for edge_index, edge in enumerate(campaign.interaction_edges):
            layer = layer_map.get(edge["layer_id"], {})
            event = events.get(layer.get("event_id"))
            claim_path = f"campaign.interaction_edges[{edge_index}]"
            if (event is not None and not set(edge["evidence_refs"]).intersection(event.get("evidence_refs", ()))
                    and not defer_evidence_review and claim_path not in (approved_claim_paths or set())):
                fail("campaign_interaction_evidence", "Interaction evidence association requires source-event verification", json.dumps({
                    "path": claim_path + ".evidence_refs", "edge_index": edge_index,
                    "layer_id": edge["layer_id"], "event_id": layer["event_id"],
                    "actor_id": edge["actor_id"], "action": edge["action"], "target_id": edge["target_id"],
                    "edge_evidence_refs": list(edge["evidence_refs"]),
                    "event_evidence_refs": list(event.get("evidence_refs", ())),
                    "required": "Verify this exact interaction against its own quotes and selected source event. Disjoint references need complete independent evidence review; event anchors or shared characters alone are not proof. Never add an unsupported encounter or action.",
                }, ensure_ascii=False, sort_keys=True))
    return tuple(findings)


def portfolio_findings(campaigns: Sequence[CoverCampaignPlan]) -> tuple[CampaignFinding, ...]:
    """Reject repeated event/interaction/topology despite changing prose, palette or props."""
    seen: dict[tuple[Any, ...], int] = {}
    findings = []
    for index, campaign in enumerate(campaigns):
        signatures = {layer["layer_id"]: (layer["type"], tuple(sorted(layer["cast"])), layer.get("event_id", ""))
                      for layer in campaign.layers}
        topology = []
        for first in campaign.layers:
            for second in campaign.layers:
                if signatures[first["layer_id"]] >= signatures[second["layer_id"]]:
                    continue
                ax, ay, aw, ah = first["region"]
                bx, by, bw, bh = second["region"]
                dx, dy = (ax + aw / 2) - (bx + bw / 2), (ay + ah / 2) - (by + bh / 2)
                # Relative organization can differ; enlarging/cropping all
                # layers without changing their relationship cannot.
                topology.append((signatures[first["layer_id"]], signatures[second["layer_id"]],
                                 "aligned" if abs(dx) < 0.12 else "left" if dx < 0 else "right",
                                 "aligned" if abs(dy) < 0.12 else "above" if dy < 0 else "below"))
        fingerprint = (
            campaign.composition_mode, tuple(sorted(campaign.event_ids)),
            tuple(sorted((e["actor_id"], e["target_id"], tuple(sorted(e["affected_character_ids"])))
                         for e in campaign.interaction_edges)),
            tuple(sorted(signatures.values())), tuple(sorted(topology)),
            tuple(sorted((signatures.get(r["from_layer"], ()), signatures.get(r["to_layer"], ()),
                          r["relationship"], r["temporal_relationship"]) for r in campaign.layer_relationships)),
        )
        if fingerprint in seen:
            findings.append(CampaignFinding("campaign_portfolio_repeated", "Change event, relationship organization or composition topology, not wording or palette", f"plans {seen[fingerprint]} and {index}"))
        seen[fingerprint] = index
    return tuple(findings)


def campaign_response_contract(*, profile_version: str = CAMPAIGN_PROFILE) -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "composition_mode": "scene | campaign_composite",
        "cover_epoch": "source event epoch; composite declares the campaign framing epoch",
        "event_ids": ["exact source events.event_id"],
        "interaction_edges": [{"actor_id": "cast id", "action": "visible action, never posing",
            "target_id": "cast id or evidenced object id", "affected_character_ids": ["cast id"],
            "visible_reaction": "observable response or consequence", "evidence_refs": ["evidence:id"], "layer_id": "layer id"}],
        "hook": {**{name: "source-supported, specific, executable statement" for name in (
            "visible_fact", "unexpected_contradiction", "visual_trigger", "open_question", "withheld_answer", "reader_promise", "spoiler_ceiling")},
            "unexpected_contradiction": "source-specific visible tension, unresolved choice or surprising contrast; do not manufacture an opposing action or require hidden motives/dialogue to be uniquely visible",
            "withheld_answer": "source-supported answer deliberately left open; never require the image to prove this hidden answer",
            "evidence_refs": ["evidence:id"]},
        "layers": [{"layer_id": "unique id", "type": "story_moment | character_portrait | story_object",
            "event_id": "required for story_moment", "cast": ["cast id"], "epoch": "exact source event epoch or timeless for portrait",
            "location": "exact source event location or campaign space for portrait", "function": "visible content and narrative purpose",
            "region": [0.0, 0.0, 1.0, 1.0], "visual_weight": 1.0, "evidence_refs": ["evidence:id"]}],
        "layer_relationships": [{"from_layer": "layer id", "to_layer": "layer id", "relationship": "conflict | alliance | barrier | cause | visual_echo",
            "temporal_relationship": "same_moment | cross_time | timeless_portrait", "truth_boundary": "what the juxtaposition must not imply", "evidence_refs": ["evidence:id"]}],
        "read_order": ["all layer IDs once, first to last"], "title_render_mode": "composed",
        "title_layout": {"title": "exact approved title", "language": "brief language", "lines": ["exact title, optionally line-broken"],
            "font_id": "sans-bold | serif-bold | cjk-sans", "font_weight": "bold", "font_size_min": 0.04,
            "font_size_max": 0.10, "align": "center", "letter_spacing": 0.0, "color": "#ffffff",
            "region": [0.08, 0.03, 0.84, 0.24], "safe_zones": [[0.0, 0.3, 1.0, 0.7]]},
        "rules": ["Scene: exactly one story_moment layer/event and one time/place; no invented encounters.",
            "For scene, cover_epoch must exactly equal the sole layer.epoch and that source event's epoch string; copy the source epoch and location verbatim, not a paraphrase or general cover-era label.",
            "Composite: separate events/portraits/objects into evidenced layers and declare links, time differences and truth boundaries.",
            "same_moment requires exactly matching epoch and location on both linked layers. Events at different times or locations must never be staged as the same scene; retain source time/place and use cross_time or timeless_portrait as appropriate.",
            "A cause relationship requires two story_moment layers with source-supported causation. A character_portrait or story_object cannot be a cause endpoint; an appropriate non-causal link such as visual_echo may connect a motif.",
            "Every interaction actor_id, affected_character_ids and character target_id must occur in that edge's layer.cast, and the layer's cast must be supported by its exact source event. Physical interactions belong to story_moment layers, never across layers or inside a portrait.",
            "For composed titles, title_layout.region is the allowed title area. safe_zones are protected faces, hands and story actions where lettering is forbidden, not title-safe areas; do not put the title region inside a safe_zone.",
            "Keep the exact approved title and reading order in explicit lines. letter_spacing must be 0 through .02 canvas widths (never negative); font_size_min/max are fractions of canvas width. Choose line breaks, region and size range that fit the entire title without covering protected story content.",
            "Interaction actions and visible reactions, not head count, establish the conflict.",
            "The question must follow from the visual trigger and the answer must remain withheld.",
            "Explore scene and composite when supported; no fixed number of either; differentiate reading motive and topology."],
    }
    if uses_commercial_expression(profile_version):
        result["expression_mode"] = "literal_scene | editorial; explicitly choose for every new plan"
        result["event_ids"] = ["exact source event IDs for literal story moments; may be empty in editorial"]
        result["interaction_edges"][0]["action"] = "source-supported physical action; may omit all edges for editorial portrait or symbolic art"
        result["cover_epoch"] = "exact source event epoch for literal_scene; promotional/timeless framing allowed for editorial"
        result["rules"] = [
            "literal_scene preserves the existing exact event, time, location and physical interaction rules.",
            "editorial is promotional expression of the novel's real central relationship, conflict, world or theme, not a literal plot screenshot. It may use photographic portraits, object symbolism or a designed composite without claiming a single source event.",
            "editorial may use event_ids=[] and interaction_edges=[]. Keep real story_evidence_refs plus nonempty hook and layer evidence_refs; quoted source facts must support the core meaning, not every pose, eye direction or ordinary staging detail.",
            "Every new plan declares expression_mode. A story_moment, physical interaction edge, same_moment link or cause link still makes a literal factual claim and retains its event/cast/time/causality checks; editorial is not a waiver for an invented encounter or plot event.",
            "There is no protagonist headcount, visible-choice or per-image causal-action quota. A symbolic cover may have empty cast and focal_character_id; cast characters that are shown must retain source identities and relationships.",
            "Keep the novel-specific promise legible through emotional contrast, relationship geometry, setting or a meaningful motif. Distinct candidates should explore different visual arguments; avoid four generic or cosmetically varied pictures.",
            "Do not invent kinship, betrayal, knowledge, consequential actions, world rules or resolutions. Explicit author requirements and spoiler ceilings remain binding in their original scope.",
            *result["rules"][6:8],  # Deterministic title layout and source/title text integrity.
            "Every layer has a declared purpose, real evidence and readable hierarchy. Composite layers use truthful links; promotional juxtaposition does not assert physical co-presence.",
        ]
    result["rules"].append(
        "An empty source event location means the analysis does not establish one source-supported place for the whole event; the source may describe separate locations or remote actions. Do not fill it with an invented common location or turn remote communication into a meeting. A story_moment requires a nonempty source location, including inside an editorial campaign. Choose an event with a source location, or use an editorial character_portrait/story_object (when the profile supports editorial) without claiming a literal encounter. Event participants can be remote senders, absent people or people affected later and do not establish co-presence. Two empty locations never prove same_moment."
    )
    return result
