import type { SourceBook, SourceDesignState, SourceSelection } from "../api/sourceBooks";
import { sourceStatusLabels } from "./sourceStatus";

export const designStatusLabels: Record<SourceDesignState["status"], string> = {
  not_started: "尚未设计",
  running: "设计中",
  paused: "设计已暂停",
  needs_attention: "设计待处理",
  ready: "路线就绪",
  unknown: "设计状态待同步",
};

export const sourceModeLabels: Record<SourceSelection["mode"], string> = {
  source_development: "原故事推演",
  original_rewrite: "改编重写",
  reference_creation: "借鉴创作",
};

export function sourceMode(value: string | null | undefined): SourceSelection["mode"] | undefined {
  return value === "source_development" || value === "original_rewrite" || value === "reference_creation"
    ? value : undefined;
}

export type DesignFilter = "all" | "running" | "paused" | "needs_attention" | "ready";
export const designFilters: Array<{ value: DesignFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "running", label: "设计中" },
  { value: "paused", label: "已暂停" },
  { value: "needs_attention", label: "待处理" },
  { value: "ready", label: "路线就绪" },
];

export function matchesDesignFilter(book: SourceBook, filter: DesignFilter): boolean {
  if (filter === "all") return true;
  const status = book.design?.status;
  return status === filter || (filter === "needs_attention" && status === "unknown");
}

export function sourcePickerStatus(book: SourceBook): string {
  return book.design && book.design.status !== "not_started"
    ? designStatusLabels[book.design.status]
    : sourceStatusLabels[book.study.status];
}
