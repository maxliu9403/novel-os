import type { CoverRepairTask, CoverSet } from "../api/client";

export function repairTaskFixture(status: CoverRepairTask["status"] = "awaiting_approval"): CoverRepairTask {
  return {task_id: "repair-task-fixture", status, kind: "design", phase: status,
    parent_cover_set_id: "parent-set", parent_candidate_id: "candidate-1", candidate_number: 1,
    concept_id: "concept-1", direction_id: "repair-direction", cover_set_id: null, result_candidate_id: null,
    image_calls: 1, proposal_sha256: "proposal-hash", error: null,
    quality_report: {status: "blocked", blockers: ["core_conflict_missing"], repair_codes: [], evidence: ["动作与故事相反。"]},
    created_at: "2026-10-11T12:00:00Z", updated_at: "2026-10-11T12:02:00Z", finished_at: null,
    proposal: {direction_id: "repair-direction", direction_sha256: "direction-hash", brief_sha256: "brief-hash", image_calls: 1,
      plan: {concept_id: "concept-1", visual_strategy: "A truthful visible exchange", cast: ["protagonist"],
        focal_character_id: "protagonist", frozen_action: "She returns the unsigned page.", blocking: "An unobstructed table separates the pair.",
        primary_prop: "page", visual_hook: {hook_type: "refusal", first_glance_subject: "her hand", open_question: "Will she agree?",
          reader_promise: "A choice with consequences", expected_thumbnail_read: "A deliberate refusal"}}},
  };
}
export function repairedSetFixture(): CoverSet {
  return {cover_set_id: "repaired-set", project_id: "book", revision: 1, active_revision: 0, status: "ready",
    requested_count: 4, brief: {title: "Book"}, concepts: [], candidates: [], selected_candidate_id: "",
    source_prompt_sha256: "source", foundation_sha256: "foundation", created_at: "2026-10-11T12:03:00Z", updated_at: "2026-10-11T12:03:00Z"};
}
