import { displayLabel } from "./displayLabels";

export const TASK_POLL_INTERVAL_MS = 2500;

const STATUS_LABELS: Record<string, string> = {
  in_progress: "未完成",
  draft: "未启动",
  pending: "待启动",
  paused: "已暂停",
  interrupted: "已中断",
  cancelled: "已取消",
};

const PHASE_LABELS: Record<string, string> = {
  intake: "读取创作要求",
  outline: "规划大纲",
  "foundation.commit": "保存故事设定",
  "chapter.plan": "规划章节",
  "chapter.design_check": "检查章节设计",
  "chapter.write": "撰写正文",
  "chapter.check.pre": "初稿质量检查",
  "chapter.edit": "编辑润色",
  "chapter.check.post": "修订稿质量检查",
  "chapter.validate": "连续性校验",
  "chapter.commercial_check": "章节阅读体验检查",
  "chapter.style": "风格校验",
  "chapter.promote": "审核定稿",
  "long_form.audit": "长篇一致性检查",
  "commercial.free_trial_review": "试读内容检查",
  "commercial.book_review": "全书阅读体验检查",
  "ending.preflight": "准备结局检查",
  "ending.review": "结局检查",
  "book.check": "全书连续性检查",
  compile: "编排导出",
  "delivery.package": "生成交付包",
  initialize: "初始化作品",
  plan_outline: "规划大纲",
  plan_chapter: "规划章节",
  write: "撰写正文",
  edit: "编辑润色",
  validate: "连续性校验",
  style: "风格校验",
  approve: "审核定稿",
  export: "导出作品",
  "publication.copy": "出版文案生成",
};

export function projectTaskStatusLabel(status: string) {
  return STATUS_LABELS[status] ?? displayLabel(status);
}

export function projectTaskPhaseLabel(phase: string | null | undefined) {
  if (phase?.startsWith("chapter.repair.")) return "修复章节质量";
  if (phase?.startsWith("chapter.design.repair.")) return "修复章节设计";
  if (phase?.startsWith("chapter.commercial.repair.")) return "修复章节阅读体验";
  return phase ? PHASE_LABELS[phase] ?? displayLabel(phase) : "尚未记录";
}

export function isProjectTaskRunning(status: string) {
  return status === "running" || status === "pending" || status === "queued";
}

export function formatProjectTaskTime(iso: string | null | undefined) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}
