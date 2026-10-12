import { useEffect, useRef, useState } from "react";
import { api, type CoverDirection, type CoverDirectionTask } from "../api/client";

const active = (task: CoverDirectionTask | null) => task?.status === "queued" || task?.status === "running";
const messageOf = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
type Snapshot = { owner: string; task: CoverDirectionTask | null; checking: boolean; connectionError: string; lastSyncedAt: number | null };

/** The server owns the operation; mounting only subscribes and never starts work. */
export function useCoverDirectionTask(owner: string, onComplete: (directions: CoverDirection[], notify: boolean) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot>({owner: "", task: null, checking: true, connectionError: "", lastSyncedAt: null});
  const [revision, setRevision] = useState(0);
  const [submittingOwner, setSubmittingOwner] = useState("");
  const epoch = useRef(0);
  const submitting = useRef(new Set<string>());
  const observedActive = useRef(new Set<string>());

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const selected = ++epoch.current;
    const recover = async () => {
      let again = true;
      try {
        const task = await api.coverDirectionTask(owner);
        if (!live || selected !== epoch.current) return;
        const lastSyncedAt = Date.now();
        if (active(task)) observedActive.current.add(task!.task_id);
        if (task?.status === "completed") {
          // Completion is confirmed even if fetching its saved plans later
          // fails. Keep actions locked until that second read is reconciled.
          setSnapshot({owner, task, checking: true, connectionError: "", lastSyncedAt});
          const directions = await api.coverDirections(owner);
          if (!live || selected !== epoch.current) return;
          onComplete(directions, observedActive.current.delete(task.task_id));
        }
        // This is the time the browser confirmed task state, not model progress.
        // The server's updated_at may remain unchanged during a long call.
        setSnapshot({owner, task, checking: false, connectionError: "", lastSyncedAt});
        again = active(task);
      } catch (cause) {
        if (!live || selected !== epoch.current) return;
        setSnapshot(current => ({owner, task: current.owner === owner ? current.task : null,
          checking: true, connectionError: messageOf(cause),
          lastSyncedAt: current.owner === owner ? current.lastSyncedAt : null}));
      } finally {
        if (live && selected === epoch.current && again) timer = setTimeout(recover, 1500);
      }
    };
    void recover();
    return () => { live = false; clearTimeout(timer); epoch.current += 1; };
  }, [owner, revision, onComplete]);

  const current = snapshot.owner === owner ? snapshot : {task: null, checking: true, connectionError: "", lastSyncedAt: null};
  const busy = current.checking || active(current.task) || submittingOwner === owner;
  const start = async (count: number, analysisId = "", freshness = "require_current_head") => {
    if (busy || submitting.current.has(owner)) return;
    submitting.current.add(owner);
    const selected = epoch.current;
    setSubmittingOwner(owner);
    try {
      const task = await api.startCoverDirectionTask(owner, count, analysisId, freshness);
      if (selected !== epoch.current) return;
      if (active(task)) observedActive.current.add(task.task_id);
      // Reconcile even an immediately completed task before enabling approval or generation.
      setSnapshot({owner, task, checking: true, connectionError: "", lastSyncedAt: Date.now()});
      setRevision(value => value + 1);
    } catch (cause) {
      if (selected !== epoch.current) return;
      // A dropped POST response does not prove the worker failed to start.
      // Read its durable record before allowing another submission.
      setSnapshot(previous => ({owner, task: previous.owner === owner ? previous.task : null,
        checking: true, connectionError: messageOf(cause),
        lastSyncedAt: previous.owner === owner ? previous.lastSyncedAt : null}));
      setRevision(value => value + 1);
      throw cause;
    } finally {
      submitting.current.delete(owner);
      setSubmittingOwner(current => current === owner ? "" : current);
    }
  };
  return {task: current.task, checking: current.checking, connectionError: current.connectionError,
    lastSyncedAt: current.lastSyncedAt, busy, start};
}
