import { useEffect, useRef, useState } from "react";
import { api, type CoverRepairTask, type CoverSet } from "../api/client";

const running = (task: CoverRepairTask | null) => task?.status === "planning" || task?.status === "generating";
const errorOf = (value: unknown) => value instanceof Error ? value.message : String(value);
type State = {owner: string; task: CoverRepairTask | null; checking: boolean; error: string; syncedAt: number | null};

/** All recovery is GET-only; planning and exact-proposal approval are explicit actions. */
export function useCoverRepairTask(owner: string, onSets: (sets: CoverSet[], task: CoverRepairTask, notify: boolean) => void) {
  const [state, setState] = useState<State>({owner: "", task: null, checking: true, error: "", syncedAt: null});
  const [revision, setRevision] = useState(0);
  const [submittingOwner, setSubmittingOwner] = useState("");
  const epoch = useRef(0);
  const locks = useRef(new Set<string>());
  const tracked = useRef<{owner: string; id: string} | null>(null);
  const observed = useRef(new Set<string>());

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const selected = ++epoch.current;
    if (tracked.current?.owner !== owner) tracked.current = null;
    async function recover() {
      let again = true;
      try {
        const id = tracked.current?.owner === owner ? tracked.current.id : undefined;
        const task = await api.coverRepairTask(owner, id);
        if (!live || selected !== epoch.current) return;
        if (id && (!task || task.task_id !== id)) throw new Error("单张修复任务尚未同步，正在重新查询。");
        const syncedAt = Date.now();
        if (task && (running(task) || task.status === "awaiting_approval")) tracked.current = {owner, id: task.task_id};
        if (task && running(task)) observed.current.add(task.task_id);
        setState({owner, task, checking: Boolean(task?.cover_set_id), error: "", syncedAt});
        if (task?.cover_set_id) {
          const sets = await api.covers(owner);
          if (!live || selected !== epoch.current) return;
          if (!sets.some(set => set.cover_set_id === task.cover_set_id)) throw new Error("修复后的候选批次尚未同步，正在重新读取。");
          onSets(sets, task, !running(task) && observed.current.delete(task.task_id));
        }
        setState({owner, task, checking: false, error: "", syncedAt});
        again = running(task);
      } catch (cause) {
        if (!live || selected !== epoch.current) return;
        setState(previous => ({owner, task: previous.owner === owner ? previous.task : null,
          checking: true, error: errorOf(cause), syncedAt: previous.owner === owner ? previous.syncedAt : null}));
      } finally {
        if (live && selected === epoch.current && again) timer = setTimeout(recover, 1500);
      }
    }
    void recover();
    return () => {live = false; clearTimeout(timer); epoch.current += 1;};
  }, [owner, revision, onSets]);

  const current = state.owner === owner ? state : {task: null, checking: true, error: "", syncedAt: null};
  const busy = current.checking || running(current.task) || submittingOwner === owner;
  const submit = async (operation: () => Promise<CoverRepairTask>) => {
    if (busy || locks.current.has(owner)) return;
    const selected = epoch.current;
    locks.current.add(owner);
    setSubmittingOwner(owner);
    try {
      const task = await operation();
      if (selected !== epoch.current) return;
      if (!task.task_id) throw new Error("服务器未返回可恢复的修复任务，请等待状态确认。");
      tracked.current = {owner, id: task.task_id};
      observed.current.add(task.task_id);
      setState({owner, task, checking: true, error: "", syncedAt: Date.now()});
      setRevision(value => value + 1);
      return task;
    } catch (cause) {
      if (selected !== epoch.current) return;
      // The POST may have been accepted. Recover, never repeat it automatically.
      tracked.current = null;
      setState(previous => ({owner, task: previous.owner === owner ? previous.task : null,
        checking: true, error: errorOf(cause), syncedAt: previous.owner === owner ? previous.syncedAt : null}));
      setRevision(value => value + 1);
      throw cause;
    } finally {
      locks.current.delete(owner);
      setSubmittingOwner(previous => previous === owner ? "" : previous);
    }
  };
  const start = (setId: string, candidateId: string, expectedRevision: number, refinementPrompt?: string) =>
    submit(() => refinementPrompt === undefined
      ? api.startCoverRepair(owner, setId, candidateId, expectedRevision)
      : api.startCoverRepair(owner, setId, candidateId, expectedRevision, refinementPrompt));
  const approve = () => {
    const task = current.task;
    if (!task || task.status !== "awaiting_approval" || !task.proposal_sha256 || !task.proposal) return Promise.resolve();
    return submit(() => api.approveCoverRepair(owner, task.task_id, task.proposal_sha256!, task.image_calls));
  };
  const retry = (taskId: string) => submit(() => api.retryCoverRefinement(owner, taskId));
  return {...current, busy, submitting: submittingOwner === owner, start, approve, retry};
}
