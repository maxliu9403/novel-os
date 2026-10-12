import { useEffect, useState } from "react";
import { api, type CoverRepairTask, type CoverSet } from "../api/client";
import { matchesRefinementTask, refinementReferenceUrl } from "../lib/coverRepair";
import Icon from "./Icon";

/** Historical cards resolve their exact task with GET; opening a card never retries it. */
export default function CoverRefinementRetryActions({owner, setId, candidateId, number, knownTask, sets, disabled, onRetry, onReplan}: {
  owner: string; setId: string; candidateId: string; number: number; knownTask: CoverRepairTask | null;
  sets: CoverSet[]; disabled: boolean; onRetry: (task: CoverRepairTask) => void; onReplan: (task: CoverRepairTask) => void;
}) {
  const key = `${owner}:${setId}:${candidateId}`;
  const known = matchesRefinementTask(knownTask, setId, candidateId) ? knownTask : null;
  const [read, setRead] = useState<{key: string; task: CoverRepairTask | null; error: string} | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (known) return;
    let live = true;
    api.coverRepairTaskForCandidate(owner, setId, candidateId).then(task => {
      if (!live) return;
      const valid = matchesRefinementTask(task, setId, candidateId);
      setRead({key, task: valid ? task : null, error: valid ? "" : "未找到这张图对应的精修任务，无法安全恢复原图与方案。"});
    }).catch(error => {
      if (live) setRead({key, task: null, error: `暂时无法读取精修任务：${error instanceof Error ? error.message : String(error)}`});
    });
    return () => {live = false;};
  }, [owner, setId, candidateId, key, known, revision]);
  const current = read?.key === key ? read : null;
  const resolved = known || current?.task;
  const successor = resolved && knownTask?.kind === "refinement" && knownTask.retry_of_task_id === resolved.task_id
    && knownTask.parent_cover_set_id === resolved.parent_cover_set_id && knownTask.parent_candidate_id === resolved.parent_candidate_id
    ? knownTask : null;
  const task = successor || resolved;
  const original = sets.find(set => set.cover_set_id === task?.parent_cover_set_id)?.candidates.find(candidate => candidate.candidate_id === task?.parent_candidate_id);
  const stopped = task?.status === "failed" || task?.status === "interrupted";
  const reason = !task ? current?.error || "正在读取这张图的精修任务…"
    : !stopped ? "该精修任务已有后续状态，请查看上方任务进度。"
      : !refinementReferenceUrl(task, original) ? "原始参考图片暂不可用，无法重试精修。请恢复原图后重新查询。" : "";
  return <div className="cover-refinement-retry">
    <p className="cover-repair__note">精修未完成。原图保留，重试前需要重新确认 1 次图片编辑调用。</p>
    <div className="cover-refinement-retry__actions">
      <button type="button" className="btn-secondary" disabled={disabled || Boolean(reason)}
        aria-label={`重试精修候选图 ${number}`} aria-describedby={`refinement-retry-${candidateId}`}
        onClick={() => task && onRetry(task)}><Icon name="history" className="h-3.5 w-3.5" />重试精修</button>
      <button type="button" className="btn-ghost" disabled={disabled || Boolean(reason)}
        aria-label={`修改候选图 ${number} 的精修要求并重新规划`} onClick={() => task && onReplan(task)}>修改要求并重新规划</button>
      {!known && current?.error && <button type="button" className="btn-ghost" disabled={disabled}
        onClick={() => {setRead(null); setRevision(value => value + 1);}}>重新查询精修任务</button>}
    </div>
    <p id={`refinement-retry-${candidateId}`} className="cover-repair__note">{reason || "沿用原始参考图片与已保存的优化要求，不会自动出图。"}</p>
  </div>;
}
