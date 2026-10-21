import type { WorkshopDeliveryStage } from "../api/workshop";

export const DELIVERY_STAGE_LABELS: Record<WorkshopDeliveryStage, string> = {
  story_design: "全局故事设计", chapter_plan: "完整章节规划", assembly: "骨架交付整合",
};
