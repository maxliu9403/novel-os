import { useEffect, useState } from "react";
import type { CoverDirectionTask } from "../api/client";
import Icon from "./Icon";

type Props = {
  task: CoverDirectionTask | null;
  checking: boolean;
  connectionError: string;
  lastSyncedAt: number | null;
  submitting?: boolean;
};

const statuses = { queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断" };
const titles = {
  queued: "美术方向已排队", running: "美术方向正在后台规划", completed: "美术方向已生成",
  failed: "美术方向规划失败", interrupted: "美术方向规划已中断",
  syncing: "正在同步美术方向任务", reconnecting: "正在恢复状态连接", submitting: "正在提交美术方向任务",
  delayed: "状态同步较慢",
};
const workPhases = {
  draft_started: "生成美术方向", draft_response: "校验方向草案", draft_validation: "校验方向草案",
  semantic_started: "独立审核故事与构图", semantic_response: "检查审核结论", semantic_validation: "检查审核结论",
  completed: "保存并核验美术方向", failure: "处理规划失败",
};
const timestamp = (value: string | null | undefined) => {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
};
function duration(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  const tail = `${String(seconds % 60).padStart(2, "0")} 秒`;
  return minutes < 60 ? `${minutes} 分 ${tail}`
    : `${Math.floor(minutes / 60)} 小时 ${String(minutes % 60).padStart(2, "0")} 分 ${tail}`;
}

/** Shows durable task state; the clock is elapsed wall time, never model progress. */
export default function CoverDirectionTaskStatus({ task, checking, connectionError, lastSyncedAt, submitting = false }: Props) {
  const [now, setNow] = useState(Date.now);
  const active = task?.status === "queued" || task?.status === "running";
  useEffect(() => {
    if (!active && !checking) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, checking]);

  if (!task && !checking && !connectionError && !submitting) return null;
  const delayed = (active || checking) && lastSyncedAt !== null && now - lastSyncedAt > 15000;
  const phase = connectionError ? "reconnecting" : submitting ? "submitting"
    : delayed ? "delayed" : checking && !active ? "syncing" : task?.status || "syncing";
  const spinning = ["queued", "running", "syncing", "reconnecting", "submitting", "delayed"].includes(phase);
  const warning = ["failed", "interrupted", "reconnecting", "delayed"].includes(phase);
  const created = timestamp(task?.created_at);
  const finished = timestamp(task?.finished_at);
  const end = active ? now : finished;
  const elapsed = !submitting && created !== null && end !== null ? duration(end - created) : null;
  const progress = !submitting ? task?.progress : undefined;
  const workLabel = progress && workPhases[progress.phase]
    ? progress.phase === "draft_started" && progress.attempt > 1 ? "修订美术方向" : workPhases[progress.phase]
    : null;
  const phaseStarted = timestamp(progress?.phase_started_at);
  const phaseElapsed = progress && phaseStarted !== null && phase === "running" ? duration(now - phaseStarted) : null;
  const workTitle = workLabel && phase === "running" ? `正在${workLabel}` : titles[phase];
  const synced = lastSyncedAt !== null && Number.isFinite(lastSyncedAt)
    ? new Date(lastSyncedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : null;
  const description = phase === "reconnecting"
    ? task?.status === "completed" ? "任务已完成，正在重试加载已保存方案，不会重新规划。"
      : `${task ? `上次确认：${statuses[task.status]}。` : "尚未取得任务状态。"}正在自动重试状态查询，当前运行情况待确认。`
    : phase === "delayed" ? task?.status === "completed" ? "任务已完成，正在等待已保存方案同步。"
      : `${task ? `上次确认：${statuses[task.status]}。` : "尚未取得任务状态。"}尚未收到新的状态响应，当前运行情况待确认。`
    : phase === "submitting" ? "正在确认后台任务，请勿重复提交。"
    : phase === "syncing" ? task?.status === "completed" ? "任务已完成，正在加载已保存方案。" : "正在读取已保存状态，请稍候。"
    : phase === "queued" ? "任务已保存，等待后台开始。可以离开页面，返回后会自动同步。"
    : phase === "running" ? "可以切换页面或关闭浏览器；任务在后台继续，返回后自动同步。"
    : phase === "completed" ? "方案已保存，请审核并批准后生成候选封面。"
    : `${task?.error || "本次任务未完成。"} 可重新创建方向；不会自动重试模型调用。`;

  return <section aria-label="美术方向任务状态" className={`cover-direction-task${warning ? " is-warning" : ""}${phase === "completed" ? " is-complete" : ""}`}>
    <div className="cover-direction-task__main">
      <span className="cover-direction-task__mark" aria-hidden="true">
        {spinning ? <span className="cover-direction-task__spinner" />
          : <Icon name={phase === "completed" ? "circle-check" : "circle-alert"} className="h-5 w-5" />}
      </span>
      <div className="cover-direction-task__message" role="status" aria-live="polite" aria-atomic="true">
        <h2>{workTitle}</h2>
        <p>{description}</p>
        {workLabel && phase !== "running" && <p>{active ? "上次确认阶段：正在" : "最后阶段："}{workLabel}</p>}
        {progress && <p>第 {progress.attempt} 轮 · 已收到 {progress.completed_model_calls} 次模型回复</p>}
        {active && <p>Token 在模型调用结束后更新；回复次数不代表已通过审核，也不代表封面张数。</p>}
        {connectionError && <p className="cover-direction-task__error">{connectionError}</p>}
      </div>
    </div>
    <div className="cover-direction-task__timing" aria-live="off">
      {elapsed && <p className="cover-direction-task__elapsed">{active ? connectionError || delayed ? "本次任务已等待" : "已用时" : "总用时"} {elapsed}</p>}
      {elapsed && active && <p>从任务创建计时，包含排队等待</p>}
      {phaseElapsed && <p>本阶段已用时 {phaseElapsed}</p>}
      <p>{synced ? `${connectionError || delayed ? "上次状态同步" : "状态已同步"} ${synced}` : "等待首次状态同步"}</p>
      {active && <p>同步时间不代表模型已有新输出</p>}
    </div>
  </section>;
}
