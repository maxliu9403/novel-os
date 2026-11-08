import { displayLabel } from "./displayLabels";

export const TASK_POLL_INTERVAL_MS = 2500;
export const TASK_IDLE_POLL_INTERVAL_MS = 30_000;

const STATUS_LABELS: Record<string, string> = {
  in_progress: "未完成",
  draft: "未启动",
  pending: "待启动",
  paused: "已暂停",
  interrupted: "已中断",
  cancelled: "已取消",
  done: "已完成",
};

const PHASE_LABELS: Record<string, string> = {
  intake: "读取创作要求",
  outline: "规划大纲",
  "planning.global_design": "转换全书设计",
  "planning.chapter_batch": "补齐章节设计",
  "planning.semantic_review": "全书一致性核查",
  "planning.targeted_repair": "定点修订大纲",
  "planning.global_reference_repair": "修订全局章节标记",
  "foundation.commit": "保存故事设定",
  "chapter.plan": "规划章节",
  "chapter.design_check": "检查章节设计",
  "chapter.write": "撰写正文",
  "chapter.check.pre": "初稿质量检查",
  "chapter.edit": "编辑润色",
  "chapter.prepare": "准备审稿版本",
  "chapter.check.post": "修订稿质量检查",
  "chapter.validate": "章节审稿",
  "chapter.commercial_check": "章节阅读体验检查",
  "chapter.style": "检查与保存候选",
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

export function projectTaskElapsed(iso: string | null | undefined, now: number) {
  if (!iso) return null;
  const timestamp = Date.parse(iso);
  if (!Number.isFinite(timestamp) || timestamp > now) return null;
  const seconds = Math.floor((now - timestamp) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
}

export function projectTaskPhaseGuidance(phase: string | null | undefined) {
  if (phase === "outline" || phase === "plan_outline") return {
    current: "设计全书结构、人物轨迹和章节安排。",
    next: "大纲通过校验并保存后，进入章节创作。",
  };
  if (phase === "chapter.write" || phase === "write") return {
    current: "基于已保存的章节设计撰写正文。",
    next: "正文返回后进行质量检查和编辑润色。",
  };
  if (phase?.startsWith("chapter.")) return {
    current: "按当前章节阶段完成创作或质量检查。",
    next: "各项检查通过后保存定稿，再推进后续章节。",
  };
  if (phase?.startsWith("ending.") || phase === "book.check") return {
    current: "检查全书结局、人物弧线及伏笔回收。",
    next: "结局与全书检查通过后，编排并生成交付内容。",
  };
  if (phase === "compile" || phase === "delivery.package") return {
    current: "整理已完成的章节并准备交付文件。",
    next: "交付文件生成并保存后，任务才会显示完成。",
  };
  return {
    current: "执行当前阶段，等待可保存的结果。",
    next: "当前阶段通过检查并保存后，继续下一阶段。",
  };
}
