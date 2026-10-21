import { useEffect, useRef, useState } from "react";
import { api, type ProjectTaskDetail } from "../api/client";
import {
  formatProjectTaskTime,
  isProjectTaskRunning,
  projectTaskPhaseLabel,
  projectTaskStatusLabel,
  TASK_POLL_INTERVAL_MS,
} from "../lib/projectTasks";
import StatusPill from "./StatusPill";

export default function TaskStatusPanel({
  projectId,
  runId,
  onTaskChanged,
}: {
  projectId: string;
  runId: string;
  onTaskChanged?: () => void;
}) {
  const [task, setTask] = useState<ProjectTaskDetail | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const latest = useRef<ProjectTaskDetail | null>(null);
  const resumeLock = useRef(false);
  const requestVersion = useRef(0);

  useEffect(() => {
    const refresh = () => setRefreshKey((value) => value + 1);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function refresh() {
      const version = ++requestVersion.current;
      try {
        const next = await api.projectTask(projectId, runId);
        if (cancelled || version !== requestVersion.current) return;
        const previous = latest.current;
        latest.current = next;
        setTask(next);
        setReadError(null);
        if (previous && (previous.status !== next.status || previous.current_chapter !== next.current_chapter)) {
          onTaskChanged?.();
        }
        if (isProjectTaskRunning(next.status)) timer = window.setTimeout(refresh, TASK_POLL_INTERVAL_MS);
      } catch (e) {
        if (cancelled || version !== requestVersion.current) return;
        setReadError(e instanceof Error ? e.message : String(e));
        if (latest.current && isProjectTaskRunning(latest.current.status)) {
          timer = window.setTimeout(refresh, TASK_POLL_INTERVAL_MS);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [projectId, runId, refreshKey, onTaskChanged]);

  async function resume() {
    if (!task?.can_resume || resumeLock.current) return;
    resumeLock.current = true;
    setSubmitting(true);
    setResumeError(null);
    try {
      const result = await api.resumeProjectTask(projectId, runId);
      requestVersion.current += 1;
      const running = { ...task, status: "running", can_resume: false, resume_job_id: result.job_id };
      latest.current = running;
      setTask(running);
      setRefreshKey((value) => value + 1);
      onTaskChanged?.();
    } catch (e) {
      setResumeError(e instanceof Error ? e.message : String(e));
    } finally {
      resumeLock.current = false;
      setSubmitting(false);
    }
  }

  async function copy(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopyMessage(`${label}已复制`);
    } catch {
      setCopyMessage("复制失败，请手动选择并复制文本");
    }
  }

  const command = `./deploy.sh novel-resume ${runId}`;
  const currentError = task?.resume_job_error || task?.error;
  const running = task ? isProjectTaskRunning(task.status) : false;

  return (
    <section aria-label="创作任务" className="mb-7 rounded-[24px] border border-[rgba(104,103,234,0.18)] bg-white/55 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold text-ink-text">创作任务</h2>
          <p className="mt-1.5 break-all text-[11px] text-ink-muted">任务 ID：<span className="font-mono">{runId}</span></p>
        </div>
        {task && <StatusPill status={task.status} label={projectTaskStatusLabel(task.status)} />}
      </div>

      {!task && !readError && <p className="mt-4 text-[13px] text-ink-muted">正在加载任务状态…</p>}
      {readError && (
        <div role="alert" className="mt-4 text-[13px] text-[#b84363]">
          <p>任务状态加载失败：{readError}</p>
          <button type="button" className="btn-ghost mt-2" onClick={() => setRefreshKey((value) => value + 1)}>重新加载</button>
        </div>
      )}

      {task && (
        <>
          <div className="mt-4 flex flex-wrap gap-x-7 gap-y-2 text-[12px] text-ink-muted">
            <p>当前阶段：<span className="font-medium text-ink-text">{projectTaskPhaseLabel(task.current_phase)}</span>{task.current_chapter != null && task.current_chapter > 0 ? ` · 第 ${task.current_chapter} 章` : ""}</p>
            <p>最近更新：{formatProjectTaskTime(task.updated_at) ?? "时间未知"}</p>
          </div>
          {running && <p role="status" className="mt-3 text-[12px] text-[var(--color-violet)]">任务运行中，状态将自动更新。</p>}
          {currentError && <TaskError error={currentError} title={running ? "上次失败原因" : task.resume_job_error ? "本次恢复失败原因" : "失败原因"} onCopy={copy} />}
          {task.resume_job_error && task.error && task.resume_job_error !== task.error && (
            <details className="mt-3 text-[12px] text-ink-muted">
              <summary className="cursor-pointer">查看任务此前的失败原因</summary>
              <TaskError error={task.error} title="此前的任务错误" onCopy={copy} />
            </details>
          )}
          {!currentError && task.status === "failed" && <p className="mt-4 text-[13px] text-[#b84363]">任务失败，但任务记录未提供错误详情。</p>}

          {task.failed_stages.length > 0 && (
            <details open={!running} className="mt-4 rounded-2xl border border-[rgba(74,91,133,0.12)] bg-white/55 px-4 py-3">
              <summary className="cursor-pointer text-[13px] font-medium text-ink-text">失败阶段记录（{task.failed_stages.length}）</summary>
              <ul className="mt-3 space-y-3">
                {task.failed_stages.map((stage, index) => (
                  <li key={`${stage.phase}:${stage.chapter}:${index}`} className="border-t border-[rgba(74,91,133,0.1)] pt-3 first:border-0 first:pt-0">
                    <p className="text-[13px] font-medium text-ink-text">{projectTaskPhaseLabel(stage.phase)}{stage.chapter != null && stage.chapter > 0 ? ` · 第 ${stage.chapter} 章` : ""}</p>
                    <p className="mt-1 text-[11px] text-ink-muted">开始：{formatProjectTaskTime(stage.started_at) ?? "时间未知"} · 结束：{formatProjectTaskTime(stage.finished_at) ?? "时间未知"}</p>
                    {stage.error && <TaskError error={stage.error} title="阶段错误" onCopy={copy} />}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {(task.can_resume || submitting) && (
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button type="button" className="btn-primary disabled:cursor-wait disabled:opacity-50" disabled={submitting} onClick={() => void resume()}>
                {submitting ? "恢复中…" : "恢复重试"}
              </button>
              <p className="max-w-xl text-[12px] leading-relaxed text-ink-muted">沿用原任务，校验并复用已完成且有效的阶段；无效产物将按任务规则修复或阻止继续。</p>
            </div>
          )}
          {resumeError && <p role="alert" className="mt-3 break-words text-[13px] text-[#b84363]">恢复提交失败：{resumeError}</p>}
          {task.can_resume && (
            <details className="mt-4 text-[12px] text-ink-muted">
              <summary className="cursor-pointer">在终端恢复此任务</summary>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <code className="min-w-0 break-all rounded-lg bg-white/80 px-3 py-2 text-ink-text">{command}</code>
                <button type="button" className="btn-ghost" onClick={() => void copy(command, "恢复命令")}>复制命令</button>
              </div>
            </details>
          )}
        </>
      )}
      {copyMessage && <p role="status" className="mt-2 text-[12px] text-ink-muted">{copyMessage}</p>}
    </section>
  );
}

function TaskError({ error, title, onCopy }: {
  error: string;
  title: string;
  onCopy: (text: string, label: string) => Promise<void>;
}) {
  return (
    <details open className="mt-3 rounded-xl bg-[#fff0f4]/70 px-3 py-2.5">
      <summary className="cursor-pointer text-[12px] font-medium text-[#b84363]">{title}</summary>
      <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink-text">{error}</p>
      <button type="button" className="mt-2 text-[11px] text-ink-muted hover:text-[var(--color-violet)]" onClick={() => void onCopy(error, "原始错误")}>复制原始错误</button>
    </details>
  );
}
