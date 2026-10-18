import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { confirmObservedPosterIntents, confirmPosterIntent, posterApi, posterAssetUrl, posterIntent, type PosterCandidate, type PosterDirection, type PosterLayout, type PosterOverview, type PosterPlan, type PosterPreferences, type PosterSet, type PosterTask } from "../api/poster";
import Icon from "../components/Icon";
import GenerationCostPanel from "../components/GenerationCostPanel";
import PosterDialog from "../components/poster/PosterDialog";
import PosterTypography from "../components/poster/PosterTypography";
import "../styles/poster-studio.css";

const activeTask = (task: PosterTask) => task.status === "queued" || task.status === "running";
const time = (value: string) => new Date(value).toLocaleString("zh-CN", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
const message = (reason: unknown) => reason instanceof Error ? reason.message : "请求失败，请稍后重试";
const finishedImage = (candidate: PosterCandidate) => Boolean(candidate.url && candidate.sha256 && candidate.width > 0 && candidate.height > 0);
const paletteText = (palette: PosterDirection["palette"]) => Array.isArray(palette) ? palette.join(" · ") : palette;
const kindName: Record<string, string> = {plan: "封面方向设计", planning: "封面方向设计", generate: "封面生成", batch: "封面生成", generation: "封面生成", retry: "候选恢复", refine: "原图精修", refinement: "原图精修", typography: "标题排版"};
const stageName: Record<string, string> = {queued: "已排队", starting: "准备执行", planning: "设计故事与美术方向", format_correction: "整理设计格式", validating_format: "检查方案格式", preparing: "准备图片任务", generating: "等待图片服务返回", composing: "合成完整标题", candidate_saved: "候选图片已保存", complete: "已完成"};
function originalFor(set: PosterSet, candidate: PosterCandidate, sets: PosterSet[]): CandidateTarget | null {
  let parent = set;
  const seen = new Set<string>();
  while (parent.repair_lineage?.parent_cover_set_id && !seen.has(parent.cover_set_id)) {
    seen.add(parent.cover_set_id);
    const next = sets.find(item => item.cover_set_id === parent.repair_lineage?.parent_cover_set_id);
    if (!next) break;
    const source = next.candidates.find(item => item.concept_id === candidate.concept_id);
    if (source && finishedImage(source)) return {set: next, candidate: source};
    parent = next;
  }
  return null;
}
function useOverview(projectId: string) {
  const [data, setData] = useState<PosterOverview | null>(null);
  const [error, setError] = useState("");
  const [syncedAt, setSyncedAt] = useState("");
  const mounted = useRef(true);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async (force = true) => {
    if (!force && controller.current) return;
    const request = ++sequence.current;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const deadline = window.setTimeout(() => { abort.abort(); if (mounted.current && request === sequence.current) setError("状态读取超时，请刷新重试"); }, 20000);
    try {
      const result = await posterApi.overview(projectId, abort.signal);
      if (!mounted.current || request !== sequence.current) return;
      confirmObservedPosterIntents(projectId, result.tasks); setData(result); setError(""); setSyncedAt(new Date().toLocaleTimeString("zh-CN"));
    } catch (reason) {
      if (mounted.current && request === sequence.current && !abort.signal.aborted) setError(message(reason));
    } finally { window.clearTimeout(deadline); if (controller.current === abort) controller.current = null; }
  }, [projectId]);
  useEffect(() => {
    mounted.current = true;
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void refresh(false); }, 6000);
    const focus = () => { void refresh(); };
    window.addEventListener("focus", focus);
    return () => { mounted.current = false; controller.current?.abort(); window.clearTimeout(initial); window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [refresh]);
  return {data, error, syncedAt, refresh};
}

