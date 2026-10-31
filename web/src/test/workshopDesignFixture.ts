import type { WorkshopDesign } from "../api/workshop";

export function workshopDesign(): WorkshopDesign {
  return { schema: "workshop-design.v1", policy_sha256: "frozen-policy",
    policy: { guidance: { version: "genre-design.2026-10.v1", selections: [
      { selection: "Werewolf", coverage: "specialized_guidance" }, { selection: "原创类型", coverage: "general_guidance" },
    ] } }, decisions: [], rounds: [{ source_revision: 1, message_index: 0, status: "recorded",
      next_focus: { topic: "旧敌为何愿意结盟", why: "双方利益决定联盟代价。" },
      recommendation_reason: "让身份规则改变人物选择。", assumptions: ["族群规模待定"], conflicts: ["离开还是改变族群"],
      proposals: [{ id: "r1:p1", title: "庇护的交换", story: "Rin拒绝献出盟友，失去庇护后争取旧敌结盟。",
        tradeoff: "更多政治博弈，减少命定爱情。", recommended: true,
        genre_roles: [{ selection: "Werewolf", role: "world", contribution: "族群规则限制安全资源。" }],
        mechanisms: [{ selection: "Werewolf", name: "庇护契约", rule: "劳务换取庇护", boundary: "族长不能控制无契约者",
          cost: "失去住所", choice: "庇护或盟友", consequence: "旧敌抬高条件", payoff: "联盟改变规则", removal_effect: "高潮的权力交换不成立" }],
      }] }],
  };
}
