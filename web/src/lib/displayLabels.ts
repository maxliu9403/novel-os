/**
 * Chinese presentation labels for values that must remain stable in APIs and storage.
 * Keep protocol keys in English; translate them only at the UI boundary.
 */
const LABELS: Record<string, string> = {
  // Project and workflow state.
  active: "进行中",
  in_progress: "进行中",
  planning: "规划中",
  writing: "写作中",
  revising: "修订中",
  complete: "已完成",
  completed: "已完成",
  archived: "已归档",
  pending: "待处理",
  approved: "已通过",
  blocked: "已阻塞",
  failed: "失败",
  running: "运行中",
  queued: "排队中",
  skipped: "已跳过",
  ready: "就绪",
  planned: "已规划",
  drafted: "已有初稿",
  validated: "已验证",
  in_review: "审核中",
  generating: "生成中",
  partial: "部分完成",
  stale: "需更新",
  rejected: "已拒绝",
  selected: "已选择",
  draft: "初稿",
  outline: "大纲",
  revised: "修订稿",
  final: "定稿",

  // Story entities and search result kinds.
  character: "人物",
  location: "地点",
  worldbuilding: "世界设定",
  item: "物件",
  relationship: "关系",
  chapter: "章节",
  project: "作品",
  protagonist: "主角",
  antagonist: "反派",
  supporting: "配角",

  // Relationship labels.
  ally: "盟友",
  rival: "对手",
  family: "家人",
  romantic: "恋人",
  mentor: "导师",
  enemy: "敌人",
  owes_debt: "亏欠",
  secret: "秘密关系",
  unknown: "未知",

  // Agents and people.
  architect: "架构师",
  scribe: "执笔者",
  editor: "编辑",
  guardian: "连续性守卫",
  continuity_guardian: "连续性守卫",
  style_curator: "风格策展",
  author: "作者",
  beta: "试读者",

  // Validation.
  critical: "严重",
  warning: "警告",
  info: "提示",
  unresolved: "未解决",
  resolved: "已解决",
  timeline: "时间线",
  world_rule: "世界规则",
  dormant_thread: "停滞故事线",
  plot: "情节",
  knowledge: "人物认知",
  chronology: "时间顺序",
};

export function displayLabel(value: string | null | undefined): string {
  if (!value) return "";
  const key = value.trim().toLowerCase().replace(/[ -]+/g, "_");
  return LABELS[key] ?? value.replace(/[_-]+/g, " ");
}

export function agentLabel(value: string | null | undefined, fallback = ""): string {
  return value?.trim() ? displayLabel(value) : fallback;
}

export function stageLabel(value: string): string {
  return displayLabel(value);
}

export function entityTypeLabel(value: string): string {
  return displayLabel(value);
}
