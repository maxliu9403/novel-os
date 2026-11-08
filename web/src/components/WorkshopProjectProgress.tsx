import { useEffect, useState } from "react";
import { api, type ProjectDetail } from "../api/client";
import { isProjectTaskRunning, projectTaskPhaseLabel, projectTaskStatusLabel, TASK_POLL_INTERVAL_MS } from "../lib/projectTasks";

/** Workshop launch receipts are historical. The project's durable run owns progress. */
export default function WorkshopProjectProgress({ projectId }: { projectId: string | null }) {
  return <ProjectProgress key={projectId} projectId={projectId} />;
}

function ProjectProgress({ projectId }: { projectId: string | null }) {
  const [state, setState] = useState<{ project?: ProjectDetail; error?: string }>({});
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    let timer: number;
    async function poll() {
      try {
        const project = await api.project(projectId!);
        if (!active) return;
        setState({ project });
        if (!isProjectTaskRunning(project.status) && project.status !== "draft") return;
      } catch (error) {
        if (!active) return;
        setState({ error: error instanceof Error ? error.message : String(error) });
      }
      if (active) timer = window.setTimeout(poll, TASK_POLL_INTERVAL_MS);
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [projectId]);
  const project = state.project;
  const running = !!project && isProjectTaskRunning(project.status);
  return <section aria-label="全书创作进度" className="mb-5 rounded-2xl border border-violet/25 bg-violet-soft p-4">
    <p role="status" className="text-[14px] font-semibold text-violet">{project
      ? `作品创作 · ${projectTaskStatusLabel(project.status)}`
      : !projectId || state.error ? "作品创作状态暂不可确认" : "正在读取作品创作状态…"}</p>
    {project?.task_id && <p className="mt-1 text-[12px] text-ink-muted">当前任务 {project.task_id}{project.task_phase && project.status !== "completed" ? ` · ${projectTaskPhaseLabel(project.task_phase)}` : ""}</p>}
    <p className="mt-1 text-[12px] text-ink-muted">{running ? "关闭窗口不会暂停当前任务。" : project ? "以作品中已保存的创作与交付结果为准。" : "本讨论的启动记录已保留，请打开作品核对最新结果。"}</p>
    {(state.error || project?.task_error) && <p role="alert" className="mt-2 text-[13px] text-ink-text">{state.error ? `暂时无法读取作品状态：${state.error}` : project?.task_error}</p>}
  </section>;
}
