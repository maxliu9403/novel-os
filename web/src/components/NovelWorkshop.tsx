import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type JobStatus } from "../api/client";
import { workshopApi, type NarrativeMode, type WorkshopInput, type WorkshopRuntimeProgress, type WorkshopSession, type WorkshopTurnIntent } from "../api/workshop";
import { GENRE_OPTIONS, genreLabel, mergeGenres } from "../lib/genres";
import { Field, fieldClass, textareaClass } from "./Modal";
import GenreChips from "./GenreChips";
import Icon from "./Icon";
import WorkshopSessions from "./WorkshopSessions";
import GenerationCostPanel from "./GenerationCostPanel";
import { DELIVERY_STAGE_LABELS } from "../lib/workshopProgress";
import { WorkshopDeliveryCheckpoints, WorkshopRuntimeStatus } from "./WorkshopProgress";

export interface WorkshopSeed { title: string; author: string; genres: string[]; premise: string; method_mode?: "off" | "advisory" }

const PRIMARY = "btn-primary !bg-violet hover:!bg-[#5d5bd2] !shadow-[0_8px_20px_rgba(104,103,234,0.18)]";
const CACHE_KEY = "novel-os.workshop.active-session";
const MODES: Array<[NarrativeMode, string]> = [
  ["short_novel", "短篇 · 连续章节"], ["standalone_long", "长篇 · 连续章节"],
  ["multi_volume", "单本分卷 · 章号连续"], ["series_installment", "多本系列 · 当前分册"],
];
const INITIAL_MESSAGE = "请基于我的基础提示开始头脑风暴。保留已明确的设定，先补问关键缺项，提出可选择的故事方向；持续完善人物心理、章节因果、伏笔和钩子。篇幅允许合理浮动。";
const DELIVERY_MESSAGE = "请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。";
const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error);
const titleOf = (session: WorkshopSession) => session.prompt_md.match(/^\s*(?:[-*]\s*)?(?:\*\*)?Title(?:\*\*)?\s*:[ \t]*(.+)$/im)?.[1].trim() || session.input.title || "未命名故事";

