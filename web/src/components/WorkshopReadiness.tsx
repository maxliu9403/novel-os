import type { WorkshopNextStep } from '../api/workshop';

export default function WorkshopReadiness({ step, disabled, canDeliver, onCapacity, onPlan, onDeliver }: {
  step: WorkshopNextStep; disabled: boolean; canDeliver: boolean;
  onCapacity: () => void; onPlan: () => void; onDeliver: () => void;
}) {
  const readiness = step.readiness;
  if (!readiness || !['ready_for_capacity', 'ready_for_skeleton'].includes(readiness.state) || ['wait', 'recover', 'complete', 'resolve_question', 'edit_basics'].includes(step.kind)) return null;
  const complete = readiness.state === 'ready_for_skeleton';
  return <section aria-label="骨架准备提示" role="status" className="my-4 rounded-2xl border border-violet/30 bg-white px-5 py-5 shadow-sm sm:px-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0 flex-1"><p className="text-[12px] font-medium tracking-wide text-violet">下一步 · 完整骨架</p><h3 className="mt-1 text-[21px] font-semibold">{readiness.title}</h3><p className="mt-2 text-[15px] leading-7 text-ink-muted">{readiness.detail}</p></div>
      <button type="button" className="btn-primary self-center" disabled={disabled || (complete ? !canDeliver : step.kind !== 'choose_capacity' && !step.can_plan_capacity)} onClick={complete ? onDeliver : step.kind === 'choose_capacity' ? onCapacity : onPlan}>{complete ? '开始生成完整骨架' : step.kind === 'choose_capacity' ? '查看并调整篇幅' : '获取篇幅建议'}</button></div>
    <p className="mt-3 text-[12px] leading-6 text-ink-muted">你仍可以继续打磨故事。生成骨架会整理章节、人物与情绪兑现，暂不启动正文。</p>
  </section>;
}
