import type { ProjectCoverSummary } from "../api/client";

export const COVER_STATUS_LABELS: Record<ProjectCoverSummary["status"], string> = {
  not_started: "待设计", processing: "处理中", awaiting_review: "待确认",
  needs_attention: "需处理", selected: "已选定",
};