function ModelProgress({task}: {task: PosterTask}) {
  const event = task.last_upstream_event || task.last_model_event;
  if (!event && task.completed_model_calls === undefined) return null;
  const upstream = Boolean(task.last_upstream_event || event?.event.startsWith("upstream."));
  const sourceStamp = event?.source_timestamp || event?.observed_at;
  const stamp = sourceStamp || event?.received_at || (task.last_upstream_event ? task.last_upstream_event_at : task.last_model_event_at);
  const validStamp = stamp && Number.isFinite(Date.parse(stamp));
  const type = event?.event_kind || event?.upstream_event || event?.event;
  return <div className="poster-model-progress">
    <dl>{event && <><dt>{upstream ? "最近上游事件" : "最近调用事件"}</dt><dd>{type}{event.activity_kind ? ` · ${event.activity_kind}` : ""}</dd><dt>{sourceStamp ? "事件时间" : "事件接收时间"}</dt><dd>{validStamp ? <time dateTime={stamp}>{new Date(stamp).toLocaleString("zh-CN", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"})}</time> : "未记录"}</dd></>}{task.completed_model_calls !== undefined && <><dt>已完成模型调用</dt><dd>{task.completed_model_calls} 次</dd></>}</dl>
    {event && <small>{event.event === "heartbeat" ? "此心跳仅确认调用进程仍在运行，不代表模型正在思考。" : "事件只说明收到服务信号，不代表持续生成或已经完成。"}</small>}
  </div>;
}

function TaskStatus({task, syncedAt, onRetry}: {task: PosterTask; syncedAt: string; onRetry?: () => void}) {
  const [now, setNow] = useState(() => Date.now());
  const running = activeTask(task);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const seconds = Math.max(0, Math.floor(((running ? now : Date.parse(task.updated_at)) - Date.parse(task.created_at)) / 1000));
  return <section className={`poster-progress${running ? "" : " poster-progress--failed"}`} aria-label="封面任务状态" role="status">
    <div className="poster-progress__icon">{running ? <span className="poster-spinner" /> : <Icon name="circle-alert" />}</div>
    <div className="poster-progress__copy"><strong>{kindName[task.kind] || "封面任务"} · {running ? task.status === "queued" ? "等待执行" : "进行中" : task.status === "interrupted" ? "已中断" : "未完成"}</strong>
      <p>{stageName[task.stage] || task.stage || "等待后台状态"}{task.total ? ` · 已处理 ${task.completed || 0}/${task.total}` : ""}</p>
      {running ? <small>可以切换页面，返回后恢复进度。后台状态不代表模型正在持续生成。</small> : <p className="poster-progress__error">{task.error || "请检查已保存结果后重试。"}</p>}
      {!running && <small>已保存的图片和当前选择保留；不会自动追加模型调用。</small>}
      {(task.last_model_event || task.last_upstream_event || task.completed_model_calls !== undefined) && <details className="poster-details"><summary>调用进度详情</summary><ModelProgress task={task} /></details>}
    </div>
    <div className="poster-progress__meta"><span>用时 {Number.isFinite(seconds) ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : "待同步"}</span><small>状态同步 {syncedAt || "—"}</small>{onRetry && !running && <button type="button" className="poster-button poster-button--small" onClick={onRetry}>重试这次任务</button>}</div>
  </section>;
}

type CandidateTarget = {set: PosterSet; candidate: PosterCandidate};
type Drawer = "source" | "history" | "costs" | "plan" | null;
export default function PosterStudio(props: {projectId: string; onLegacy?: () => void}) {
  return <PosterWorkspace key={props.projectId} {...props} />;
}
function PosterWorkspace({projectId, onLegacy}: {projectId: string; onLegacy?: () => void}) {
  const {data, error: readError, syncedAt, refresh} = useOverview(projectId);
  const [searchParams, setSearchParams] = useSearchParams();
  const [localDrawer, setLocalDrawer] = useState<Drawer>(null);
  const drawerParam = searchParams.get("poster_drawer");
  const requestedDrawer = drawerParam === "source" || drawerParam === "history" || drawerParam === "costs"
    ? drawerParam : searchParams.get("tab") === "costs" ? "costs" : null;
  const drawer = localDrawer || requestedDrawer;
  const setDrawer = (value: Drawer) => {
    setLocalDrawer(value);
    if (requestedDrawer) setSearchParams(previous => {
      const next = new URLSearchParams(previous); next.delete("poster_drawer");
      if (next.get("tab") === "costs") next.delete("tab");
      return next;
    }, {replace: true});
  };
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [localTask, setLocalTask] = useState<PosterTask | null>(null);
  const [planId, setPlanId] = useState("");
  const [setId, setSetId] = useState("");
  const [editDirection, setEditDirection] = useState<{plan: PosterPlan; direction: PosterDirection} | null>(null);
  const [refinement, setRefinement] = useState<CandidateTarget & {prompt: string; keep: boolean} | null>(null);
  const [typography, setTypography] = useState<CandidateTarget | null>(null);
  const [preview, setPreview] = useState<CandidateTarget | null>(null);
  const [preferences, setPreferences] = useState<PosterPreferences>({market: "", audience: "", emphasis: "由故事决定", notes: ""});
  const mutation = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const plans = useMemo(() => [...(data?.plans || [])].sort((a, b) => b.created_at.localeCompare(a.created_at)), [data?.plans]);
  const sets = useMemo(() => [...(data?.sets || [])].sort((a, b) => b.created_at.localeCompare(a.created_at)), [data?.sets]);
  const plan = plans.find(value => value.plan_id === planId) || plans[0];
  const set = sets.find(value => value.cover_set_id === setId) || sets[0];
  const tasks = useMemo(() => {
    const values = [...(data?.tasks || [])];
    if (localTask && !values.some(value => value.task_id === localTask.task_id)) values.push(localTask);
    return values.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [data?.tasks, localTask]);
  const running = tasks.filter(activeTask);
  const paidDisabled = Boolean(busy || running.length || data?.legacy_active);
  const task = running[0] || tasks.find(value => value.status !== "complete" && value.task_id === tasks[0]?.task_id);
  const posterTab = searchParams.get("poster_tab");
  const tab = posterTab === "gallery" || posterTab === "plan" ? posterTab
    : searchParams.get("tab") === "gallery" ? "gallery" : sets.length ? "gallery" : "plan";
  const chooseTab = (value: string) => setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("poster_tab", value); return next; });
  const successful = set?.candidates.filter(finishedImage).length || 0;
  const generationUsageHint = `${plan?.directions.length || 4} 次图片生成${data?.advisory_enabled === false ? "。" : "；启用画面建议时，完成后最多追加 1 次文字调用。"}`;
  const chosen = (target: CandidateTarget) => data?.active_cover_set_id === target.set.cover_set_id && data?.selected_candidate_id === target.candidate.candidate_id;
  const run = async (scope: string, payload: unknown, operation: (requestId: string) => Promise<PosterTask>, after?: (result: PosterTask) => void): Promise<boolean> => {
    if (mutation.current) return false;
    mutation.current = true; setBusy(scope); setError(""); setNotice("");
    try {
      const requestId = posterIntent(projectId, scope, payload);
      const result = await operation(requestId);
      confirmPosterIntent(projectId, scope, requestId); if (!mounted.current) return true; setLocalTask(result); after?.(result); await refresh(); return true;
    } catch (reason) { if (mounted.current) setError(`${message(reason)}。请求标识已保留，再次点击会核对同一次请求。`); return false; }
    finally { mutation.current = false; if (mounted.current) setBusy(""); }
  };
  const createPlan = () => run("plan", preferences, requestId => posterApi.plan(projectId, requestId, preferences), () => { setDrawer(null); setPlanId(""); chooseTab("plan"); });
  const generate = () => plan && run(`batch:${plan.plan_id}`, {plan_id: plan.plan_id, plan_hash: plan.plan_hash}, requestId => posterApi.generate(projectId, requestId, plan), result => { setSetId(result.result?.cover_set_id || ""); chooseTab("gallery"); });
  const retry = (targetSet: PosterSet, candidateIds?: string[]) => run(`retry:${targetSet.cover_set_id}:${candidateIds?.join(",") || "all"}`, {set_id: targetSet.cover_set_id, candidate_ids: candidateIds}, requestId => posterApi.retry(projectId, requestId, targetSet.cover_set_id, candidateIds), result => { setSetId(result.result?.cover_set_id || targetSet.cover_set_id); chooseTab("gallery"); });
  const select = async (target: CandidateTarget) => {
    if (mutation.current || chosen(target)) return;
    mutation.current = true; setBusy(`select:${target.candidate.candidate_id}`); setError("");
    try { await posterApi.select(projectId, target.set.cover_set_id, target.candidate.candidate_id, target.set.revision, target.candidate.sha256, data?.active_revision); if (!mounted.current) return; setNotice("已设为作品封面，交付包将使用这个版本。"); await refresh(); }
    catch (reason) { if (mounted.current) { setError(message(reason)); await refresh(); } }
    finally { mutation.current = false; if (mounted.current) setBusy(""); }
  };
  const rebuildDelivery = async () => {
    if (mutation.current) return;
    mutation.current = true; setBusy("delivery"); setError(""); setNotice("");
    try {
      const result = await posterApi.rebuildDelivery(projectId);
      if (!mounted.current) return;
      if (result.delivery.status === "ready") setNotice("交付包已更新，可以下载。");
      else setNotice("已尝试更新交付包，请查看同步状态。");
      await refresh();
    } catch (reason) { if (mounted.current) { setError(message(reason)); await refresh(); } }
    finally { mutation.current = false; if (mounted.current) setBusy(""); }
  };
  const saveDirection = async () => {
    if (!editDirection || mutation.current) return;
    mutation.current = true; setBusy("edit"); setError("");
    try {
      const updated = await posterApi.editPlan(projectId, editDirection.plan, editDirection.plan.directions.map(value => value.id === editDirection.direction.id ? editDirection.direction : value));
      if (!mounted.current) return;
      setPlanId(updated.plan_id); setEditDirection(null); setNotice("修改已保存为新方案。生成时会使用这次修改。"); await refresh();
    } catch (reason) { if (mounted.current) setError(message(reason)); }
    finally { mutation.current = false; if (mounted.current) setBusy(""); }
  };
  const retryTask = (failed: PosterTask) => {
    if (failed.kind === "plan" || failed.kind === "planning") { setDrawer("plan"); return; }
    const resultSet = sets.find(value => value.cover_set_id === failed.result?.cover_set_id);
    if (resultSet && ["refine", "refinement", "retry", "typography"].includes(failed.kind)) {
      const targets = resultSet.candidates.filter(value => !finishedImage(value) && (value.status === "failed" || value.status === "pending"));
      if (targets.length) { void retry(resultSet, targets.map(value => value.candidate_id)); return; }
    }
    const sourceSet = sets.find(value => value.cover_set_id === failed.input?.cover_set_id || value.cover_set_id === failed.result?.cover_set_id);
    const sourceCandidate = sourceSet?.candidates.find(value => value.candidate_id === failed.input?.candidate_id);
    if (sourceSet && sourceCandidate && (failed.kind === "refine" || failed.kind === "refinement")) {
      setRefinement({set: sourceSet, candidate: sourceCandidate, prompt: failed.input?.prompt || "", keep: failed.input?.keep_composition ?? true});
    } else if (sourceSet) void retry(sourceSet);
    else setDrawer("history");
  };
  if (!data) return <section className="poster-studio poster-loading" aria-live="polite"><Icon name="palette" className="h-8 w-8" /><h1>封面工作室</h1><p>{readError || "正在读取封面、方案与任务状态…"}</p>{readError && <button className="poster-button" onClick={() => void refresh()}>重新读取</button>}</section>;
  if (!data.enabled) return <section className="poster-studio poster-loading"><h1>封面工作室</h1><p>新版工作室尚未在当前环境启用。</p>{onLegacy && <button className="poster-button" onClick={onLegacy}>打开历史封面工作室</button>}</section>;
  return <div className="poster-studio">
    <div className="poster-topline"><Link to={`/projects/${encodeURIComponent(projectId)}`}><Icon name="arrow-left" /> 返回作品</Link><span>完整骨架 · 故事海报设计</span></div>
    <header className="poster-header"><div><h1>封面工作室</h1><p>{data.title}</p></div><div className="poster-header__actions"><button className="poster-button poster-button--quiet" onClick={() => setDrawer("history")}><Icon name="history" /> 历史</button><button className="poster-button poster-button--quiet" onClick={() => setDrawer("costs")}>模型与费用</button>{data.selected_candidate_id && (data.delivery && data.delivery.status !== "ready" ? <button className="poster-button" disabled title="交付包尚未同步到当前封面"><Icon name="download" /> 交付包</button> : <a className="poster-button" href={api.deliveryPackageUrl(projectId)} download="book-package.zip"><Icon name="download" /> 交付包</a>)}</div></header>
    <div className="poster-context"><span><Icon name="file-text" /> {data.source.path || "完整小说骨架"} · {data.source.bytes.toLocaleString("zh-CN")} 字节</span><span>4 个设计方向 · 逐张保存</span><button className="poster-text-button" onClick={() => setDrawer("source")}>查看来源 <Icon name="chevron-right" /></button></div>
    {(error || readError) && <div className="poster-error" role="alert">{error || `状态同步暂时失败：${readError}。下方保留上次结果。`}<button className="poster-text-button" onClick={() => { setError(""); void refresh(); }}>刷新状态</button></div>}
    {notice && <p className="poster-success" role="status">{notice}</p>}
    {data.selected_candidate_id && data.delivery && data.delivery.status !== "ready" && <section className="poster-warning poster-delivery" role="status" aria-label="交付包同步状态"><div><strong>{data.delivery.status === "pending" ? "交付包正在更新，当前封面已保存。" : "封面选择已保存，交付包尚未同步。"}</strong><p>{data.delivery.error || "同步完成后可下载包含当前封面的 ZIP 包。"}</p></div>{data.delivery.retryable && <button className="poster-button poster-button--small" disabled={!!busy} onClick={() => void rebuildDelivery()}>{busy === "delivery" ? "正在更新交付包…" : "重新生成交付包"}</button>}</section>}
    {data.legacy_active && <p className="poster-warning" role="status">历史封面任务仍在运行。完成后可开始新设计；现有完整图片仍可选择。</p>}
    <div className="poster-toolbar"><div className="poster-tabs" role="tablist" aria-label="封面工作区" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); const next = event.key === "Home" ? "plan" : event.key === "End" ? "gallery" : tab === "plan" ? "gallery" : "plan";
      chooseTab(next); document.getElementById(`poster-tab-${next}`)?.focus();
    }}><button role="tab" tabIndex={tab === "plan" ? 0 : -1} id="poster-tab-plan" aria-controls="poster-panel-plan" aria-selected={tab === "plan"} onClick={() => chooseTab("plan")}>设计方案 <span>{plan?.status === "approved" ? "已用于生成" : plan ? "待确认" : "未设计"}</span></button><button role="tab" tabIndex={tab === "gallery" ? 0 : -1} id="poster-tab-gallery" aria-controls="poster-panel-gallery" aria-selected={tab === "gallery"} onClick={() => chooseTab("gallery")}>候选封面 <span>{successful}/{set?.requested_count || 4}</span></button></div><button className="poster-button" disabled={paidDisabled || !!data.source.error} onClick={() => setDrawer("plan")}><Icon name="sparkles" /> {plan ? "重新设计方向" : "设计 4 个封面方向"}</button></div>
    {task && <TaskStatus task={task} syncedAt={syncedAt} onRetry={!paidDisabled ? () => retryTask(task) : undefined} />}
    {tab === "plan" ? <section id="poster-panel-plan" role="tabpanel" aria-labelledby="poster-tab-plan">
      {plan ? <><div className="poster-section-heading"><div><h2>让故事成为第一眼的吸引力</h2><p>围绕人物、关系与情绪构图，允许艺术化表达。</p></div>{plans.length > 1 && <label className="poster-inline-label">方案<select aria-label="选择设计方案" value={plan.plan_id} onChange={event => setPlanId(event.target.value)}>{plans.map((value, index) => <option key={value.plan_id} value={value.plan_id}>{index === 0 ? "最新 · " : ""}{time(value.created_at)}{value.stale ? " · 来源已更新" : ""}</option>)}</select></label>}</div>
        <div className="poster-brief"><div><span className="poster-eyebrow">故事核心</span><h3>{plan.story.premise}</h3><p>{plan.story.core_conflict}</p><span className="poster-emotion">{plan.story.emotional_promise}</span></div><div className="poster-cast">{plan.story.cast.map((character, index) => <div key={`${character.name}-${index}`}><span className="poster-avatar">{character.name.slice(0, 1)}</span><strong>{character.name}</strong><small>{character.role}</small><p>{character.relationship || character.identity}</p></div>)}</div></div>
        {plan.stale && <p className="poster-warning">骨架已更新，此方案保留为历史版本。请基于当前完整骨架重新设计后生成。</p>}
        <div className="poster-directions">{plan.directions.map((direction, index) => <article className="poster-direction" key={direction.id}><div className="poster-direction__visual"><span>{String(index + 1).padStart(2, "0")}</span><Icon name={direction.characters.length > 1 ? "users" : "clapperboard"} className="h-12 w-12" /><small>{direction.medium}</small></div><div className="poster-direction__body"><span className="poster-eyebrow">方向 / {String(index + 1).padStart(2, "0")}</span><h3>{direction.title}</h3><p>{direction.story_promise}</p><dl><dt>主要人物</dt><dd>{direction.characters.join("、") || "依故事场景设计"}</dd><dt>构图</dt><dd>{direction.composition}</dd><dt>色彩</dt><dd>{paletteText(direction.palette)}</dd></dl><button className="poster-button poster-button--small" disabled={paidDisabled} onClick={() => setEditDirection({plan, direction: {...direction}})}><Icon name="pencil" /> 调整这个方向</button></div></article>)}</div>
        <div className="poster-plan-footer"><div><strong>满意这组方向，就开始出图。</strong><p>{generationUsageHint}</p><p>一张完成即可选择；原有封面与历史版本继续保留。</p></div><button className="poster-button poster-button--primary" disabled={paidDisabled || plan.stale || !!data.source.error} onClick={() => void generate()}><Icon name="sparkles" /> 按此方案生成 {plan.directions.length} 张</button></div>
      </> : <div className="poster-empty"><Icon name="clapperboard" className="h-12 w-12" /><h2>从完整故事，设计四种封面表达</h2><p>将核心人物、关系和情绪组织成封面海报。确认方向后，再生成图片。</p><button className="poster-button poster-button--primary" disabled={paidDisabled || !!data.source.error} onClick={() => setDrawer("plan")}>设计 4 个封面方向</button>{data.source.error && <p className="poster-error">{data.source.error}</p>}</div>}
    </section> : <section id="poster-panel-gallery" role="tabpanel" aria-labelledby="poster-tab-gallery">
      <div className="poster-section-heading"><div><h2>候选画廊 <span className="poster-count">{successful}/{set?.requested_count || 4} 张完整图片</span></h2><p>挑选喜欢的一张，继续精修或调整标题。</p>{plan && <p>新批次：{generationUsageHint}</p>}</div><div className="poster-gallery-controls">{plan && <button className="poster-button poster-button--small" disabled={paidDisabled || plan.stale || !!data.source.error} title="按当前所选设计方案生成新的四张候选" onClick={() => void generate()}>再生成 4 张</button>}{sets.length > 0 && <select aria-label="选择封面批次" value={set?.cover_set_id || ""} onChange={event => setSetId(event.target.value)}>{sets.map((value, index) => <option key={value.cover_set_id} value={value.cover_set_id}>{index === 0 ? "最新 · " : ""}{time(value.created_at)} · {value.candidates.filter(finishedImage).length}/{value.requested_count}</option>)}</select>}{set?.candidates.some(candidate => !finishedImage(candidate) && candidate.status === "failed") && <button className="poster-button poster-button--small" disabled={paidDisabled} onClick={() => void retry(set)}>重试失败候选</button>}</div></div>
      {set ? <div className="poster-gallery">{set.candidates.map((candidate, index) => {
        const target = {set, candidate}; const ready = finishedImage(candidate); const selected = chosen(target);
        const original = !ready ? originalFor(set, candidate, sets) : null;
        const visibleImage = ready ? target : original;
        const concept = set.concepts.find(value => value.concept_id === candidate.concept_id);
        const metadata = candidate.poster_metadata;
        const advice = set.advisory?.candidates?.find(value => value.candidate_id === candidate.candidate_id);
        const artwork = candidate.quality_report?.assets as {artwork_url?: string} | undefined;
        const typeAvailable = metadata?.typography_available ?? candidate.typography_available ?? Boolean(candidate.artwork_url || artwork?.artwork_url);
        const title = concept?.visual_strategy || `候选 ${String(index + 1).padStart(2, "0")}`;
        const failure = tasks.find(value => (value.kind === "refine" || value.kind === "refinement") && value.input?.candidate_id === candidate.candidate_id && value.input?.cover_set_id === set.cover_set_id && !activeTask(value) && value.status !== "complete");
        return <article className={`poster-candidate${selected ? " poster-candidate--selected" : ""}`} key={candidate.candidate_id} aria-label={`候选 ${index + 1}`}>
          <div className="poster-artwork">{visibleImage ? <><img src={posterAssetUrl(visibleImage.candidate.url)} alt={`${data.title} · ${title}`} loading="lazy" /><button className="poster-zoom" aria-label={`放大候选 ${index + 1}`} onClick={() => setPreview(visibleImage)}><Icon name="eye" /></button></> : <div className="poster-artwork__empty">{candidate.status === "failed" ? <Icon name="circle-alert" className="h-7 w-7" /> : <span className="poster-spinner" />}<strong>{candidate.status === "failed" ? "本张未生成" : "等待图片保存"}</strong><p>其他已完成图片可直接使用。</p></div>}<span className={`poster-badge${selected ? " poster-badge--selected" : ""}`}>{selected ? "当前使用" : ready ? "可选择" : original ? "原封面 · 新版本未完成" : candidate.status === "failed" ? "生成失败" : "处理中"}</span></div>
          <div className="poster-candidate__body"><span className="poster-eyebrow">候选 / {String(index + 1).padStart(2, "0")}</span><h3>{title}</h3><p className="poster-candidate__description">{concept?.focal_scene || concept?.composition || ""}</p>
            {ready ? <><div className="poster-candidate__actions"><button className={`poster-button${selected ? " poster-button--selected" : " poster-button--primary"}`} disabled={selected || !!busy} onClick={() => void select(target)}><Icon name="circle-check" /> {selected ? "当前使用" : "使用这张"}</button><button className="poster-button" disabled={paidDisabled} onClick={() => setRefinement({...target, prompt: failure?.input?.prompt || "", keep: failure?.input?.keep_composition ?? true})}><Icon name="sparkles" /> {failure ? "重试精修" : "精修"}</button></div><div className="poster-candidate__secondary"><button className="poster-text-button" disabled={!typeAvailable || !!busy} title={!typeAvailable ? "此版本没有独立无字画稿，暂不支持本地重新排版。" : undefined} onClick={() => setTypography(target)}><Icon name="pencil" /> 标题排版</button><a className="poster-text-button" href={posterAssetUrl(candidate.url)} target="_blank" rel="noreferrer">查看原尺寸 <Icon name="chevron-right" /></a></div></> : <>{original && <button className="poster-button poster-button--primary" disabled={!!busy || chosen(original)} onClick={() => void select(original)}>{chosen(original) ? "原封面正在使用" : "使用原封面"}</button>}{candidate.status === "failed" && <button className="poster-button" disabled={paidDisabled} onClick={() => void retry(set, [candidate.candidate_id])}>{"重试这一张"}</button>}</>}
            {(candidate.error || failure) && <details className="poster-details poster-details--warning"><summary>{failure ? "上次精修未完成，原图已保留" : "查看失败原因"}</summary><p>{failure?.error || candidate.error}</p></details>}
            {(candidate.quality_report?.blockers?.length || candidate.quality_report?.evidence?.length) ? <details className="poster-details"><summary>画面建议 <span>不影响选择</span></summary>{candidate.quality_report.blockers?.length ? <ul>{candidate.quality_report.blockers.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p>{candidate.quality_report.evidence?.join("；") || "已保存的画面评价仅供选择与精修参考。"}</p>}</details> : null}
            {advice && <details className="poster-details"><summary>画面建议 <span>不影响选择</span></summary><p>{advice.summary}</p>{advice.suggestions.length > 0 && <ul>{advice.suggestions.map((value, index) => <li key={index}>{value}</li>)}</ul>}</details>}
            <details className="poster-details"><summary>版本与模型</summary><dl><dt>图片版本</dt><dd>{candidate.candidate_id}</dd><dt>请求图片模型</dt><dd>{metadata?.requested_model || candidate.requested_model || "未记录"}</dd><dt>实际图片模型</dt><dd>{metadata?.observed_model || candidate.observed_model || "未返回，无法确认"}</dd>{(metadata?.parent_candidate_id || candidate.parent_candidate_id) && <><dt>来源版本</dt><dd>{metadata?.parent_candidate_id || candidate.parent_candidate_id}</dd></>}</dl></details>
          </div></article>;
      })}</div> : <div className="poster-empty"><Icon name="image" className="h-12 w-12" /><h2>候选封面将在这里逐张出现</h2><p>先确认设计方向，再生成图片。</p><button className="poster-button" onClick={() => chooseTab("plan")}>查看设计方案</button></div>}
      {set?.advisory && <p className="poster-gallery-note">{["queued", "running"].includes(set.advisory.status) ? "正在整理画面建议，可继续选择或精修。" : ["failed", "interrupted"].includes(set.advisory.status) ? "建议暂不可用，不影响使用。" : set.advisory.portfolio_note}</p>}
      {set && <p className="poster-gallery-note"><Icon name="circle-check" /> 完整图片可直接使用，无需等待其他候选或填写理由。精修与排版另存为新版本。</p>}
    </section>}
    <footer className="poster-footer"><span>原画稿、排版与选择版本分别保存</span>{onLegacy && <button className="poster-text-button" onClick={onLegacy}>历史工作室</button>}</footer>
    {drawer === "source" && <PosterDialog title="完整骨架来源" onClose={() => setDrawer(null)}><div className="poster-drawer-body"><p>规划读取当前完整骨架与明确关联的附件，不默认扫描章节正文。</p><dl><dt>文件</dt><dd>{data.source.path || "outputs/input/prompt.md"}</dd><dt>大小</dt><dd>{data.source.bytes.toLocaleString("zh-CN")} 字节</dd><dt>SHA-256</dt><dd>{data.source.sha256 || "未读取"}</dd></dl>{data.source.error && <p className="poster-error">{data.source.error}</p>}<pre className="poster-source">{data.source.preview || "来源正文暂不可用。"}</pre></div></PosterDialog>}
    {drawer === "history" && <PosterDialog title="封面设计历史" onClose={() => setDrawer(null)}><div className="poster-drawer-body"><h3>设计方案</h3>{plans.length ? plans.map((value, index) => <button className="poster-history-row" key={value.plan_id} onClick={() => { setPlanId(value.plan_id); chooseTab("plan"); setDrawer(null); }}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{time(value.created_at)}</strong><small>{value.directions.length} 个方向 · {value.status === "approved" ? "已用于生成" : "待确认"}{value.stale ? " · 来源已更新" : ""}</small></div><Icon name="chevron-right" /></button>) : <p>尚无设计方案</p>}<h3>封面批次与版本</h3>{sets.map((value, index) => <button className="poster-history-row" key={value.cover_set_id} onClick={() => { setSetId(value.cover_set_id); chooseTab("gallery"); setDrawer(null); }}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{time(value.created_at)}</strong><small>{value.candidates.filter(finishedImage).length} 张完整图片{value.cover_set_id === data.active_cover_set_id ? " · 含当前封面" : ""}</small></div><Icon name="chevron-right" /></button>)}<h3>任务记录</h3>{tasks.map(value => <details className="poster-details" key={value.task_id}><summary>{kindName[value.kind] || value.kind} · {({complete: "完成", failed: "未完成", interrupted: "中断", queued: "排队", running: "进行中"})[value.status]} <span>{time(value.created_at)}</span></summary><p>{value.stage}</p><ModelProgress task={value} />{value.error && <p>{value.error}</p>}<small>{value.task_id}</small></details>)}</div></PosterDialog>}
    {drawer === "costs" && <PosterDialog title="模型与费用" onClose={() => setDrawer(null)} wide><div className="poster-drawer-body"><GenerationCostPanel owner={projectId} /><p className="poster-muted">每张图的请求模型和实际观测模型，可在候选卡片的「版本与模型」中查看。未返回的模型身份不作推测。</p></div></PosterDialog>}
    {drawer === "plan" && <PosterDialog title={plan ? "重新设计封面方向" : "设计 4 个封面方向"} onClose={() => setDrawer(null)}><div className="poster-drawer-body poster-form"><p>完整阅读当前骨架，联合设计四种封面表达。此次只设计方向；确认后再生成图片。</p><div className="poster-fields"><label>目标市场<input value={preferences.market} onChange={event => setPreferences(value => ({...value, market: event.target.value}))} placeholder="按骨架设定，可补充" /></label><label>目标读者<input value={preferences.audience} onChange={event => setPreferences(value => ({...value, audience: event.target.value}))} placeholder="按骨架设定，可补充" /></label></div><label>构图偏好<select value={preferences.emphasis} onChange={event => setPreferences(value => ({...value, emphasis: event.target.value}))}><option>由故事决定</option><option>偏人物关系海报</option><option>偏群像</option><option>偏主角</option><option>偏氛围</option></select></label><label>创作要求<textarea maxLength={6000} value={preferences.notes} onChange={event => setPreferences(value => ({...value, notes: event.target.value}))} placeholder="希望突出的角色关系、情绪或视觉风格…" rows={4} /></label><p className="poster-muted">人物身份、核心关系与世界设定保持；允许宣传式同框与艺术化构图。</p><button className="poster-button poster-button--primary" disabled={paidDisabled || !!data.source.error} onClick={() => void createPlan()}>{busy === "plan" ? "正在提交…" : "开始设计 4 个方向"}</button></div></PosterDialog>}
    {editDirection && <PosterDialog title={`调整方向 · ${editDirection.direction.title}`} onClose={() => setEditDirection(null)}><div className="poster-drawer-body poster-form"><p>人物、构图和风格修改会用于下一次出图；保存为新方案，不调用模型，已生成图片保持原样。</p>{([['title', '方向名称'], ['story_promise', '故事承诺'], ['composition', '人物与画面构图'], ['medium', '美术媒介']] as const).map(([field, label]) => <label key={field}>{label}<textarea rows={field === "composition" ? 4 : 2} maxLength={field === "title" ? 180 : field === "story_promise" ? 1600 : field === "medium" ? 300 : 2400} value={editDirection.direction[field]} onChange={event => setEditDirection({...editDirection, direction: {...editDirection.direction, [field]: event.target.value}})} /></label>)}<fieldset className="poster-cast-options"><legend>画面人物</legend><p>选择这个方向中出现的人物，保留骨架中的身份与关系。</p><div>{editDirection.plan.story.cast.map(character => <label className="poster-check" key={character.name}><input type="checkbox" checked={editDirection.direction.characters.includes(character.name)} onChange={event => setEditDirection({...editDirection, direction: {...editDirection.direction, characters: event.target.checked ? [...editDirection.direction.characters, character.name] : editDirection.direction.characters.filter(name => name !== character.name)}})} /><span><strong>{character.name}</strong><small>{character.role}</small></span></label>)}</div>{!editDirection.direction.characters.length && <p>未选人物，将以场景与氛围为主。</p>}</fieldset><label>配色<input value={paletteText(editDirection.direction.palette)} onChange={event => setEditDirection({...editDirection, direction: {...editDirection.direction, palette: event.target.value}})} /></label><details className="poster-details"><summary>详细画面描述</summary><label>图片创作描述<textarea rows={6} maxLength={4500} value={editDirection.direction.image_prompt} onChange={event => setEditDirection({...editDirection, direction: {...editDirection.direction, image_prompt: event.target.value}})} /></label></details><button className="poster-button poster-button--primary" disabled={!!busy} onClick={() => void saveDirection()}>{busy === "edit" ? "正在保存…" : "保存新方案"}</button>{error && <p role="alert" className="poster-error">{error}</p>}</div></PosterDialog>}
    {refinement && <PosterDialog title="原图精修" onClose={() => setRefinement(null)} wide><div className="poster-editor"><div className="poster-editor__preview"><img src={posterAssetUrl(refinement.candidate.url)} alt="保留的原封面" /><span className="poster-caption">原封面保留，精修结果另存为新版本</span></div><div className="poster-editor__controls"><h3>保留喜欢的部分，进一步打磨。</h3><p className="poster-muted">以这张画稿作为参考，只编辑这一张。其他候选和当前选择不变。</p><label>你希望如何优化？<textarea rows={7} maxLength={5000} value={refinement.prompt} onChange={event => setRefinement({...refinement, prompt: event.target.value})} placeholder="例如：保持人物关系与镜头，让女主更坚定，背景人物的关系更清晰，减少桌面杂物。" /></label><label className="poster-check"><input type="checkbox" checked={refinement.keep} onChange={event => setRefinement({...refinement, keep: event.target.checked})} />尽量保留当前构图</label><p className="poster-info">人物身份、核心关系与故事设定始终保持。精修会发起一次新的图片编辑调用。</p>{error && <p className="poster-error" role="alert">{error}</p>}<div className="poster-editor__footer"><button className="poster-button poster-button--primary" disabled={paidDisabled || !refinement.prompt.trim()} onClick={() => void run(`refine:${refinement.set.cover_set_id}:${refinement.candidate.candidate_id}`, {prompt: refinement.prompt, keep: refinement.keep}, requestId => posterApi.refine(projectId, requestId, refinement.set.cover_set_id, refinement.candidate.candidate_id, refinement.prompt.trim(), refinement.keep), result => { setRefinement(null); setSetId(result.result?.cover_set_id || ""); chooseTab("gallery"); })}>{busy.startsWith("refine:") ? "正在提交精修…" : "开始精修这一张"}</button><small>失败后可重试，原图仍可直接选用。</small></div></div></div></PosterDialog>}
    {typography && <PosterTypography projectId={projectId} set={typography.set} candidate={typography.candidate} title={data.title} busy={!!busy} onClose={() => setTypography(null)} onSave={(layout: PosterLayout) => run(`type:${typography.set.cover_set_id}:${typography.candidate.candidate_id}`, layout, requestId => posterApi.typography(projectId, requestId, typography.set.cover_set_id, typography.candidate.candidate_id, layout), result => { setSetId(result.result?.cover_set_id || ""); chooseTab("gallery"); })} />}
    {preview && <PosterDialog title="封面预览" onClose={() => setPreview(null)} wide><div className="poster-editor"><div className="poster-editor__preview"><img src={posterAssetUrl(preview.candidate.url)} alt={data.title} /></div><div className="poster-editor__controls"><h3>{data.title}</h3><p className="poster-muted">{preview.candidate.width} × {preview.candidate.height}</p><div className="poster-thumbnail"><img src={posterAssetUrl(preview.candidate.url)} alt="作品库缩略图" /><span>120 × 180 缩略图</span></div><button className="poster-button poster-button--primary" disabled={chosen(preview) || !!busy} onClick={() => void select(preview)}>{chosen(preview) ? "当前使用" : "使用这张"}</button><button className="poster-button" disabled={paidDisabled} onClick={() => { setRefinement({...preview, prompt: "", keep: true}); setPreview(null); }}>精修这一张</button></div></div></PosterDialog>}
  </div>;
}
