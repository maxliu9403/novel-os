import type { WorkshopNextStep, WorkshopSession } from '../api/workshop';

/** API capabilities are authoritative; old cached responses retain a safe route. */
export function workshopNextStep(session: WorkshopSession): WorkshopNextStep {
  if (session.next_step) return session.next_step;
  const result = (kind: WorkshopNextStep['kind'], label: string, reason: string, canContinue = false, canDeliver = false, canPlan = false): WorkshopNextStep => ({
    kind, label, reason, can_continue: canContinue, can_deliver: canDeliver, can_plan_capacity: canPlan,
    delivery_blocked_reason: canDeliver ? null : reason,
  });
  if (session.status === 'running') return result('wait', '正在推演', '等待本轮回复完成，已保存内容会继续保留。');
  if (session.status === 'launched') return result('complete', '已启动创作', '请在作品任务中查看创作进度。');
  if (session.restart_required || !session.input.premise.trim()) return result('edit_basics', '检查基础设定', '请先保存有效的基础设定，再继续讨论。');
  if (session.status === 'paused' || session.status === 'error') return result('recover', '恢复本轮', '请先恢复或重试尚未完成的本轮任务。');
  if (session.creative_design?.story_state?.pending) return result('resolve_question', '决定当前问题', '请先决定本轮故事走向，再生成完整骨架。');
  if (session.creative_design?.policy.capacity_version === 'story-capacity.v1') {
    const capacity = session.capacity;
    if (capacity?.status === 'confirmed' && capacity.confirmed) return result('deliver', '生成完整骨架', '篇幅已确认，可以展开完整人物、章节和情绪回报。', true, true);
    if (capacity?.status === 'proposed' && capacity.plan && capacity.proposal_sha256) return result('choose_capacity', '查看篇幅方案', '请先选择篇幅方案；采用后会直接生成完整骨架。', true, false, true);
    return result('plan_capacity', '完善篇幅方案', '当前没有待决定的重大分歧，下一步先完善篇幅方案，再由你确认并生成完整骨架。', true, false, true);
  }
  const advice = session.creative_design?.rounds.at(-1)?.guidance;
  if (advice?.next_action === 'generate' && advice.reviewed_revision === session.revision) return result('deliver', '生成完整骨架', '故事主线已收拢，可以展开完整骨架。', true, true);
  return result('discuss', '继续完善故事', '可以继续推演人物选择、情绪回报和全书因果，无需额外填写想法。', true, true);
}
