import { useEffect, useRef, useState } from "react";
import { api, ApiError, type CoverGenerationTask, type CoverSet } from "../api/client";

const active = (task: CoverGenerationTask | null) => task?.status === "queued" || task?.status === "running";
const messageOf = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
type Snapshot = { owner: string; task: CoverGenerationTask | null; checking: boolean; connectionError: string; lastSyncedAt: number | null };
type Direction = {direction_id: string; approved_direction_sha256: string};
type Intent = {requestId: string; count: number; direction: Direction};
type Intents = Record<string, Intent>;
const storageKey = (owner: string) => `novel-os:cover-generation-intent:${owner}`;
const intentKey = (count: number, direction: Direction) => JSON.stringify([direction.direction_id, direction.approved_direction_sha256, count]);
function newRequestId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
}

function readIntents(owner: string): Intents {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey(owner)) || "{}");
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return {};
    return Object.fromEntries(Object.entries(saved).filter((entry): entry is [string, Intent] => {
      const value = entry[1] as Partial<Intent> | null;
      return Boolean(value && typeof value.requestId === "string" && Number.isInteger(value.count)
        && typeof value.direction?.direction_id === "string" && typeof value.direction?.approved_direction_sha256 === "string");
    }));
  } catch { return {}; }
}

function writeIntents(owner: string, intents: Intents) {
  try {
    if (Object.keys(intents).length) sessionStorage.setItem(storageKey(owner), JSON.stringify(intents));
    else sessionStorage.removeItem(storageKey(owner));
  } catch { /* The in-memory intent still protects retries when browser storage is unavailable. */ }
}

function pendingIntents(memory: Map<string, Intents>, owner: string) {
  if (!memory.has(owner)) memory.set(owner, readIntents(owner));
  return memory.get(owner)!;
}

function clearIntents(memory: Map<string, Intents>, owner: string, requestIds: string[]) {
  const pending = pendingIntents(memory, owner);
  for (const [key, intent] of Object.entries(pending)) {
    if (requestIds.includes(intent.requestId)) delete pending[key];
  }
  writeIntents(owner, pending);
}

/** Mounting only reads saved work; only start() can submit a new paid batch. */
export function useCoverGenerationTask(owner: string, onSets: (sets: CoverSet[], task: CoverGenerationTask, notify: boolean) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot>({owner: "", task: null, checking: true, connectionError: "", lastSyncedAt: null});
  const [revision, setRevision] = useState(0);
  const [submittingOwner, setSubmittingOwner] = useState("");
  const epoch = useRef(0);
  const submitting = useRef(new Set<string>());
  const observedActive = useRef(new Set<string>());
  const tracked = useRef<{owner: string; taskId: string} | null>(null);
  const intents = useRef(new Map<string, Intents>());

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const selected = ++epoch.current;
    if (tracked.current?.owner !== owner) tracked.current = null;
    const recover = async () => {
      let again = true;
      try {
        const taskId = tracked.current?.owner === owner ? tracked.current.taskId : undefined;
        const task = await api.coverGenerationTask(owner, taskId);
        if (!live || selected !== epoch.current) return;
        if (taskId && (!task || task.task_id !== taskId)) throw new Error("已提交任务的状态尚未确认，正在重新查询。");
        if (task) clearIntents(intents.current, owner, [task.request_id || "", ...(task.request_aliases || [])]);
        const lastSyncedAt = Date.now();
        if (active(task)) {
          tracked.current = {owner, taskId: task!.task_id};
          observedActive.current.add(task!.task_id);
        }
        // Publish confirmed terminal state before reading its saved images.
        // A failed image-list read must not turn completion back into running.
        setSnapshot({owner, task, checking: Boolean(task?.cover_set_id), connectionError: "", lastSyncedAt});
        if (task?.cover_set_id) {
          const sets = await api.covers(owner);
          if (!live || selected !== epoch.current) return;
          if (!sets.some(item => item.cover_set_id === task.cover_set_id)) throw new Error("任务已绑定的封面批次尚未同步，正在重新读取。");
          const notify = !active(task) && observedActive.current.delete(task.task_id);
          onSets(sets, task, notify);
        }
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
  }, [owner, revision, onSets]);

  const current = snapshot.owner === owner ? snapshot : {task: null, checking: true, connectionError: "", lastSyncedAt: null};
  const busy = current.checking || active(current.task) || submittingOwner === owner;
  const start = async (count: number, direction: Direction) => {
    if (busy || submitting.current.has(owner)) return;
    const pending = pendingIntents(intents.current, owner);
    const key = intentKey(count, direction);
    const intent = pending[key] || {requestId: newRequestId(), count, direction};
    pending[key] = intent;
    writeIntents(owner, pending);
    submitting.current.add(owner);
    const selected = epoch.current;
    setSubmittingOwner(owner);
    try {
      const job = await api.generateCovers(owner, count, direction, intent.requestId);
      if (selected !== epoch.current) return;
      const taskId = job.task_id || (typeof job.meta?.generation_task_id === "string" ? job.meta.generation_task_id : "");
      if (taskId) clearIntents(intents.current, owner, [intent.requestId]);
      tracked.current = taskId ? {owner, taskId} : null;
      if (taskId) observedActive.current.add(taskId);
      setSnapshot({owner, task: null, checking: true, connectionError: "", lastSyncedAt: null});
      setRevision(value => value + 1);
    } catch (cause) {
      if (selected !== epoch.current) return;
      // A rejected validation request has no accepted operation to recover.
      if (cause instanceof ApiError && [400, 404, 409, 422].includes(cause.status)) clearIntents(intents.current, owner, [intent.requestId]);
      // A lost response can still mean the server accepted the paid operation.
      // Discover latest before allowing another submission; never replay POST.
      tracked.current = null;
      setSnapshot(previous => ({owner, task: previous.owner === owner ? previous.task : null,
        checking: true, connectionError: messageOf(cause),
        lastSyncedAt: previous.owner === owner ? previous.lastSyncedAt : null}));
      setRevision(value => value + 1);
      throw cause;
    } finally {
      submitting.current.delete(owner);
      setSubmittingOwner(currentOwner => currentOwner === owner ? "" : currentOwner);
    }
  };
  return {...current, busy, submitting: submittingOwner === owner, start};
}
