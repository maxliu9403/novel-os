import { useEffect, useState } from "react";
import type { PipelineProgress, ProjectTaskDetail } from "../api/client";
import {
  formatProjectTaskTime,
  isProjectTaskRunning,
  projectTaskElapsed,
  projectTaskPhaseGuidance,
  projectTaskPhaseLabel,
  projectTaskStatusLabel,
} from "../lib/projectTasks";

const STEP_LABELS: Record<string, string> = {
  pending: "待开始", running: "进行中", completed: "已保存", blocked: "待处理",
  paused: "已暂停", incomplete: "未完成", awaiting_update: "等待阶段更新",
};

function requestActivity(progress: PipelineProgress, now: number) {
  const request = progress.request;
  if (!request) return { label: "当前阶段处理中", detail: "尚无当前模型请求记录。" };
  if (request.status === "completed") return {
    label: "回复已返回 · 校验与保存",
    detail: "已收到完整模型回复；通过阶段检查后才会记为已保存。",
  };
  if (request.status === "timeout") return { label: "模型请求已超时", detail: "请查看失败记录和已保存的阶段成果。" };
  if (request.status === "failed") return { label: "模型请求失败", detail: "请查看失败记录；返回内容不等于阶段成果。" };
  if (request.status === "cancelled") return { label: "模型请求已取消", detail: "任务是否已暂停以后台最终状态为准。" };
  const contentTime = Date.parse(progress.last_model_activity_at ?? "");
  const requestTime = Date.parse(request.started_at ?? "");
  // A content event from an earlier request must not make a new request look active.
  const hasContent = request.model_activity_count > 0 || request.output_chars > 0;
  const currentContent = hasContent && Number.isFinite(contentTime) && contentTime >= requestTime;
  return hasContent ? {
    label: "已收到模型内容 · 等待完整回复",
    detail: currentContent
      ? `最近收到内容：${projectTaskElapsed(progress.last_model_activity_at, now) ? `${projectTaskElapsed(progress.last_model_activity_at, now)}前` : "时间待同步"}。活动记录不代表模型始终在生成。`
      : "已记录本次模型内容事件，具体活动时间待同步。",
  } : {
    label: "等待模型内容",
    detail: "尚未收到本次请求的模型内容，无法确认模型是否正在生成。",
  };
}