function timestamp(value?: string | null) {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

const PHASE_LABELS: Record<WorkshopRuntimeProgress["phase"], string> = {
  preparing: "正在准备本轮讨论", starting: "正在启动模型请求", waiting: "等待模型回复",
  receiving: "正在接收回复", parsing: "正在整理回复", validating: "正在检查骨架完整性",
  repairing: "正在修复回复格式", delivery: "正在准备骨架交付", completed: "本轮讨论已完成", failed: "本轮请求未完成", cancelled: "本轮请求已暂停",
};

function remember(id: string) {
  try { localStorage.setItem(CACHE_KEY, id); } catch { /* A session also stays in component state. */ }
}

export default function NovelWorkshop({ open, seed, onClose, onBack, initialSessionId, onSessionChange, onCreateNew }: {
  open: boolean; seed: WorkshopSeed; onClose: () => void; onBack: () => void; initialSessionId?: string;
  onSessionChange?: (id: string) => void; onCreateNew?: () => void;
}) {
  const navigate = useNavigate();
  const [input, setInput] = useState<WorkshopInput>({ ...seed, language: "", mode: "", audience: "", market: "", constraints: "" });
  const [otherGenre, setOtherGenre] = useState("");
  const [session, setSession] = useState<WorkshopSession | null>(null);
  const [appliedSeed, setAppliedSeed] = useState(seed);
  const [saved, setSaved] = useState<WorkshopSession | null>(null);
  const [step, setStep] = useState<"details" | "discussion" | "preview" | "sessions">("details");
  const [showSessions, setShowSessions] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<"create" | "send" | "prepare" | "launch" | "restore" | "titles" | "input" | "pause" | "resume" | "restart" | null>(initialSessionId ? "restore" : null);
  const [editingBasics, setEditingBasics] = useState(false);
  const [restartChoice, setRestartChoice] = useState(true);
  const [namingDrafts, setNamingDrafts] = useState<Record<string, { title: string; hints: string }>>({});
  const namingDraft = session ? namingDrafts[session.id] ?? { title: session.naming?.confirmed_title ?? String(session.prepared?.validation.title ?? ""), hints: "" } : { title: "", hints: "" };
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [acceptedTurn, setAcceptedTurn] = useState<{ sessionId: string; jobId: string; priorLogCount: number; startedAt: number | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const [runJob, setRunJob] = useState<JobStatus | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [model, setModel] = useState<string>("在模型设置中独立配置");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const dialog = useRef<HTMLDivElement>(null);
  const latestReply = useRef<HTMLElement>(null);
  const sessionDrafts = useRef(new Map<string, string>());
  const sessionGeneration = useRef(0);
  const mounted = useRef(true);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);

  if (seed !== appliedSeed) {
    setAppliedSeed(seed);
    if (!session) {
      setInput((current) => ({ ...current, ...seed }));
      setOtherGenre("");
    }
  }

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let current = true;
    try {
      const id = initialSessionId || localStorage.getItem(CACHE_KEY);
      if (id) workshopApi.get(id).then((value) => {
        if (!current) return;
        setSaved(value);
        if (initialSessionId) {
          setSession(value); remember(value.id); setBusy(null);
          setStep(value.restart_required ? "details" : value.status === "launched" || value.active_operation === "titles" || value.naming?.source_sha256 ? "preview" : "discussion");
          setInput(value.input);
        }
      }).catch((failure: unknown) => {
        if (current && initialSessionId) { setError(`无法恢复这次讨论：${messageOf(failure)}`); setBusy(null); }
      });
    } catch { /* Browser storage may be unavailable. */ }
    const refreshModel = () => {
      api.studioModels().then((config) => {
        const route = config.text_routes.find((value) => value.id === "workshop");
        if (current && route) {
          const label = route.configured
            ? `${route.effective_model}${route.inherits_default ? " · 继承默认，可单独更改" : " · 独立配置"}`
            : "尚未配置，请在模型设置中连接";
          setModel(`${label} · 配置的单次请求时限 ${(route.effective_timeout_seconds ?? 900) / 60} 分钟`);
        }
      }).catch(() => undefined);
    };
    refreshModel();
    window.addEventListener("focus", refreshModel);
    return () => { current = false; window.removeEventListener("focus", refreshModel); };
  }, [initialSessionId]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
      if (event.key !== "Tab") return;
      const controls = dialog.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
      );
      if (!controls?.length) return;
      const visible = Array.from(controls).filter((element) => element.getClientRects().length > 0);
      const first = visible[0];
      const last = visible[visible.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      document.body.style.overflow = overflow;
      window.removeEventListener("keydown", handleKey);
      previous?.focus();
    };
  }, [open]);

  const running = session?.status === "running";
  const paused = session?.status === "paused";
  const restoring = busy === "restore";
  const controlling = busy === "pause" || busy === "resume";
  const pollingBlocked = restoring || controlling;
  const sessionId = session?.id;
  const jobId = session?.job_id;
  const latestStartIndex = session?.logs.findLastIndex((entry) => entry.event === "turn_started") ?? -1;
  const recordedProgress = session?.runtime_progress;
  const runtimeProgress = recordedProgress && recordedProgress.revision === session?.revision
    && recordedProgress.job_id === jobId && recordedProgress.operation === (session?.active_operation ?? "brainstorm")
    ? recordedProgress : null;
  const deliveryProgress = !session?.restart_required && session?.active_operation !== "titles" ? session?.delivery_progress : null;
  const resumableDelivery = Boolean(deliveryProgress?.resumable && (paused || session?.status === "error"));
  const savedAssembly = deliveryProgress?.completed_stages.includes("assembly");
  const savedAssemblyParts = (deliveryProgress?.assembly?.completed_parts ?? 0) > 0;
  const deliveryResumeLabel = savedAssembly ? "完成骨架保存" : savedAssemblyParts ? "从已保存部分继续" : deliveryProgress?.completed_stages.length ? "从已保存阶段继续" : "继续骨架交付";
  const newTurn = acceptedTurn && acceptedTurn.sessionId === sessionId && acceptedTurn.jobId === jobId ? acceptedTurn : null;
  // The optimistic session after submit still has the previous round's logs.
  // Trust those logs only once a newly appended turn_started is present.
  const awaitingStartLog = Boolean(newTurn && latestStartIndex < newTurn.priorLogCount);
  const startedAt = timestamp(runtimeProgress?.started_at) ?? (awaitingStartLog ? newTurn?.startedAt ?? null
    : timestamp(session?.logs[latestStartIndex]?.time));
  const pausedAt = paused ? timestamp(session?.paused_at) ?? timestamp(session?.logs.findLast((entry) => entry.event === "turn_paused")?.time) : null;
  const elapsedUntil = paused ? pausedAt : clock;
  const elapsed = startedAt !== null && elapsedUntil !== null && elapsedUntil >= startedAt ? Math.floor((elapsedUntil - startedAt) / 1000) : null;

  useEffect(() => {
    if (!open || !running) return;
    const updateClock = () => setClock(Date.now());
    const immediate = window.setTimeout(updateClock, 0);
    const tick = window.setInterval(updateClock, 1000);
    return () => { window.clearTimeout(immediate); window.clearInterval(tick); };
  }, [open, running, sessionId, jobId]);
  useEffect(() => {
    if (!open || !running || !sessionId || !jobId || pollingBlocked) return;
    let active = true;
    const generation = sessionGeneration.current;
    let timer: number;
    async function poll() {
      try {
        const [jobResult, sessionResult] = await Promise.allSettled([api.getJob(jobId!), workshopApi.get(sessionId!)]);
        if (!active || generation !== sessionGeneration.current) return;
        // Sessions are durable; JobRunner records may disappear after a server
        // restart. Always apply the recovered session even when the job is 404.
        if (sessionResult.status === "rejected") throw sessionResult.reason;
        const current = sessionResult.value;
        const job = jobResult.status === "fulfilled" ? jobResult.value : null;
        setConnectionError(null);
        if (job?.status === "error" && job.job_id === current.job_id && current.status === "running") {
          setSession({ ...current, status: "error", last_error: job.error || "本轮推导未完成，请重试。" });
        } else {
          setSession(current);
        }
        if (current.status !== "running") return;
      } catch (failure) {
        if (active && generation === sessionGeneration.current) setConnectionError(`状态连接中断：${messageOf(failure)}。正在重新连接，已保存的讨论上下文不会丢失。`);
      }
      if (active && generation === sessionGeneration.current) timer = window.setTimeout(poll, 1500);
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [open, running, sessionId, jobId, pollingBlocked]);

  const launched = session?.status === "launched";
  useEffect(() => {
    if (!open || !launched || !jobId || pollingBlocked) return;
    let active = true;
    const generation = sessionGeneration.current;
    let timer: number;
    async function pollRun() {
      try {
        const job = await api.getJob(jobId!);
        if (!active || generation !== sessionGeneration.current) return;
        setRunJob(job); setRunError(null);
        if (job.status !== "running") return;
      } catch (failure) {
        if (active && generation === sessionGeneration.current) setRunError(`暂时无法读取创作任务状态：${messageOf(failure)}。可打开作品查看最新进度。`);
      }
      if (active && generation === sessionGeneration.current) timer = window.setTimeout(pollRun, 2500);
    }
    void pollRun();
    return () => { active = false; window.clearTimeout(timer); };
  }, [open, launched, jobId, pollingBlocked]);

  async function submitTurn(current: WorkshopSession, message: string, intent: WorkshopTurnIntent) {
    setBusy("send");
    setError(null);
    // Keep the chosen action locally until the durable session refreshes. If a
    // response is lost, retry still follows the author's discussion/delivery choice.
    setSession((value) => value?.id === current.id ? { ...value, turn_intent: intent } : value);
    try {
      const job = await workshopApi.turn(current.id, message, current.revision, intent);
      if (!mounted.current) return;
      setClock(Date.now());
      setAcceptedTurn({ sessionId: current.id, jobId: job.job_id, priorLogCount: current.logs.length, startedAt: timestamp(job.started_at) });
      setSession({ ...current, status: "running", active_operation: "brainstorm", turn_intent: intent, job_id: job.job_id, prepared: null, last_error: null,
        delivery_progress: null,
        naming: current.naming ? { ...current.naming, source_sha256: "", confirmed_title: null, candidates: [] } : null });
      setNamingDrafts((values) => ({ ...values, [current.id]: { title: "", hints: "" } }));
      setDraft("");
      setStep("discussion");
    } catch (failure) {
      if (!mounted.current) return;
      setError(messageOf(failure));
      // A version conflict or lost response must not make the next request use
      // a stale revision; retain any unsent local text while refreshing state.
      try { setSession(await workshopApi.get(current.id)); } catch { /* Retain current draft. */ }
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  async function controlRequest(action: "pause" | "resume") {
    if (!session || busy || (action === "pause" ? !running || !session.job_id : !paused && !resumableDelivery)) return;
    const current = session;
    // Invalidate outstanding polls before the control request, not after React
    // commits the paused state, so a late response cannot undo the user's action.
    const generation = ++sessionGeneration.current;
    setBusy(action); setError(null); setConnectionError(null);
    try {
      if (action === "pause") {
        const updated = await workshopApi.pause(current.id, current.revision, current.job_id!);
        if (!mounted.current || generation !== sessionGeneration.current) return;
        setSession(updated); setClock(Date.now());
      } else {
        const job = await workshopApi.resume(current.id, current.revision);
        if (!mounted.current || generation !== sessionGeneration.current) return;
        setClock(Date.now());
        setAcceptedTurn({ sessionId: current.id, jobId: job.job_id, priorLogCount: current.logs.length, startedAt: timestamp(job.started_at) });
        setSession({ ...current, status: "running", job_id: job.job_id, last_error: null, paused_at: null });
        setStep(current.active_operation === "titles" ? "preview" : "discussion");
      }
    } catch (failure) {
      if (!mounted.current || generation !== sessionGeneration.current) return;
      setError(messageOf(failure));
      try {
        const updated = await workshopApi.get(current.id);
        if (mounted.current && generation === sessionGeneration.current) { setSession(updated); setClock(Date.now()); }
      } catch { /* Retain saved content and allow the user to retry. */ }
    } finally {
      if (mounted.current && generation === sessionGeneration.current) setBusy(null);
    }
  }

  async function restoreSession(id: string) {
    if (busy) return;
    const generation = ++sessionGeneration.current;
    if (session) sessionDrafts.current.set(session.id, draft);
    setBusy("restore"); setError(null);
    try {
      const current = await workshopApi.get(id);
      if (!mounted.current || generation !== sessionGeneration.current) return;
      setSession(current); setSaved(current); remember(current.id);
      setDraft(sessionDrafts.current.get(current.id) ?? "");
      setConnectionError(null); setRunError(null); setRunJob(null); setCopied(false);
      setClock(Date.now());
      setEditingBasics(false);
      setRestartChoice(true); setInput(current.input);
      setStep(current.restart_required ? "details" : current.status === "launched" || current.active_operation === "titles" || current.naming?.source_sha256 ? "preview" : "discussion");
      onSessionChange?.(current.id);
    } catch (failure) {
      if (mounted.current) setError(`无法恢复这次讨论：${messageOf(failure)}`);
    } finally { if (mounted.current) setBusy(null); }
  }

  function chooseSession() {
    if (session) sessionDrafts.current.set(session.id, draft);
    setStep("sessions"); setError(null);
  }

  function newSession() {
    sessionGeneration.current++;
    if (session) sessionDrafts.current.set(session.id, draft);
    setSession(null); setDraft(""); setError(null); setConnectionError(null);
    setRunError(null); setRunJob(null); setCopied(false); setShowSessions(false);
    setAcceptedTurn(null);
    setEditingBasics(false);
    setInput({ title: "", author: "", genres: [], premise: "", language: "", mode: "", audience: "", market: "", constraints: "", method_mode: seed.method_mode });
    setOtherGenre("");
    setStep("details");
    onCreateNew?.();
  }

  async function start(event: FormEvent) {
    event.preventDefault();
    if (busy || !input.premise.trim()) return;
    setBusy("create"); setError(null);
    try {
      const current = await workshopApi.create({ ...input, genres: mergeGenres(input.genres, otherGenre), title: input.title.trim(), premise: input.premise.trim() });
      if (!mounted.current) return;
      remember(current.id); setSaved(null); setSession(current); setStep("discussion");
      await submitTurn(current, INITIAL_MESSAGE, "discuss");
      if (mounted.current) onSessionChange?.(current.id);
    } catch (failure) {
      if (mounted.current) { setError(messageOf(failure)); setBusy(null); }
    }
  }

  function showBasics() {
    if (session) { setInput(session.input); setOtherGenre(""); }
    setEditingBasics(false); setStep("details");
  }

  async function saveBasics(event: FormEvent) {
    event.preventDefault();
    if (!session || running || busy || launched || !editingBasics) return;
    const generation = sessionGeneration.current;
    setBusy("input"); setError(null);
    try {
      const current = await workshopApi.updateInput(session.id, session.revision,
        { ...input, genres: mergeGenres(input.genres, otherGenre), premise: input.premise.trim(), title: input.title.trim() });
      if (!mounted.current) return;
      if (generation !== sessionGeneration.current) return;
      setSession(current); setEditingBasics(false); setRestartChoice(true); setStep("details");
      setNamingDrafts((drafts) => ({ ...drafts, [current.id]: { title: "", hints: "" } }));
    } catch (failure) {
      if (mounted.current) setError(messageOf(failure));
      try { const current = await workshopApi.get(session.id); if (mounted.current && generation === sessionGeneration.current) setSession(current); } catch { /* Keep edits for retry. */ }
    }
    finally { if (mounted.current) setBusy(null); }
  }

  async function restartBasics() {
    if (!session || !session.restart_required || busy || running || launched) return;
    const current = session;
    const generation = ++sessionGeneration.current;
    setBusy("restart"); setError(null); setConnectionError(null);
    try {
      const job = await workshopApi.restart(current.id, current.revision);
      if (!mounted.current || generation !== sessionGeneration.current) return;
      setSession({ ...current, revision: current.revision + 1, status: "running", job_id: job.job_id,
        prompt_md: "", messages: [], questions: [], logs: [], prepared: null, naming: null, runtime_progress: null, delivery_progress: null,
        restart_required: false, initial_input: current.input, ready_for_confirmation: false, last_error: null });
      setAcceptedTurn({ sessionId: current.id, jobId: job.job_id, priorLogCount: 0, startedAt: timestamp(job.started_at) });
      setDraft(""); setStep("discussion"); setClock(Date.now()); setExpanded({});
    } catch (failure) {
      if (!mounted.current || generation !== sessionGeneration.current) return;
      setError(messageOf(failure));
      try {
        const recovered = await workshopApi.get(current.id);
        if (mounted.current && generation === sessionGeneration.current) {
          setSession(recovered);
          if (!recovered.restart_required) { setStep("discussion"); setDraft(""); }
        }
      } catch { /* Keep the decision; a retry is guarded by the exact saved revision. */ }
    } finally { if (mounted.current && generation === sessionGeneration.current) setBusy(null); }
  }

  async function suggestTitles() {
    if (!session || running || busy || launched) return;
    const generation = sessionGeneration.current;
    setBusy("titles"); setError(null); setStep("preview");
    try {
      const job = await workshopApi.titles(session.id, session.revision, namingDraft.hints, namingDraft.title);
      if (!mounted.current || generation !== sessionGeneration.current) return;
      setClock(Date.now());
      setAcceptedTurn({ sessionId: session.id, jobId: job.job_id, priorLogCount: session.logs.length, startedAt: timestamp(job.started_at) });
      setSession({ ...session, status: "running", active_operation: "titles", job_id: job.job_id, prepared: null, last_error: null });
    } catch (failure) {
      if (mounted.current) setError(messageOf(failure));
      try { const current = await workshopApi.get(session.id); if (mounted.current && generation === sessionGeneration.current) setSession(current); } catch { /* Keep saved outline. */ }
    } finally { if (mounted.current) setBusy(null); }
  }

  async function prepare() {
    if (!session || running || busy) return;
    const generation = sessionGeneration.current;
    setBusy("prepare"); setError(null);
    try {
      const current = await workshopApi.prepare(session.id, session.revision, namingDraft.title.trim());
      if (mounted.current) { setSession(current); setCopied(false); }
    } catch (failure) {
      if (mounted.current) setError(messageOf(failure));
      try { const current = await workshopApi.get(session.id); if (mounted.current && generation === sessionGeneration.current) setSession(current); } catch { /* Keep chosen title for retry. */ }
    } finally { if (mounted.current) setBusy(null); }
  }

  async function launch() {
    if (!session?.prepared || session.prepared.revision !== session.revision || busy || running) return;
    setBusy("launch"); setError(null);
    try {
      const result = await workshopApi.launch(session.id, session.revision);
      if (!mounted.current) return;
      setSession({ ...session, status: "launched", project_id: result.project_id, job_id: result.job_id });
      setRunJob({ job_id: result.job_id, status: "running", kind: "workshop_launch", error: null });
      remember(session.id);
    } catch (failure) {
      if (mounted.current) setError(messageOf(failure));
    } finally { if (mounted.current) setBusy(null); }
  }

  async function copyCommand() {
    if (!session?.prepared) return;
    try { await navigator.clipboard.writeText(session.prepared.command); setCopied(true); }
    catch { setError("复制未成功，请在下方选中并复制完整命令。"); }
  }

  if (!open) return null;
  const customGenres = input.genres.filter((genre) => !GENRE_OPTIONS.some((option) => option.toLowerCase() === genre.toLowerCase()));
  const disabled = Boolean(busy || running || launched);
  const storedPrepared = session?.prepared?.revision === session?.revision ? session?.prepared : null;
  const titleChanged = Boolean(storedPrepared && namingDraft.title.trim() !== String(storedPrepared.validation.title ?? session?.naming?.confirmed_title ?? ""));
  const prepared = titleChanged && !launched ? null : storedPrepared;
  const namingStage = Boolean(session?.naming?.source_sha256 || storedPrepared || session?.active_operation === "titles");
  function changeNaming(patch: Partial<{ title: string; hints: string }>) {
    if (session) setNamingDrafts((values) => ({ ...values, [session.id]: { ...namingDraft, ...patch } }));
  }
  const canPrepare = Boolean(session?.ready_for_confirmation && !session.questions.length);
  const validBasics = Boolean(session?.input.premise.trim());
  const canDeliver = Boolean(session && validBasics && !disabled && !session.restart_required);
  const deliveryLabel = session?.prompt_md.trim() ? "更新完整骨架" : "生成完整骨架";
  const requestDelivery = () => {
    if (!session || !canDeliver) return;
    const addition = draft.trim();
    const message = addition ? `${DELIVERY_MESSAGE}\n\n请同时纳入这条尚未发送的补充：\n${addition}` : DELIVERY_MESSAGE;
    void submitTurn(session, message, "deliver");
  };
  const basicPhaseLabel = runtimeProgress ? PHASE_LABELS[runtimeProgress.phase] : "等待模型回复";
  const phaseLabel = runtimeProgress?.delivery_stage ? `${DELIVERY_STAGE_LABELS[runtimeProgress.delivery_stage]} · ${basicPhaseLabel}` : basicPhaseLabel;
  const progressAction = runtimeProgress?.repairing_response && ["preparing", "starting", "waiting", "receiving"].includes(runtimeProgress.phase)
    ? `正在修复回复格式 · ${phaseLabel}` : phaseLabel;
  const progressLabel = running
    ? elapsed !== null ? `${progressAction} · 已用时 ${elapsed} 秒`
      : awaitingStartLog ? `${progressAction} · 正在同步开始时间` : `${progressAction} · 开始时间暂不可用`
    : busy === "send" ? "正在提交本轮想法…" : null;
  const lastAssistant = session?.messages.findLastIndex((message) => message.role === "assistant") ?? -1;
  const standaloneQuestions = Boolean(session?.questions.length && !session.messages[lastAssistant]?.questions?.length);
  const inputKey = `${sessionId}:input`;
  const questionKey = `${sessionId}:questions:${JSON.stringify(session?.questions ?? [])}`;
  const messageKey = (index: number) => `${sessionId}:message:${index}`;
  const toggle = (key: string, current: boolean) => setExpanded((values) => ({ ...values, [key]: !current }));
  function expandAll(value: boolean) {
    if (!session) return;
    const keys = [inputKey, questionKey, ...session.messages.map((_, index) => messageKey(index))];
    setExpanded((values) => ({ ...values, ...Object.fromEntries(keys.map((key) => [key, value])) }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4">
      <div className="absolute inset-0 bg-[#0c3bb8]/35 backdrop-blur-[6px]" aria-hidden="true" onClick={onClose} />
      <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="novel-workshop-title"
        className="glass-shell relative flex h-[calc(100dvh-16px)] w-full min-w-0 max-w-[1600px] flex-col overflow-hidden p-1.5 outline-none sm:h-[calc(100dvh-32px)] sm:p-2">
        {/* A single mobile scroll surface keeps stacked progress from clipping the conversation and composer. */}
        <div className="glass-panel flex min-h-0 flex-1 flex-col overflow-y-auto sm:overflow-hidden">
          <header className="flex shrink-0 flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-paper-line px-4 py-3 sm:px-8">
            <div className="min-w-0 flex-1 basis-full sm:basis-auto">
              <p className="mb-0.5 text-[11px] font-medium tracking-[0.12em] text-violet">新作品 · 创作准备</p>
              <h2 id="novel-workshop-title" className="text-[23px] font-semibold tracking-tight text-ink-text">一起把故事想完整</h2>
              <p className="mt-0.5 text-[12px] text-ink-muted">反复讨论，完善骨架，再启动全书创作。</p>
              {session && step !== "details" && step !== "sessions" && <p className="mt-1 break-words text-[12px] text-ink-muted">{titleOf(session)} · 任务 ID <code title={session.id}>{session.id.slice(0, 12)}</code></p>}
            </div>
            <div className="ml-auto flex shrink-0 flex-wrap justify-end gap-1">
              {session && step !== "sessions" && <button type="button" onClick={chooseSession} disabled={Boolean(busy)} className="btn-ghost !text-violet">切换讨论</button>}
              <button type="button" onClick={onClose} className="btn-ghost" aria-label="关闭头脑风暴">关闭</button>
            </div>
          </header>
          <nav aria-label="创作准备步骤" className="flex shrink-0 flex-wrap gap-1 px-4 py-2 sm:gap-2 sm:px-8">
            {([ ["details", "1", "基础设定"], ["discussion", "2", "头脑风暴"], ["preview", "3", "骨架与启动"] ] as const).map(([id, number, label]) => (
              <button key={id} type="button" aria-current={step === id ? "step" : undefined}
                disabled={Boolean(busy) || (id === "discussion" ? !session : id === "preview" ? !session?.prompt_md : false)}
                onClick={() => id === "details" ? showBasics() : setStep(id)}
                className={`rounded-full px-3 py-2 text-[12px] transition-colors disabled:opacity-40 ${step === id ? "bg-violet-soft font-semibold text-violet" : "text-ink-muted hover:bg-white/60"}`}>
                <span className="mr-1.5 opacity-70">{number}</span>{label}
              </button>
            ))}
          </nav>
          {session?.restart_required && <section aria-label="确认重新推导" className="mx-5 mb-3 shrink-0 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 sm:mx-8">
            <p className="text-[13px] font-semibold">基础设定已保存，尚未调用模型。</p>
            {restartChoice ? <><p className="mt-1 text-[12px] leading-relaxed text-ink-muted">是否从头推导？确认后只使用新设定，旧对话、骨架与书名方案归入历史，不再参与新推导。历史费用与 Token 记录保留。</p>
              <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={PRIMARY} disabled={Boolean(busy)} onClick={() => void restartBasics()}>{busy === "restart" ? "正在提交…" : "确认，从头推导"}</button>
                <button type="button" className="btn-ghost" disabled={Boolean(busy)} onClick={() => setRestartChoice(false)}>暂不推导</button></div></>
              : <button type="button" className="btn-secondary mt-2" disabled={Boolean(busy)} onClick={() => setRestartChoice(true)}>查看从头推导选项</button>}
          </section>}

          {session && step !== "sessions" && (running || paused) && <section aria-label="本轮请求控制" className="mx-4 mb-2 flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-xl border border-violet/20 bg-white/65 px-4 py-3 sm:mx-8">
            <div className="min-w-0 flex-1 basis-full text-[12px] leading-relaxed text-ink-muted sm:basis-auto">
              {paused ? <><p role="status" className="font-medium text-violet">{session.active_operation === "titles" ? "书名推荐已暂停" : "推导已暂停"}{elapsed !== null && ` · 本轮已运行 ${elapsed} 秒`}</p><p>{resumableDelivery ? savedAssembly ? "全部阶段成果已保存。继续时重新校验并保存正式骨架，无需再次调用模型。" : savedAssemblyParts ? "已完成阶段和交付部分已保存。继续时复用这些成果，保留已有正式骨架。" : "已完成阶段已保存。继续时从未完成阶段发起新请求，保留已有正式骨架。" : "已有内容已保留。继续时会根据保存的完整上下文重新发起本轮请求，也可以先修改内容或基础设定。"}</p></>
                : <WorkshopRuntimeStatus progress={runtimeProgress} label={progressLabel!} now={clock} startedAt={startedAt} />}
            </div>
            <button type="button" className="btn-secondary ml-auto !text-violet" disabled={Boolean(busy) || (running && !session.job_id)} onClick={() => void controlRequest(paused ? "resume" : "pause")}>{busy === "pause" ? "正在暂停…" : busy === "resume" ? "正在继续…" : paused ? resumableDelivery ? deliveryResumeLabel : session.active_operation === "titles" ? "继续推荐" : "继续推导" : session.active_operation === "titles" ? "暂停推荐" : "暂停推导"}</button>
          </section>}

          {deliveryProgress && step !== "sessions" && <div className="mx-4 mb-2 max-h-[35dvh] shrink-0 overflow-y-auto sm:mx-8">
            <WorkshopDeliveryCheckpoints progress={deliveryProgress} running={running} renderContent={(text) => <Markdown text={text} />} />
          </div>}

          {(error || (step !== "sessions" && (session?.last_error || connectionError || (session?.status === "error" && resumableDelivery)))) && (
            <div role="alert" className="mx-5 mb-3 shrink-0 rounded-2xl border border-violet/20 bg-violet-soft px-4 py-3 text-[13px] leading-relaxed text-ink-text sm:mx-8">
              {error || connectionError || session?.last_error}
              {session?.last_error && <p className="mt-1 text-ink-muted">本轮未完成。{session.prompt_md ? "已有骨架仍保留。" : "基础设定和已提交的对话仍保留。"}{resumableDelivery ? savedAssembly ? "全部阶段成果已保存，可重新校验并保存正式骨架，无需再次调用模型。" : savedAssemblyParts ? "可从未完成部分继续，已保存阶段和部分会复用，不会自动重试。" : "可从未完成阶段继续，已保存阶段会复用，不会自动重试。" : "重试会使用已保存的完整上下文。"}</p>}
              {session?.status === "error" && resumableDelivery && <button type="button" className="btn-secondary mt-2 !text-violet" disabled={Boolean(busy)} onClick={() => void controlRequest("resume")}>{busy === "resume" ? "正在继续…" : deliveryResumeLabel}</button>}
            </div>
          )}

          {step === "sessions" && <div className="flex min-h-0 flex-none flex-col sm:flex-1">
            <div className="min-h-0 flex-none px-5 sm:flex-1 sm:overflow-y-auto pb-5 sm:px-8">
              <h3 className="mb-2 text-[16px] font-semibold">选择要继续的头脑风暴</h3>
              <p className="mb-5 text-[12px] leading-relaxed text-ink-muted">同名作品可按任务 ID 和更新时间区分。切换不会停止正在进行的推导，未发送的修改在本窗口按讨论分别保留。</p>
              <WorkshopSessions currentId={session?.id} busy={Boolean(busy)} onSelect={(id) => void restoreSession(id)} onNew={newSession} />
              {busy === "restore" && <p role="status" className="mt-3 text-[12px] text-violet">正在恢复选中的完整讨论…</p>}
            </div>
            <footer className="shrink-0 border-t border-paper-line bg-white/65 px-5 py-4 sm:px-8">
              <button type="button" className="btn-ghost" disabled={Boolean(busy)} onClick={() => setStep(session ? session.status === "launched" ? "preview" : "discussion" : "details")}>{session ? "返回当前讨论" : "返回新建"}</button>
            </footer>
          </div>}

          {step === "details" && (
            <form onSubmit={session ? saveBasics : start} className="flex min-h-0 flex-none flex-col sm:flex-1">
              <div className="min-h-0 flex-none px-5 sm:flex-1 sm:overflow-y-auto py-2 sm:px-8">
                <div className="mb-5 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white/60 px-4 py-3 text-[11px] text-ink-muted">
                  <span>头脑风暴模型：{model}</span><Link to="/settings?tab=text" target="_blank" rel="noreferrer" className="font-medium text-violet">独立模型设置 ↗</Link>
                </div>
                {saved && !session && <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-violet-soft p-4 text-[13px]">
                  <div><p className="font-medium">{saved.status === "launched" ? "上次的创作任务" : "上次的讨论还在"}</p><p className="mt-1 text-ink-muted">{titleOf(saved)} · 第 {saved.revision} 版</p></div>
                  <button type="button" className="btn-secondary" disabled={Boolean(busy)} onClick={() => void restoreSession(saved.id)}>{saved.status === "launched" ? "查看创作任务" : "继续上次讨论"}</button>
                </div>}
                {!session && <details className="mb-5 rounded-2xl border border-paper-line bg-white/45 p-4" open={showSessions} onToggle={(event) => setShowSessions(event.currentTarget.open)}>
                  <summary className="cursor-pointer text-[13px] font-medium text-violet">已有头脑风暴 · 搜索并恢复指定讨论</summary>
                  {showSessions && <div className="mt-4"><WorkshopSessions busy={Boolean(busy)} onSelect={(id) => void restoreSession(id)} /></div>}
                </details>}
                {session && <p className="mb-4 rounded-xl bg-violet-soft p-3 text-[13px] leading-relaxed text-ink-muted">{launched ? "这是启动创作时的基础设定，当前作品保持此快照。设计新故事请新建讨论。" : running ? "本轮推导正在进行，可以查看设定；结束后可修改。" : editingBasics ? "先编辑并保存设定，再由你确认是否从头推导。保存不会调用模型。" : "当前为已保存设定的预览，点击下方“修改基础设定”可编辑。"}</p>}
                <fieldset className="min-w-0" disabled={Boolean(busy) || running || launched || Boolean(session && !editingBasics)}>
                <div className="grid gap-x-5 sm:grid-cols-2">
                  <Field label="暂定书名"><input className={fieldClass} value={input.title} onChange={(e) => setInput({ ...input, title: e.target.value })} placeholder="选填，骨架确认后再由你决定正式书名" /></Field>
                  <Field label="作者"><input className={fieldClass} value={input.author} onChange={(e) => setInput({ ...input, author: e.target.value })} placeholder="留空则在创作时生成笔名" /></Field>
                </div>
                <fieldset className="mb-4 min-w-0">
                  <legend className="mb-1.5 text-[12px] font-medium tracking-[-0.01em] text-ink-muted">作品类型</legend>
                  <GenreChips selected={input.genres} onChange={(genres) => setInput((current) => ({ ...current, genres }))}
                    other={otherGenre} onOtherChange={setOtherGenre} />
                  {customGenres.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-ink-muted">
                      <span>已带入的自定义类型：</span>
                      {customGenres.map((genre) => (
                        <button key={genre} type="button" aria-label={`移除类型 ${genre}`} className="rounded-full bg-violet-soft px-3 py-1.5 font-medium text-violet"
                          onClick={() => setInput((current) => ({ ...current, genres: current.genres.filter((value) => value !== genre) }))}>{genre}<span className="ml-2" aria-hidden="true">×</span></button>
                      ))}
                    </div>
                  )}
                  <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">可以多选、组合类型，也可以填写其他类型；尚未确定可留空，在讨论中继续完善。</p>
                </fieldset>
                <Field label="故事构想或已有框架"><textarea className={textareaClass} rows={5} value={input.premise} onChange={(e) => setInput({ ...input, premise: e.target.value })} placeholder="粘贴故事背景、人物设定或已有细纲。还没想清楚的地方，我们一起补齐。" required /></Field>
                <div className="grid gap-x-5 sm:grid-cols-2">
                  <Field label="计划章节数"><input type="number" min={1} step={1} className={fieldClass} value={input.chapters ?? ""} onChange={(e) => setInput({ ...input, chapters: e.target.value ? Number(e.target.value) : undefined })} placeholder="未确定可留空" /></Field>
                  <Field label="每章大约多少字／词"><input type="number" min={1} step={1} className={fieldClass} value={input.words_per_chapter ?? ""} onChange={(e) => setInput({ ...input, words_per_chapter: e.target.value ? Number(e.target.value) : undefined })} placeholder="允许合理浮动" /></Field>
                  <Field label="故事组织形式"><select className={fieldClass} value={input.mode ?? ""} onChange={(e) => setInput({ ...input, mode: e.target.value as NarrativeMode | "" })}>
                    <option value="">尚未确定，在讨论中选择</option>{MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select></Field>
                  <Field label="正文语言"><input className={fieldClass} value={input.language} onChange={(e) => setInput({ ...input, language: e.target.value })} placeholder="例如：英文、中文" /></Field>
                </div>
                {input.mode === "series_installment" && <p className="mb-4 rounded-xl bg-violet-soft p-3 text-[12px] leading-relaxed text-ink-muted">这里填写当前分册的篇幅。系列总册数、共享人物和跨册伏笔会在讨论中补齐，每册分别启动。</p>}
                {input.mode === "multi_volume" && <p className="mb-4 text-[12px] text-ink-muted">填写整本书的章节数；卷数、章节范围与卷间承接会在讨论中确定。</p>}
                <details className="mb-5 rounded-2xl border border-paper-line bg-white/45 p-4">
                  <summary className="cursor-pointer text-[13px] font-medium text-ink-text">补充读者、地域与硬性限制 <span className="ml-1 font-normal text-ink-muted">也可以稍后讨论</span></summary>
                  <div className="mt-4 grid gap-x-5 sm:grid-cols-2">
                    <Field label="目标读者"><input className={fieldClass} value={input.audience} onChange={(e) => setInput({ ...input, audience: e.target.value })} placeholder="年龄、阅读偏好或期待的情绪" /></Field>
                    <Field label="发行地域"><input className={fieldClass} value={input.market} onChange={(e) => setInput({ ...input, market: e.target.value })} placeholder="例如：美国；不同于故事发生地" /></Field>
                  </div>
                  <Field label="硬性限制"><textarea className={textareaClass} rows={2} value={input.constraints} onChange={(e) => setInput({ ...input, constraints: e.target.value })} placeholder="必须保留的角色、禁止情节、结局或揭示时点；没有限制也可以写明。" /></Field>
                </details>
                </fieldset>
              </div>
              <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-paper-line bg-white/65 px-5 py-4 sm:px-8">
                <button type="button" className="btn-ghost" disabled={Boolean(busy)} onClick={() => session ? setStep(namingStage || launched ? "preview" : "discussion") : onBack()}>{session ? "返回当前讨论" : "返回普通创建"}</button>
                {session ? <div className="flex flex-wrap gap-2">{editingBasics && <button type="button" className="btn-ghost" disabled={Boolean(busy)} onClick={showBasics}>取消修改</button>}
                  {!launched && (editingBasics ? <button key="save-basics" type="submit" className={PRIMARY} disabled={disabled || !input.premise.trim()}>{busy === "input" ? "正在保存…" : "保存基础设定"}</button>
                    : <button key="edit-basics" type="button" className="btn-secondary" disabled={disabled} onClick={(event) => { event.preventDefault(); setEditingBasics(true); }}>修改基础设定</button>)}</div>
                  : <button type="submit" className={PRIMARY} disabled={Boolean(busy) || !input.premise.trim()}>{busy === "create" ? "正在保存设定…" : "开始头脑风暴"}<Icon name="chevron-right" /></button>}
              </footer>
            </form>
          )}

          {step === "discussion" && session && (
            <div className="flex min-h-0 flex-none flex-col sm:flex-1">
              <div className="mx-4 mb-2 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-xl bg-white/60 px-3 py-2 text-[12px] text-ink-muted sm:mx-8">
                <span>头脑风暴模型：{model}</span><Link to="/settings?tab=text" target="_blank" rel="noreferrer" className="font-medium text-violet">独立模型设置 ↗</Link>
              </div>
              <div className="mb-2 flex shrink-0 flex-wrap items-center justify-between gap-2 px-5 sm:px-8">
                <span className="text-[11px] text-ink-muted">{session.messages.length} 条对话 · 展开可查看全文</span>
                <div className="flex flex-wrap gap-1">
                  <button type="button" className="btn-ghost !px-2 !py-1.5 !text-[11px]" onClick={() => expandAll(true)}>全部展开</button>
                  <button type="button" className="btn-ghost !px-2 !py-1.5 !text-[11px]" onClick={() => expandAll(false)}>全部收起</button>
                  {lastAssistant >= 0 && <button type="button" className="btn-ghost !px-2 !py-1.5 !text-[11px] !text-violet" onClick={() => latestReply.current?.scrollIntoView({ block: "start", behavior: "smooth" })}>跳到最新回复</button>}
                </div>
              </div>
              <div className="min-h-0 flex-none px-5 sm:flex-1 sm:overflow-y-auto pb-5 sm:px-8" aria-label="头脑风暴对话">
                <div className="mx-auto w-full max-w-[1200px]">
                  <WorkshopHistory key={`${session.id}:history`} session={session} />
                  <details className="mb-4 rounded-xl border border-paper-line bg-white/70 px-4 py-3 text-[12px] text-ink-muted">
                    <summary className="cursor-pointer">本次讨论的费用与 Token 用量</summary>
                    <GenerationCostPanel owner={`workshop:${session.id}`} refreshKey={session.revision} />
                  </details>
                  <ConversationCard key={inputKey} id="workshop-original-input" title="原始基础设定" preview={(session.initial_input ?? session.input).premise}
                    expanded={expanded[inputKey] ?? false} onToggle={() => toggle(inputKey, expanded[inputKey] ?? false)}>
                    <InitialBrief input={session.initial_input ?? session.input} />
                  </ConversationCard>
                  {session.messages.length === 0 && <div className="my-8 max-w-xl rounded-2xl border border-paper-line bg-white/60 p-5 text-[14px] leading-relaxed text-ink-muted">已保存你的构想。模型会先识别已确定的设定，再补问关键缺项；你可以随时增加限制或改变方向。</div>}
                  <div className="mt-4 space-y-4">{session.messages.map((message, index) => {
                    const isOpen = expanded[messageKey(index)] ?? index === lastAssistant;
                    const fullSource = message.questions?.length
                      ? `${message.content}\n\n## 本轮待确认\n\n${message.questions.map((question, number) => `### 问题 ${number + 1}\n\n${question}`).join("\n\n")}`
                      : message.content;
                    return <article key={messageKey(index)} ref={index === lastAssistant ? latestReply : undefined}
                      className={message.role === "user" ? "ml-auto max-w-[94%]" : "mr-auto w-full"}>
                      <ConversationCard id={`workshop-message-${index}`} title={`${message.role === "user" ? "你的想法" : "故事设计助手"} · 第 ${index + 1} 条`}
                        preview={message.content} source={fullSource} user={message.role === "user"}
                        expanded={isOpen} onToggle={() => toggle(messageKey(index), isOpen)}>
                        <Markdown text={message.content} conversation />
                        {message.questions && message.questions.length > 0 && <div className="mt-5 border-t border-paper-line pt-4">
                          <h3 className="mb-3 text-[14px] font-semibold text-violet">本轮待确认</h3><QuestionList questions={message.questions} />
                        </div>}
                      </ConversationCard>
                    </article>;
                  })}</div>
                  {standaloneQuestions && !running && <div className="mt-4">
                    <Questions key={questionKey} questions={session.questions} expanded={expanded[questionKey] ?? true} onToggle={() => toggle(questionKey, expanded[questionKey] ?? true)} />
                  </div>}
                  {!running && busy === "send" && <p role="status" className="mt-4 text-[13px] text-violet">{progressLabel}</p>}
                  <SessionLog session={session} />
                </div>
              </div>
              <form onSubmit={(e) => { e.preventDefault(); if (draft.trim() && !disabled && !session.restart_required) void submitTurn(session, draft.trim(), "discuss"); }} className="max-h-[35dvh] shrink-0 overflow-y-auto border-t border-paper-line bg-white/70 px-5 py-3 sm:px-8">
                <label htmlFor="workshop-message" className="mb-2 block text-[12px] font-medium text-ink-muted">补充、回答或修改设定 · 不限讨论次数</label>
                <textarea id="workshop-message" className={`${textareaClass} !min-h-[72px] max-h-[22dvh] !text-[16px] !leading-[1.85]`} rows={2} disabled={restoring || controlling} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="例如：Nora 先保存证据，第六章前不要对质；把这个选择变成前三章的钩子。" />
                <p id="workshop-turn-guidance" className="mt-2 text-[11px] leading-relaxed text-ink-muted">普通发送会继续讨论，分阶段推进当前提案、取舍和修订，不会自动生成完整骨架。没有固定讨论轮数；方向满意后可直接生成。</p>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                  <button type="button" className="btn-secondary" disabled={Boolean(busy) || !session.prompt_md} onClick={() => setStep("preview")}>预览当前骨架</button>
                  <div className="flex flex-wrap gap-2">
                    {!running && session.status === "error" && !resumableDelivery && <button type="button" disabled={disabled} className="btn-ghost" onClick={() => void submitTurn(session, "请基于已保存的全部对话继续完成上一轮尚未落实的要求，保留已确认的设定。", session.turn_intent ?? "discuss")}>重试本轮</button>}
                    <button type="button" aria-describedby="workshop-turn-guidance" disabled={!canDeliver} className="btn-secondary !text-violet" onClick={requestDelivery}>{busy === "send" && session.turn_intent === "deliver" ? "正在生成骨架…" : deliveryLabel}</button>
                    <button type="submit" disabled={disabled || Boolean(session.restart_required) || !draft.trim()} className={PRIMARY}>{busy === "send" ? "提交中…" : running ? "推导中…" : "发送并继续推导"}<Icon name="chevron-right" /></button>
                  </div>
                </div>
              </form>
            </div>
          )}

          {step === "preview" && session && (
            <div className="flex min-h-0 flex-none flex-col sm:flex-1">
              <div className="min-h-0 flex-none px-5 sm:flex-1 sm:overflow-y-auto pb-6 sm:px-8">
                <WorkshopHistory key={`${session.id}:history`} session={session} />
                <details className="mb-4 rounded-xl border border-paper-line bg-white/70 px-4 py-3 text-[12px] text-ink-muted">
                  <summary className="cursor-pointer">本次讨论的费用与 Token 用量</summary>
                  <GenerationCostPanel owner={`workshop:${session.id}`} refreshKey={session.revision} />
                </details>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div><p className="text-[14px] font-semibold">当前小说骨架</p><p className="mt-1 text-[11px] text-ink-muted">第 {session.revision} 版 · {prepared ? "命令已绑定此版本" : "可继续讨论和修订"}</p></div>
                  <a className="btn-secondary" href={workshopApi.promptUrl(session.id)} download={prepared?.prompt_filename || "novel-skeleton.md"}>下载骨架 MD</a>
                  <Link className="btn-secondary" to={`/workshop/${session.id}/covers`}>基于骨架设计封面</Link>
                </div>
                {launched && <section aria-label="全书创作进度" className="mb-5 rounded-2xl border border-violet/25 bg-violet-soft p-4">
                  <p role="status" className="text-[14px] font-semibold text-violet">{runJob?.status === "error" ? "创作任务未完成" : runJob?.status === "done" ? "任务已结束，请查看作品与交付结果" : "全书创作已启动 · 正在执行"}</p>
                  <p className="mt-1 text-[12px] text-ink-muted">任务 {session.job_id} · 关闭窗口后继续执行</p>
                  {(runError || runJob?.error) && <p role="alert" className="mt-2 text-[13px] text-ink-text">{runError || runJob?.error}</p>}
                </section>}
                <details open={!namingStage || launched} className="rounded-2xl border border-paper-line bg-white/75 p-5 sm:p-7">
                  <summary className="cursor-pointer text-[14px] font-medium text-violet">查看完整骨架</summary>
                  <div className="mx-auto mt-4 max-w-[1200px]"><Markdown text={session.prompt_md} /></div>
                </details>
                {session.input_needs_revision && <p className="mt-3 rounded-xl bg-violet-soft p-3 text-[13px] text-ink-muted">基础设定已变更。这里保留的是上一版骨架，需完成更新后重新确认。</p>}
                {!launched && namingStage && <section aria-label="小说书名确认" className="mt-5 rounded-2xl border border-violet/25 bg-white/75 p-5">
                  <h3 className="text-[16px] font-semibold">为这本小说定名</h3>
                  <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">根据已确认骨架推荐书名。选择一个作为起点，或直接写下你的版本；也可以继续给出要求，让模型优化。</p>
                  <div className="my-4 grid gap-3 sm:grid-cols-2">{session.naming?.candidates.map((candidate) => <button type="button" key={candidate.title} disabled={disabled}
                    aria-pressed={namingDraft.title === candidate.title} onClick={() => changeNaming({ title: candidate.title })}
                    className={`rounded-2xl border p-4 text-left ${namingDraft.title === candidate.title ? "border-violet/40 bg-violet-soft" : "border-paper-line bg-white/65 hover:border-violet/30"}`}>
                    <span className="block text-[15px] font-semibold leading-relaxed">{candidate.title}</span><span className="mt-2 block text-[13px] leading-relaxed text-ink-muted">{candidate.reason}</span>
                  </button>)}</div>
                  <Field label="最终书名"><input className={fieldClass} value={namingDraft.title} maxLength={500} disabled={disabled} onChange={(event) => changeNaming({ title: event.target.value })} placeholder="选择候选名或输入你希望使用的书名" /></Field>
                  <button type="button" className="btn-ghost !text-violet" disabled={disabled} onClick={() => changeNaming({ title: titleOf(session) })}>沿用当前骨架书名</button>
                  {titleChanged && <p className="mt-2 text-[12px] text-ink-muted">书名已修改，确认后将同步骨架、封面交接和启动命令。</p>}
                  <Field label="书名优化要求"><textarea className={`${textareaClass} mt-3`} rows={2} value={namingDraft.hints} disabled={disabled} onChange={(event) => changeNaming({ hints: event.target.value })} placeholder="例如：突出女性成长，用简短英文书名，保留某个关键词，不透露最终反转。" /></Field>
                  <button type="button" className="btn-secondary" disabled={disabled || !canPrepare} onClick={() => void suggestTitles()}>{busy === "titles" ? "正在提交…" : "按要求重新推荐书名"}</button>
                  {Boolean(session.naming?.requests.length) && <details className="mt-4 rounded-xl border border-paper-line p-3 text-[13px]"><summary className="cursor-pointer text-ink-muted">命名历史 · {session.naming?.requests.length} 轮</summary>
                    {session.naming?.requests.map((request, index) => <div key={`${index}:${request.created_at}`} className="mt-3 border-t border-paper-line pt-3"><p className="font-medium">第 {index + 1} 轮 · {request.instructions || "根据完整骨架推荐"}</p>
                      {request.current_title && <p className="mt-1 text-ink-muted">本轮修改起点：{request.current_title}</p>}
                      {request.candidates?.map((candidate) => <p key={candidate.title} className="mt-2 leading-relaxed">{candidate.title} — {candidate.reason}</p>)}{request.error && <p className="mt-2 text-ink-muted">{request.error}</p>}</div>)}
                  </details>}
                </section>}
                {!prepared && !canPrepare && <div className="mt-4 rounded-2xl bg-violet-soft p-4 text-[13px] leading-relaxed text-ink-muted">
                  <p>这版骨架还有待确认内容，先返回讨论补齐，再生成启动命令。</p>
                  {session.questions.length > 0 && <div className="mt-3"><Questions key={questionKey} questions={session.questions} expanded={expanded[questionKey] ?? true} onToggle={() => toggle(questionKey, expanded[questionKey] ?? true)} /></div>}
                </div>}
                {prepared && <section className="mt-5 rounded-2xl border border-violet/25 bg-violet-soft p-5" aria-label="已确认的启动任务">
                  <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-[15px] font-semibold">完整启动命令</h3><button type="button" className="btn-secondary" onClick={() => void copyCommand()}>{copied ? "已复制" : "复制命令"}</button></div>
                  <dl className="my-4 grid gap-3 text-[12px] sm:grid-cols-2"><div><dt className="text-ink-muted">最终书名</dt><dd className="mt-1 font-medium">{String(prepared.validation.title ?? titleOf(session))}</dd></div><div><dt className="text-ink-muted">项目名称</dt><dd className="mt-1 break-all font-medium">{prepared.project_name}</dd></div><div><dt className="text-ink-muted">正文输出格式</dt><dd className="mt-1 font-medium">{prepared.output_formats.join(" · ").toUpperCase()}</dd></div>
                    <div><dt className="text-ink-muted">组织形式</dt><dd className="mt-1 font-medium">{MODES.find(([value]) => value === prepared.validation.mode)?.[1] ?? String(prepared.validation.mode ?? "见骨架")}</dd></div>
                    <div><dt className="text-ink-muted">创作篇幅</dt><dd className="mt-1 font-medium">{String(prepared.validation.chapters ?? "—")} 章 · 总目标 {String(prepared.validation.target_words ?? "—")} 字／词{typeof prepared.validation.chapters === "number" && typeof prepared.validation.target_words === "number" && prepared.validation.chapters > 0 && ` · 每章约 ${Math.round(prepared.validation.target_words / prepared.validation.chapters)}`}</dd></div>
                  </dl>
                  <pre tabIndex={0} className="overflow-x-auto rounded-xl border border-violet/15 bg-white/75 p-4 font-mono text-[11px] leading-relaxed text-ink-text">{prepared.command}</pre>
                  <p className="mt-3 text-[12px] leading-relaxed text-ink-muted">如在终端运行：先将下载的 MD 保存到仓库 <code className="break-all">prompt/{prepared.prompt_filename}</code>，再在仓库根目录执行命令。下方“一键启动”会自动处理文件。</p>
                  <p className="mt-3 text-[12px] leading-relaxed text-ink-muted">启动后沿用现有全书规划、正文编写与交付流程。封面可在作品的封面工作室继续制作。</p>
                  <p className="mt-2 text-[11px] text-ink-muted">检查范围：骨架元数据与启动参数。正文尚未生成，字数为允许浮动的创作目标。</p>
                </section>}
                <SessionLog session={session} />
              </div>
              <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-paper-line bg-white/70 px-5 py-4 sm:px-8">
                <button type="button" className="btn-ghost" disabled={Boolean(busy)} onClick={() => setStep("discussion")}>{launched ? "查看讨论记录" : "返回讨论，继续修改"}</button>
                {launched && session.project_id ? <button type="button" className={PRIMARY} onClick={() => { onClose(); navigate(`/projects/${encodeURIComponent(session.project_id!)}`); }}>打开作品与交付</button> : prepared ? <button type="button" className={PRIMARY} onClick={() => void launch()} disabled={disabled}>{busy === "launch" ? "正在启动…" : "一键启动全书创作"}<Icon name="chevron-right" /></button>
                  : namingStage ? <button type="button" className={PRIMARY} onClick={() => void prepare()} disabled={disabled || !canPrepare || !namingDraft.title.trim()}>{busy === "prepare" ? "正在确认书名…" : "确认书名并生成命令"}</button>
                    : <button type="button" className={PRIMARY} onClick={() => void suggestTitles()} disabled={disabled || !session.prompt_md || !canPrepare}>{busy === "titles" ? "正在确认骨架…" : "确认骨架，推荐书名"}</button>}
              </footer>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Markdown({ text, conversation = false }: { text: string; conversation?: boolean }) {
  return <div className={`${conversation ? "!text-[16px] sm:!text-[17px] [&_code]:!text-[14px] [&_pre]:!text-[14px] [&_table]:!text-[14px]" : "!text-[16px]"} prose-outline break-words !leading-[1.85] [&_h1]:!text-xl [&_h1]:!leading-snug [&_h2]:!mb-3 [&_h2]:!mt-7 [&_h2]:!text-base [&_h2]:!leading-snug [&_h2]:!text-ink-text [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-semibold [&_h4]:mb-2 [&_h4]:mt-4 [&_h4]:font-semibold [&_p]:!mb-3 [&_ul]:list-disc [&_ol]:list-decimal [&_li]:!my-1.5 [&_blockquote]:my-4 [&_blockquote]:border-l-[3px] [&_blockquote]:border-violet/40 [&_blockquote]:bg-violet-soft/50 [&_blockquote]:py-2 [&_blockquote]:pl-4 [&_blockquote]:pr-3 [&_blockquote]:text-ink-muted [&_code]:rounded [&_code]:bg-violet-soft/70 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[12px] [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-violet-soft/70 [&_pre]:p-4 [&_pre]:text-[12px] [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[12px] [&_td]:border [&_td]:border-paper-line [&_td]:p-3 [&_td]:align-top [&_th]:border [&_th]:border-paper-line [&_th]:bg-violet-soft/60 [&_th]:p-3 [&_th]:text-left [&_a]:text-violet [&_a]:underline [&_hr]:my-6 [&_hr]:border-paper-line`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ({ children }) => <div className="my-4 overflow-x-auto"><table>{children}</table></div> }}>{text}</ReactMarkdown>
  </div>;
}

function ConversationCard({ id, title, preview, expanded, onToggle, children, source, user = false }: {
  id: string; title: string; preview: string; expanded: boolean; onToggle: () => void;
  children: ReactNode; source?: string; user?: boolean;
}) {
  const [copyState, setCopyState] = useState("复制 Markdown");
  const summary = preview.replace(/[#>*`_[\]]/g, "").replace(/\s+/g, " ").trim();
  const excerpt = summary.length > 120 ? `${summary.slice(0, 120)}…` : summary;
  async function copy() {
    try { await navigator.clipboard.writeText(source ?? ""); setCopyState("已复制全文"); }
    catch { setCopyState("复制失败，可展开原文后手动复制"); }
  }
  return <section className={`overflow-hidden rounded-[20px] border ${user ? "border-violet/10 bg-violet-soft" : "border-paper-line bg-white/70"}`}>
    <button type="button" className="flex w-full items-start gap-3 px-4 py-3.5 text-left sm:px-5" aria-expanded={expanded} aria-controls={id} aria-label={`${expanded ? "收起" : "展开"}${title}`} onClick={onToggle}>
      <span className={`mt-0.5 shrink-0 text-violet transition-transform ${expanded ? "rotate-90" : ""}`}><Icon name="chevron-right" /></span>
      <span className="min-w-0 flex-1"><span className="block text-[14px] font-semibold text-ink-muted">{title}</span>
        {!expanded && <span className="mt-1.5 block text-[14px] leading-relaxed text-ink-text">{excerpt || "暂无内容"}</span>}
      </span>
      <span className="shrink-0 text-[11px] text-violet">{expanded ? "收起" : "查看全文"}</span>
    </button>
    {expanded && <div id={id} className="border-t border-paper-line px-4 pb-4 pt-4 sm:px-5">
      {children}
      {source !== undefined && <div className="mt-4 border-t border-paper-line pt-3">
        <button type="button" className="btn-ghost !px-2 !py-1 !text-[11px]" onClick={() => void copy()}>{copyState}</button>
        <RawText id={`${id}-source`} title="查看 Markdown 原文" text={source} />
      </div>}
    </div>}
  </section>;
}

function InitialBrief({ input }: { input: WorkshopInput }) {
  const fields = [
    ["暂定书名", input.title], ["作者", input.author || "留空，创作时生成笔名"],
    ["作品类型", input.genres.map(genreLabel).join(" · ")], ["组织形式", MODES.find(([mode]) => mode === input.mode)?.[1]],
    ["计划章节数", input.chapters], ["每章目标字／词", input.words_per_chapter],
    ["正文语言", input.language], ["目标读者", input.audience], ["发行地域", input.market],
  ];
  return <div>
    <dl className="mb-5 grid gap-3 text-[12px] sm:grid-cols-2">{fields.map(([label, value]) => <div key={label}>
      <dt className="text-ink-muted">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words font-medium">{value || "尚未确定"}</dd>
    </div>)}</dl>
    <h3 className="mb-3 text-[13px] font-semibold">完整故事构想与已有框架</h3><Markdown text={input.premise} conversation />
    <h3 className="mb-3 mt-5 text-[13px] font-semibold">原始硬性限制</h3><Markdown text={input.constraints || "尚未补充，可在讨论中确定。"} conversation />
    <RawText id="workshop-original-source" title="查看原始输入全文" text={input.premise} />
  </div>;
}

function Questions({ questions, expanded, onToggle }: { questions: string[]; expanded: boolean; onToggle: () => void }) {
  return <ConversationCard id="workshop-questions" title={`继续完善的关键问题 · ${questions.length} 项`} preview={questions.join("；")}
    expanded={expanded} onToggle={onToggle} source={questions.map((question, index) => `### 问题 ${index + 1}\n\n${question}`).join("\n\n")}>
    <QuestionList questions={questions} />
  </ConversationCard>;
}

function QuestionList({ questions }: { questions: string[] }) {
  return <ol className="list-decimal space-y-3 pl-5 text-[16px]">{questions.map((question, index) => <li key={index} className="pl-1 marker:text-violet"><Markdown text={question} conversation /></li>)}</ol>;
}

function RawText({ id, title, text }: { id: string; title: string; text: string }) {
  const [show, setShow] = useState(false);
  return <div className="mt-3 text-[11px] text-ink-muted">
    <button type="button" className="btn-ghost !px-2 !py-1 !text-[11px]" aria-expanded={show} aria-controls={id} onClick={() => setShow(!show)}>{show ? "收起原文" : title}</button>
    {show && <pre id={id} className="mt-3 whitespace-pre-wrap break-words rounded-xl bg-violet-soft/50 p-4 font-mono text-[12px] leading-relaxed text-ink-text">{text}</pre>}
  </div>;
}

function SessionLog({ session }: { session: WorkshopSession }) {
  if (!session.logs.length) return null;
  return <details className="mt-5 rounded-xl border border-paper-line bg-white/50 p-3">
    <summary className="cursor-pointer text-[12px] text-ink-muted">本次讨论日志 · {session.logs.length} 条</summary>
    <ol className="mt-3 max-h-44 space-y-2 overflow-y-auto text-[11px] leading-relaxed text-ink-muted">{session.logs.map((entry, index) => <li key={index}><time className="mr-2 font-mono">{entry.time}</time>{entry.message}</li>)}</ol>
  </details>;
}

function WorkshopHistory({ session }: { session: WorkshopSession }) {
  const [selected, setSelected] = useState<WorkshopSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const requestId = useRef(0);
  useEffect(() => () => { requestId.current++; }, []);
  if (!session.history?.length) return null;
  async function load(revision: number) {
    const request = ++requestId.current;
    setBusy(true); setFailure(null);
    try {
      const archived = await workshopApi.history(session.id, revision);
      if (request === requestId.current) setSelected(archived);
    } catch (error) {
      if (request === requestId.current) setFailure(messageOf(error));
    } finally { if (request === requestId.current) setBusy(false); }
  }
  return <details className="mb-4 rounded-xl border border-paper-line bg-white/70 p-4">
    <summary className="cursor-pointer text-[13px] font-medium">已归档推导 · {session.history.length} 次</summary>
    <p className="mt-2 text-[12px] text-ink-muted">这些内容仅供追溯，不参与当前推导。全部历史费用仍计入本次讨论。</p>
    <div className="mt-3 flex flex-wrap gap-2">{session.history.map((item) => <button key={item.revision} type="button" className="btn-secondary !text-[12px]" disabled={busy}
      onClick={() => void load(item.revision)}>查看归档 · 第 {item.revision} 版 · {item.title}</button>)}</div>
    {busy && <p role="status" className="mt-3 text-[12px]">正在读取历史…</p>}
    {failure && <p role="alert" className="mt-3 text-[12px]">{failure}</p>}
    {selected && <section aria-label="已归档推导内容" className="mt-4 max-h-[50vh] space-y-5 overflow-y-auto border-t border-paper-line pt-4">
      <InitialBrief input={selected.input} />
      {selected.messages.map((message, index) => <div key={index}><h4 className="mb-2 text-[12px] text-ink-muted">{message.role === "user" ? "你的想法" : "故事设计助手"} · {index + 1}</h4><Markdown text={message.content} /></div>)}
      {selected.prompt_md && <details><summary className="cursor-pointer text-[13px]">已归档完整骨架</summary><Markdown text={selected.prompt_md} /></details>}
    </section>}
  </details>;
}
