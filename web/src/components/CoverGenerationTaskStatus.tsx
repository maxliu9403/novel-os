import { useEffect, useState } from "react";
import type { CoverGenerationTask } from "../api/client";
import Icon from "./Icon";

type Props = {task: CoverGenerationTask | null; checking: boolean; connectionError: string; lastSyncedAt: number | null; submitting: boolean};
const labels = {queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断"};
const phases = {preparing: "准备生成封面", generating: "生成封面图片", composing: "排版与合成", reviewing: "审核封面质量", comparing: "检查整组差异", saving: "保存封面结果"};
const time = (value: string | null | undefined) => {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
};
function elapsed(start: number, end: number) {
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
}

export default function CoverGenerationTaskStatus({task, checking, connectionError, lastSyncedAt, submitting}: Props) {
  const [now, setNow] = useState(Date.now);
  const active = task?.status === "queued" || task?.status === "running";
  useEffect(() => {
    if (!active && !checking && !submitting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, checking, submitting]);
  if (!task && !checking && !connectionError && !submitting) return null;
  const delayed = (active || checking) && lastSyncedAt !== null && now - lastSyncedAt > 15000;
  const uncertain = Boolean(connectionError) || delayed;
  const progress = submitting ? null : task?.progress;
  const work = progress ? phases[progress.phase] : null;
  const terminal = task && !active;
  const candidate = progress?.current_candidate_index;
  const title = submitting ? "正在提交封面生成任务"
    : uncertain ? "封面任务状态暂未确认"
    : checking && !active ? task?.status === "completed" ? "封面生成已完成，正在同步结果" : "正在同步封面生成任务"
    : task?.status === "queued" ? "封面生成已排队"
    : task?.status === "running" ? work ? `正在${work}` : "封面正在后台生成"
    : task?.status === "completed" ? task.result_status === "partial" || (progress?.failed || 0) > 0 ? "本批封面已处理，部分图片生成失败" : "本批封面已处理完成"
    : task?.status === "failed" ? "封面生成失败" : "封面生成已中断";
  const description = submitting ? "正在确认后台任务，请勿重复提交。"
    : uncertain ? `${task ? `上次确认：${labels[task.status]}。` : "尚未取得任务状态。"}${connectionError ? "正在重试状态查询。" : "尚未收到新的状态响应。"}${terminal ? "正在同步已保存结果，不会重新出图。" : "当前运行情况待确认。"}`
    : checking && !active ? "正在读取已保存状态与候选图片，不会自动重新出图。"
    : active ? "可以切换页面或关闭浏览器；任务在后台继续，返回后自动同步。"
    : task?.status === "completed" ? "候选图片已保存。生成完成不代表质量审核通过，请逐张查看审核结果。"
    : `${task?.error || "本次任务未完成。"} 已有图片会保留，不会自动重试或增加图片调用。`;
  const spinning = submitting || checking || active || uncertain;
  const warning = uncertain || task?.status === "failed" || task?.status === "interrupted" || (progress?.failed || 0) > 0;
  const created = submitting ? null : time(task?.created_at);
  const finished = time(task?.finished_at);
  const end = active ? now : finished;
  const phaseStart = time(progress?.phase_started_at);
  const synced = lastSyncedAt === null ? null : new Date(lastSyncedAt).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false});

  return <section aria-label="封面生成任务状态" className={`cover-direction-task${warning ? " is-warning" : ""}${task?.status === "completed" && !warning ? " is-complete" : ""}`}>
    <div className="cover-direction-task__main">
      <span className="cover-direction-task__mark" aria-hidden="true">{spinning ? <span className="cover-direction-task__spinner" />
        : <Icon name={task?.status === "completed" ? "circle-check" : "circle-alert"} className="h-5 w-5" />}</span>
      <div className="cover-direction-task__message" role="status" aria-live="polite" aria-atomic="true">
        <h2>{title}</h2>
        <p>{description}</p>
        {work && (uncertain || terminal) && <p>{active ? "上次确认阶段" : "最后阶段"}：{work}</p>}
        {candidate != null && active && <p>{uncertain ? "上次处理" : "当前处理"}：候选图 {candidate} / {progress?.total || task?.count}</p>}
        {progress && <p>已处理 {progress.completed}/{progress.total} 张 · 可查看 {progress.ready} 张 · 生成失败 {progress.failed} 张</p>}
        {progress && <p>已处理包含失败尝试；可查看数量不代表质量审核通过。</p>}
        {terminal && task.error && (uncertain || checking) && <p>任务结果：{task.error}</p>}
        {connectionError && <p className="cover-direction-task__error">{connectionError}</p>}
      </div>
    </div>
    <div className="cover-direction-task__timing" aria-live="off">
      {created !== null && end !== null && <p className="cover-direction-task__elapsed">{active ? uncertain ? "本次任务已等待" : "已用时" : "总用时"} {elapsed(created, end)}</p>}
      {active && !submitting && <p>从任务创建计时，包含排队等待</p>}
      {active && !uncertain && phaseStart !== null && <p>本阶段已用时 {elapsed(phaseStart, now)}</p>}
      <p>{synced ? `${uncertain ? "上次状态同步" : "状态已同步"} ${synced}` : "等待首次状态同步"}</p>
      {active && <p>同步时间不代表模型已有新输出</p>}
    </div>
  </section>;
}