export default function TaskProgress({ task }: { task: ProjectTaskDetail }) {
  const [now, setNow] = useState(() => Date.now());
  const running = isProjectTaskRunning(task.status);
  const progressRecord = task.progress?.run_id === task.run_id ? task.progress : null;
  // The old execution sidecar can remain briefly while a resume job starts.
  // Keep its history, but never advertise its completed request as live work.
  const progress = running && progressRecord?.status === "running" ? progressRecord : null;
  const planning = task.planning;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const clock = running ? now : Date.parse(progress?.updated_at ?? task.updated_at ?? "") || now;
  const activity = progress ? requestActivity(progress, clock) : null;
  const guidance = projectTaskPhaseGuidance(task.current_phase);
  const planningActive = task.current_phase === "outline" || task.current_phase === "plan_outline";
  const message = (planningActive && planning?.message) || guidance.current;
  const nextAction = (planningActive && planning?.next_action) || guidance.next;
  const saved = planning?.milestones ?? [];
  const stages = task.stages ?? [];
  const milestones = progressRecord?.milestones ?? [];

  return (
    <div className="mt-4 min-w-0 space-y-4" aria-label="阶段进度与成果">
      {running && (
        <div className="min-w-0 rounded-2xl border border-[rgba(104,103,234,0.15)] bg-[rgba(241,241,255,0.72)] p-4 sm:p-5">
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p role="status" className="flex items-center gap-2 text-[13px] font-semibold text-ink-text">
                <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-violet)]" />
                {task.cancel_requested ? "正在暂停，等待执行结束" : activity?.label ?? "任务运行中 · 模型活动未知"}
              </p>
              <p className="mt-2 break-words text-[12px] leading-relaxed text-ink-muted">
                {task.cancel_requested ? "暂停请求已提交，后台确认退出后才会显示已暂停。" : activity?.detail ?? "此执行未提供模型活动记录，无法据此判断模型是否正在生成。"}
              </p>
            </div>
            {progress && <p className="shrink-0 text-[11px] tabular-nums text-ink-muted">本次执行 {projectTaskElapsed(progress.started_at, clock) ?? "时间待同步"}</p>}
          </div>
          <div className="mt-4 grid min-w-0 gap-4 border-t border-[rgba(104,103,234,0.12)] pt-4 md:grid-cols-2">
            <div className="min-w-0">
              <h3 className="text-[11px] font-medium text-ink-muted">当前在做什么</h3>
              <p className="mt-1.5 break-words text-[13px] leading-relaxed text-ink-text">{message}</p>
            </div>
            <div className="min-w-0">
              <h3 className="text-[11px] font-medium text-ink-muted">接下来</h3>
              <p className="mt-1.5 break-words text-[13px] leading-relaxed text-ink-text">{task.cancel_requested ? "等待后台退出，保留已保存成果供恢复时校验复用。" : nextAction}</p>
            </div>
          </div>
          {progress && (
            <details className="mt-4 border-t border-[rgba(104,103,234,0.12)] pt-3 text-[11px] text-ink-muted">
              <summary className="cursor-pointer">查看活动与时间记录</summary>
              <dl className="mt-3 grid min-w-0 gap-x-6 gap-y-2 sm:grid-cols-2">
                <ActivityItem label="本阶段用时" value={projectTaskElapsed(progress.phase_started_at, clock) ?? "时间未知"} />
                <ActivityItem label={progress.request?.status === "running" ? "本次请求用时" : "本次请求开始"} value={progress.request ? progress.request.status === "running" ? projectTaskElapsed(progress.request.started_at, clock) ?? "时间未知" : formatProjectTaskTime(progress.request.started_at) ?? "时间未知" : "尚无请求记录"} />
                <ActivityItem label="后台在线信号" value={formatProjectTaskTime(progress.heartbeat_at) ?? "尚未记录"} />
                <ActivityItem label="最近上游事件" value={formatProjectTaskTime(progress.last_upstream_activity_at) ?? "尚未记录"} />
                <ActivityItem label="最近模型内容" value={formatProjectTaskTime(progress.last_model_activity_at) ?? "尚未收到"} />
                <ActivityItem label="最近完整回复" value={formatProjectTaskTime(progress.last_output_at) ?? "尚未收到"} />
                {progress.request && <>
                  <ActivityItem label="本次收到内容" value={`${(progress.request.output_chars ?? 0).toLocaleString()} 字符`} />
                  <ActivityItem label="请求模型" value={`${progress.request.provider} · ${progress.request.model}`} />
                </>}
              </dl>
              <p className="mt-3 leading-relaxed">后台在线信号和上游事件不代表模型持续生成；时间记录不展示模型的私有推理内容。</p>
            </details>
          )}
        </div>
      )}

      {planning && (
        <section aria-label="大纲规划成果" className="min-w-0 rounded-2xl border border-[rgba(74,91,133,0.12)] bg-white/60 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-[13px] font-semibold text-ink-text">大纲规划成果</h3>
            <span className="text-[11px] text-ink-muted">{planning.status === "complete" ? "规划成果已保存" : "按阶段保存，通过校验后继续"}</span>
          </div>
          {!running && planningActive && planning.status === "failed" && planning.next_action && (
            <p className="mt-3 break-words rounded-xl bg-amber-50 px-3 py-3 text-sm leading-relaxed text-ink-text">
              下一步：{planning.next_action}
            </p>
          )}
          <ol className="mt-4 grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {planning.steps.map((step, index) => {
              const status = step.status !== "running" ? step.status : !running
                ? task.status === "paused" ? "paused" : "incomplete"
                : planning.status === "running" && planningActive ? "running" : "awaiting_update";
              return <li key={step.id} aria-current={step.id === planning.active_step && status === "running" ? "step" : undefined}
                className={`min-w-0 rounded-xl border px-3 py-3 ${status === "running" ? "border-[rgba(104,103,234,0.3)] bg-[rgba(239,237,255,0.7)]" : "border-[rgba(74,91,133,0.1)] bg-white/50"}`}>
                <div className="flex min-w-0 items-start gap-2">
                  <span aria-hidden="true" className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] ${step.status === "completed" ? "bg-[#e7f2ee] text-[#43826c]" : "bg-[rgba(104,103,234,0.08)] text-[var(--color-violet)]"}`}>{step.status === "completed" ? "✓" : index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-[12px] font-medium text-ink-text">{step.label}</p>
                    <p className="mt-0.5 text-[11px] text-ink-muted">{STEP_LABELS[status] ?? "状态待同步"}</p>
                  </div>
                </div>
                {step.summary && <p className="mt-2 break-words text-[11px] leading-relaxed text-ink-muted">{step.summary}</p>}
              </li>;
            })}
          </ol>
          {planning.total_batches > 0 && (
            <div className="mt-4">
              <div className="flex flex-wrap justify-between gap-1 text-[11px] text-ink-muted">
                <span>章节大纲批次：已保存 {planning.completed_batches}/{planning.total_batches} 批</span>
                {planning.chapter_count > 0 && <span>共 {planning.chapter_count} 章</span>}
              </div>
              <progress aria-label="已保存的章节大纲批次" value={planning.completed_batches} max={planning.total_batches}
                className="mt-2 block h-1.5 w-full overflow-hidden rounded-full border-0 bg-[rgba(104,103,234,0.1)] [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-[rgba(104,103,234,0.1)] [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-[var(--color-violet)] [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-[var(--color-violet)]" />
              <p className="mt-1.5 text-[11px] text-ink-muted">仅统计已保存且通过校验的批次，不代表整本小说的完成比例。</p>
            </div>
          )}
          <div className="mt-4 border-t border-[rgba(74,91,133,0.1)] pt-3">
            <h4 className="text-[12px] font-medium text-ink-text">已保存的阶段成果</h4>
            {saved.length === 0 ? <p className="mt-2 text-[11px] text-ink-muted">尚无已保存的阶段成果；模型回复将先接受检查。</p> : <>
              <ul className="mt-3 space-y-3">
                {saved.slice(-3).map((item) => <SavedMilestone key={item.id} {...item} />)}
              </ul>
              {saved.length > 3 && <details className="mt-3 text-[11px] text-ink-muted">
                <summary className="cursor-pointer">查看更早的 {saved.length - 3} 项成果</summary>
                <ul className="mt-3 space-y-3">{saved.slice(0, -3).map((item) => <SavedMilestone key={item.id} {...item} />)}</ul>
              </details>}
            </>}
          </div>
        </section>
      )}

      {(milestones.length > 0 || stages.length > 0) && (
        <details className="min-w-0 rounded-2xl border border-[rgba(74,91,133,0.12)] bg-white/55 px-4 py-3 text-[12px] text-ink-muted">
          <summary className="cursor-pointer font-medium text-ink-text">阶段记录 · 刷新后可恢复查看</summary>
          {milestones.length > 0 && <ul className="mt-3 space-y-3" aria-label="本次执行记录">
            {milestones.map((item, index) => <li key={`${item.at}:${index}`} className="min-w-0 break-words">
              <p className="text-ink-text">{item.message}</p>
              <p className="mt-1 text-[11px]">{formatProjectTaskTime(item.at) ?? "时间未知"} · {projectTaskPhaseLabel(item.phase)}{item.chapter ? ` · 第 ${item.chapter} 章` : ""}</p>
            </li>)}
          </ul>}
          {stages.length > 0 && <ul className="mt-3 space-y-2 border-t border-[rgba(74,91,133,0.1)] pt-3" aria-label="全部阶段记录">
            {stages.map((stage, index) => <li key={`${stage.phase}:${stage.chapter}:${stage.attempt}:${index}`} className="flex min-w-0 flex-wrap justify-between gap-2">
              <span className="min-w-0 break-words text-ink-text">{projectTaskPhaseLabel(stage.phase)}{stage.chapter ? ` · 第 ${stage.chapter} 章` : ""}{stage.attempt && stage.attempt > 1 ? ` · 第 ${stage.attempt} 次` : ""}</span>
              <span className="text-[11px]">{projectTaskStatusLabel(stage.status)} · {formatProjectTaskTime(stage.finished_at || stage.started_at) ?? "时间未知"}</span>
            </li>)}
          </ul>}
        </details>
      )}
    </div>
  );
}

function ActivityItem({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt>{label}</dt><dd className="mt-0.5 break-all text-ink-text">{value}</dd></div>;
}

function SavedMilestone({ time, label, summary }: { time: string; label: string; summary?: string }) {
  return <li className="flex min-w-0 gap-2.5">
    <span aria-hidden="true" className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#68a58c]" />
    <div className="min-w-0 flex-1">
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 break-words text-[12px] font-medium text-ink-text">{label}</p>
        <time className="text-[11px] text-ink-muted">{formatProjectTaskTime(time) ?? "时间未知"}</time>
      </div>
      {summary && <p className="mt-1 break-words text-[11px] leading-relaxed text-ink-muted">{summary}</p>}
    </div>
  </li>;
}
