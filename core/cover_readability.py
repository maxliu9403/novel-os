"""Shared visible-promise rules for cover design and facts-aware image review.

This is a prompt contract, not a classifier or an approval shortcut. Blind
observation must not receive this story-aware policy. Each caller receives an
independent object so concurrent owners cannot alter one another's contract.
"""

from typing import Any


def cover_readability_policy(*, commercial: bool = False) -> dict[str, Any]:
    if commercial:
        return commercial_cover_policy()
    return {
        "version": "cover-readability.v1",
        "rules": {
            "visible_promise": (
                "Separate each image's observable people, actions, objects and spatial relationships "
                "from source context, inferred motives and deliberately withheld answers. Require a "
                "concrete source-specific visible cause and response; context may explain their meaning "
                "but cannot supply an absent visual anchor. A cover need not reveal the hidden answer."
            ),
            "material_competing_read": (
                "Block a competing reading only when supported by concrete visible cues and it materially "
                "contradicts the source or this image's source-specific visible promise. An opposite action, "
                "invented access restriction or permission, changed relationship, knowledge or causation "
                "remains a failure. Name the material difference and the cue, not every imaginable reading. "
                "Do not require a unique interpretation among compatible emotional or psychological readings; "
                "ambiguity about an intentionally withheld motive is not itself a contradiction."
            ),
            "narrowing": (
                "Narrowing a rejected promise requires synchronizing all members of campaign.hook and "
                "visual_hook, the interaction_edges, and every affected dependent field. Removing a claim "
                "only from the rationale does not remove it from the design. Retain a concrete "
                "source-specific visual anchor and truthful cause/response, not generic sadness or a "
                "renamed bland encounter. Keep compatible shared book-level facts; revise a shared visual "
                "demand only where it incorrectly forces the rejected claim into every image."
            ),
            "portfolio_scope": (
                "The portfolio must cover a drawable, spoiler-allowed central encounter when the source "
                "supports one, and at least one concrete protagonist choice. Assess each other image against "
                "its own source-specific visible promise; do not require every image to repeat every "
                "relationship contradiction, the same choice, or the entire book conflict. Per-image author "
                "requirements still apply. A single-image review cannot certify the whole portfolio."
            ),
            "rejected_slot_replacement": (
                "If the rejected moment cannot carry a truthful readable promise, replace that slot's "
                "source beat, cast, topology, hook and evidence references together using the frozen source. "
                "concept_id is slot identity, not a requirement to keep the original creative hypothesis. "
                "Preserve plan count, order and unaffected directions; update all affected dependent fields. "
                "Never invent a stronger action, new authority or encounter merely to make a cue readable."
            ),
            "approval_boundary": (
                "Frozen source facts, character identities, author constraints and spoiler limits remain "
                "binding. A narrowed or replaced proposal is not acceptance: rerun schema, source, compiler "
                "and independent evidence and readability review for the complete portfolio. Facts-aware "
                "image review evaluates the actual approved image and preserves frozen blind observations; "
                "it may recommend a new direction but cannot rewrite approval, waive a defect or authorize "
                "additional calls. Unknown visual evidence remains uncertainty, not an automatic pass."
            ),
        },
        "dependent_fields": [
            "campaign.hook", "campaign.interaction_edges", "campaign.event_ids", "campaign.cover_epoch",
            "campaign.layers", "campaign.layer_relationships", "campaign.read_order", "campaign.title_layout",
            "cast", "focal_character_id", "visual_hook", "frozen_action", "gaze_graph", "blocking",
            "depth_plan", "conflict_delivery", "conflict_read", "cause_signal", "consequence_signal",
            "protagonist_action_visible", "conflict_character_ids", "causal_visibility", "design_rationale",
            "evidence_summary", "story_evidence_refs", "core_conflict_visual_contract",
        ],
    }


COMMERCIAL_POLICY_VERSION = "cover-readability.v2"
COMMERCIAL_BLOCKER_CATEGORIES = frozenset({
    "material_story_contradiction", "author_constraint", "source_integrity",
})
COMMERCIAL_ADVISORY_CATEGORIES = frozenset({
    "marketing_appeal", "visual_ambiguity", "composition_craft", "thumbnail_readability",
})


def commercial_cover_policy() -> dict[str, Any]:
    """Marketing communication, distinct from literal reconstruction of a scene."""
    return {
        "version": COMMERCIAL_POLICY_VERSION,
        "goal": "Help a prospective reader recognize the kind of story, its central relationship or pressure, and its emotional promise; create curiosity and visual appeal.",
        "rules": {
            "visible_promise": "A cover is a commercial interpretation of the novel. Convey a recognizable story core through a relationship, character, meaningful object, environment, contrast or emotion. It need not prove an exact plot action, every detail, a full causal chain or the hidden answer in one still image.",
            "creative_freedom": "Allow editorial character juxtaposition, symbolic source-related objects, condensed settings, suggestive posture and lighting, and emotional aftermath with an off-frame cause. These are not literal claims that every pictured person met in this time or place. Keep explicit literal events and physical interactions truthful; do not invent a materially different story.",
            "material_competing_read": "Only a concrete, affirmatively depicted contradiction of established core identity, relationship, world rules, major event or an explicit author constraint is a story blocker. Name the false assertion, its visual cue or plan field, and the conflicting source fact. A possible alternative reading, an ambiguous gaze, absent proof of refusal, or an unseen cause is not by itself a contradiction. Do not enumerate and eliminate every compatible interpretation.",
            "narrowing": "Describe the actual commercial promise honestly. Prefer broad supported tension or aftermath over claiming that a pose proves a disclosure, refusal or legal state. Align hook and dependent fields concisely; lack of a uniquely provable micro-action is advice, not a reason to restart an otherwise truthful design.",
            "portfolio_scope": "Seek different compelling visual approaches across the set, selected for this novel. A character study, relationship composition, environment or symbolic object may each be appropriate. Do not impose a central encounter, exact protagonist decision, direct-cause percentage or cast quota unless the user explicitly requires it. Generic or repetitive concepts deserve concrete marketing advice, not fabricated action to satisfy a checklist.",
            "quality_advice": "Keep marketing appeal, compatible visual ambiguity, composition craft and thumbnail readability as specific actionable advisories and honest scores. Advice does not block a proposal, imply a failed model call or authorize automatic revision or paid redraws. Human approval and selection remain explicit.",
            "evidence_scope": "Use verified source anchors for the story's core facts and the meaning of an editorial interpretation. Do not demand quotes for every finger position, gaze, light or ordinary staging. A literal event, physical interaction, new identity, relationship or consequential claim still needs real support. Missing provenance cannot become an empty or invented passing receipt.",
            "approval_boundary": "Preserve source integrity, explicit author constraints, title accuracy, spoiler boundaries, reference images and separately approved image-call allowances. New interpretation does not rewrite old directions, receipts, recipes or selected images. Stage A stays blind and frozen; Stage B may assess marketing communication without rewriting A or claiming uncertainty is certainty.",
        },
        "hard_blocker_categories": sorted(COMMERCIAL_BLOCKER_CATEGORIES),
        "advisory_categories": sorted(COMMERCIAL_ADVISORY_CATEGORIES),
        "dependent_fields": ["campaign.hook", "campaign.interaction_edges", "campaign.layers", "visual_hook", "frozen_action", "gaze_graph", "blocking", "conflict_read", "cause_signal", "consequence_signal", "design_rationale"],
    }
