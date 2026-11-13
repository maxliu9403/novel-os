import { useCallback, useEffect, useRef, useState } from 'react';
import { creativeRef, creativeTypesApi, validSelection, isCreativeTypeRef, type AudienceChannel, type CreativeSummary, type CreativeTypeRef } from '../api/creativeTypes';
import { sourceRoutesApi, type SourceAuthorContext, type SourceRouteChoice, type SourceRouteListing, type SourceRouteOperation, type SourceRouteRun, type StoryRoute, type StoryRouteSet } from '../api/sourceRoutes';
import type { SourceSelection } from '../api/sourceBooks';
import { clearLocal, operationFor, readLocal, writeLocal } from '../lib/sourceRouteDraft';
import CreativeTypePicker from './CreativeTypePicker';
import { supportsAudience } from '../lib/audience';
import { validRouteLengthPreferences } from '../lib/routeLengthPreferences';
import { newWorkshopInput, routeCreationInput, languageCountUnit } from '../lib/workshopCreation';
import type { WorkshopInput } from '../api/workshop';
import WorkshopCreationFields from './WorkshopCreationFields';
import '../styles/source-routes.css';

// Old browser drafts may have preferences at the top level. New drafts have one canonical input.
interface RouteDraft { author_note: string; creative_types?: CreativeTypeRef[]; genres?: string[]; audience_channel?: AudienceChannel | null;
  target_total_words?: number; words_per_chapter?: number; chapters?: number; language?: string; creation_input?: WorkshopInput }
interface SavedDraft { feedback: string; scope: string; selected?: { sha: string; route_id: string }; routeDrafts: Record<string, RouteDraft> }
const emptyDraft: SavedDraft = { feedback: '', scope: '', routeDrafts: {} };
const active = (run?: SourceRouteRun | null) => !!run && ['prepared', 'queued', 'running'].includes(run.status) && !run.interrupted && run.worker_active !== false;
const complete = (run: SourceRouteRun) => ['ready', 'complete', 'completed'].includes(run.status);
const failureText = (error: unknown) => error instanceof Error ? error.message : String(error);
const duration = (seconds: number) => seconds >= 60 ? `${Math.round(seconds / 60)} 分钟` : `${Math.round(seconds)} 秒`;
const statusText: Record<string, string> = { prepared: '正在准备整书设计', queued: '等待开始设计', running: '正在研读与设计', ready: '整书路线已保存', complete: '整书路线已保存', completed: '整书路线已保存', paused: '已暂停，进度已保存', cancelled: '已暂停，进度已保存', failed: '本次设计尚未完成', error: '本次设计尚未完成', pending_unknown: '请求结果尚未确认，进度已保存', needs_author: '有一项故事取舍需要明确', needs_capacity: '当前模型容量不足，进度已保存', conflict: '设计内容需要确认，进度已保存', interrupted: '进度已保存，可继续' };
const tabNames = ['情绪与看点', '完整故事走向', '全书悬念与兑现', '原文与新设想'] as const;
const displayValue = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join('\n');
  if (value && typeof value === 'object') return Object.entries(value).filter(([key]) => !key.endsWith('_id') && !key.endsWith('_sha256') && key !== 'source_refs').map(([, item]) => displayValue(item)).filter(Boolean).join('\n');
  return '';
};

export default function SourceRoutes({ sourceId, mode, authorContext = {}, draftKey = 'library', onChoice, onContinue, onStatusChange, onPendingChange, choiceOverride, continueLabel = '采用此路线并展开', disabled = false }: {
  sourceId: string;
  mode: SourceSelection['mode'];
  authorContext?: SourceAuthorContext;
  draftKey?: string;
  onChoice?: (choice: SourceRouteChoice | null) => void;
  onContinue?: (choice: SourceRouteChoice) => Promise<void> | void;
  onStatusChange?: () => void;
  onPendingChange?: (pending: boolean) => void;
  choiceOverride?: SourceRouteChoice | null;
  continueLabel?: string;
  disabled?: boolean;
}) {
  const storageKey = `novelos-route-draft-v1:${draftKey}:${sourceId}:${mode}`;
  const [draft, setDraft] = useState<SavedDraft>(() => {
    const saved = readLocal<SavedDraft>(storageKey) ?? emptyDraft;
    if (!choiceOverride || choiceOverride.source_id !== sourceId || choiceOverride.mode !== mode) return saved;
    const key = `${choiceOverride.route_set_sha256}:${choiceOverride.route_id}`;
    return { ...saved, selected: { sha: choiceOverride.route_set_sha256, route_id: choiceOverride.route_id },
      routeDrafts: { ...saved.routeDrafts, [key]: { author_note: choiceOverride.author_note,
        creation_input: routeCreationInput(choiceOverride, authorContext) } } };
  });
  const [listing, setListing] = useState<SourceRouteListing>();
  const [version, setVersion] = useState(draft.selected?.sha ?? '');
  const [routeSet, setRouteSet] = useState<StoryRouteSet>();
  const [run, setRun] = useState<SourceRouteRun | null>();
  const [catalog, setCatalog] = useState<CreativeSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [importPending, setImportPending] = useState(false);
  useEffect(() => { onPendingChange?.(importPending); return () => onPendingChange?.(false); }, [importPending, onPendingChange]);
  const [error, setError] = useState('');
  const [syncError, setSyncError] = useState('');
  const [additionalCount, setAdditionalCount] = useState(2);
  const [initialCount, setInitialCount] = useState(3);
  const [detailId, setDetailId] = useState('');
  const [tab, setTab] = useState<(typeof tabNames)[number]>('情绪与看点');
  const [refreshKey, setRefreshKey] = useState(0);
  const choiceRef = useRef(onChoice);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  const modal = useRef<HTMLDialogElement>(null);
  useEffect(() => { choiceRef.current = onChoice; }, [onChoice]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { writeLocal(storageKey, draft); }, [storageKey, draft]);
  const refresh = useCallback(() => setRefreshKey(v => v + 1), []);
  useEffect(() => {
    let live = true;
    creativeTypesApi.list().then(data => { if (live) setCatalog(data.items); }).catch(e => { if (live) setError(failureText(e)); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        const value = await sourceRoutesApi.list(sourceId, controller.signal);
        if (!live) return;
        setListing(value); setSyncError('');
        const latestRun = value.active_run?.request?.mode === mode ? value.active_run : value.runs?.find(item => item.request?.mode === mode);
        setRun(latestRun ?? null);
        const versions = value.items.filter(item => item.mode === mode);
        setVersion(current => current || versions[0]?.sha256 || '');
        timer = setTimeout(load, active(latestRun) ? 1800 : 10000);
      } catch (e) {
        if (live) { setSyncError(failureText(e)); timer = setTimeout(load, 5000); }
      }
    };
    void load();
    return () => { live = false; controller.abort(); clearTimeout(timer); };
  }, [sourceId, mode, refreshKey]);
  useEffect(() => {
    if (!version) return;
    let live = true;
    const controller = new AbortController();
    sourceRoutesApi.get(sourceId, version, controller.signal).then(value => {
      if (live) { setRouteSet(value); setSyncError(''); }
    }).catch(e => { if (live) setSyncError(failureText(e)); });
    return () => { live = false; controller.abort(); };
  }, [sourceId, version, refreshKey]);
  const sets = listing?.items.filter(item => item.mode === mode) ?? [];
  const latestSha = sets[0]?.sha256;
  const currentSet = routeSet?.sha256 === version && routeSet.mode === mode ? routeSet : undefined;
  const selected = currentSet?.routes.find(route => draft.selected?.sha === currentSet.sha256 && draft.selected.route_id === route.route_id);
  const selectedKey = selected && currentSet ? `${currentSet.sha256}:${selected.route_id}` : '';
  const selectedDraft = selectedKey ? draft.routeDrafts[selectedKey] : undefined;
  const legacyPreferences = selectedDraft ? Object.fromEntries(Object.entries(selectedDraft).filter(([key, value]) => key !== 'author_note' && key !== 'creation_input' && value !== undefined)) : {};
  const selectedInput = selectedDraft ? newWorkshopInput({ ...(selectedDraft.creation_input ?? { ...currentSet?.author_context, ...authorContext, ...legacyPreferences }), source_book: null }) : undefined;
  const lengthValid = !selectedInput || validRouteLengthPreferences(selectedInput);
  const compatibleLength = !!selected?.length_recommendation && languageCountUnit(selectedInput?.language ?? '') === selected.length_recommendation.count_unit;
  const choice: SourceRouteChoice | null = selected && currentSet && selectedDraft && selectedInput ? {
    source_id: sourceId, route_set_sha256: currentSet.sha256, route_id: selected.route_id, route_title: selected.title, mode,
    author_note: selectedDraft.author_note, creation_input: selectedInput,
    creative_types: selectedInput.creative_types?.filter(isCreativeTypeRef) ?? [], genres: selectedInput.genres, audience_channel: selectedInput.audience_channel ?? null,
    language: selectedInput.language, chapters: selectedInput.chapters, target_total_words: selectedInput.target_total_words, words_per_chapter: selectedInput.words_per_chapter, adoption_scope: 'core_and_payoffs', ...(latestSha && latestSha !== currentSet.sha256 ? { latest_route_set_sha256: latestSha } : {}),
    ...(run && !complete(run) && run.request?.feedback ? { pending_feedback_run_id: run.run_id } : {}),
  } : null;
  const choiceJSON = JSON.stringify(choice);
  useEffect(() => { choiceRef.current?.(JSON.parse(choiceJSON)); }, [choiceJSON]);
  const detail = currentSet?.routes.find(route => route.route_id === detailId);
  useEffect(() => {
    if (!detail || !modal.current) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (typeof modal.current.showModal === 'function') modal.current.showModal();
    else modal.current.setAttribute('open', '');
    return () => { previous?.focus(); };
  }, [detail]);
  function recommend(route: StoryRoute): { creative_types: CreativeTypeRef[]; genres: string[]; audience_channel: AudienceChannel | null } | undefined {
    const ids = [route.type_suggestion.genre_id, ...route.type_suggestion.tag_ids];
    const items = ids.map(id => catalog.find(item => item.id === id && item.status === 'enabled' && item.active_version && item.source_sha256 && supportsAudience(item, route.type_suggestion.audience)));
    if (items.some(item => !item)) return undefined;
    return { creative_types: items.map(item => creativeRef(item!)), genres: items.map(item => item!.name), audience_channel: route.type_suggestion.audience };
  }
  function select(route: StoryRoute) {
    if (!currentSet) return;
    const key = `${currentSet.sha256}:${route.route_id}`;
    const existing = draft.routeDrafts[key];
    const input = newWorkshopInput({ ...currentSet.author_context, ...authorContext });
    const recommendation = recommend(route);
    const types: { creative_types: CreativeTypeRef[]; genres: string[]; audience_channel: AudienceChannel | null } = input.creative_types?.length ? { creative_types: input.creative_types.filter(isCreativeTypeRef), genres: input.genres, audience_channel: input.audience_channel ?? null }
      : recommendation && recommendation.audience_channel === input.audience_channel ? recommendation : { creative_types: [], genres: [], audience_channel: input.audience_channel ?? null };
    const length = route.length_recommendation;
    // A character estimate is not a word estimate. Preserve the historical recommendation,
    // but only prefill compatible units; do not infer the prose language from source analysis.
    const compatible = length && languageCountUnit(input.language) === length.count_unit;
    const creation_input = { ...input, ...types,
      target_total_words: input.target_total_words ?? (compatible ? length.total_words : undefined),
      words_per_chapter: input.words_per_chapter ?? (compatible ? length.words_per_chapter : undefined),
      chapters: input.chapters ?? length?.chapters };
    const defaults: RouteDraft = { author_note: '', creation_input };
    setDraft(value => ({ ...value, selected: { sha: currentSet.sha256, route_id: route.route_id }, routeDrafts: { ...value.routeDrafts, [key]: existing ?? defaults } }));
    setDetailId('');
  }
  function updateSelected(change: { author_note?: string; creation_input?: WorkshopInput }) {
    if (!selectedKey || !selectedDraft || !selectedInput) return;
    setDraft(value => ({ ...value, routeDrafts: { ...value.routeDrafts,
      [selectedKey]: { author_note: selectedDraft.author_note, creation_input: selectedInput, ...change } } }));
  }
  function updateCreation(change: Partial<WorkshopInput>) {
    if (selectedInput) updateSelected({ creation_input: { ...selectedInput, ...change } });
  }
  async function submit(operation: SourceRouteOperation) {
    if (busyRef.current || active(run) || run?.worker_active || run?.status === 'pending_unknown' || disabled) return;
    busyRef.current = true; setBusy(true); setError('');
    const feedback = draft.feedback;
    const context = selectedInput ?? newWorkshopInput({ ...currentSet?.author_context, ...authorContext });
    const payload = { operation, mode, author_context: { ...context, creative_types: context.creative_types?.filter(isCreativeTypeRef) ?? [] }, feedback,
      ...(currentSet ? { base_route_set_sha256: currentSet.sha256 } : {}),
      ...(draft.scope ? { target_route_ids: [draft.scope] } : {}),
      ...(operation === 'initial_routes' ? { requested_count: initialCount } : operation === 'expand_routes' ? { requested_count: additionalCount } : {}),
    };
    const key = `${storageKey}:operation`;
    try {
      const started = await sourceRoutesApi.start(sourceId, operationFor(key, payload));
      clearLocal(key);
      if (!mounted.current) return;
      setRun(started);
      setDraft(value => ({ ...value, feedback: value.feedback === feedback ? '' : value.feedback }));
      if (started.route_set_sha256) setVersion(started.route_set_sha256);
      refresh();
      onStatusChange?.();
    } catch (e) { if (mounted.current) setError(failureText(e)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function control(action: 'pause' | 'resume') {
    if (!run || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError('');
    try { const value = await sourceRoutesApi[action](sourceId, run.run_id); if (mounted.current) { setRun(value); refresh(); onStatusChange?.(); } }
    catch (e) { if (mounted.current) setError(failureText(e)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function retryUnknown() {
    const recovery = run?.recovery;
    if (!run || busyRef.current || disabled || run.worker_active || !recovery?.can_retry_unknown || !recovery.request_sha256 || !recovery.receipt_sha256) return;
    busyRef.current = true; setBusy(true); setError('');
    const payload = operationFor(`${storageKey}:retry:${run.run_id}`, {
      action: 'retry_unknown' as const,
      request_sha256: recovery.request_sha256, receipt_sha256: recovery.receipt_sha256,
    });
    try {
      const value = await sourceRoutesApi.retryUnknown(sourceId, run.run_id, payload);
      // Keep this operation binding even after acknowledgement: stale polling or
      // a lost browser response must not authorize a second attempt on this receipt.
      if (mounted.current) { setRun(value); refresh(); onStatusChange?.(); }
    } catch (e) { if (mounted.current) setError(failureText(e)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function proceed() {
    if (!choice || !onContinue || busyRef.current || importPending || !lengthValid) return;
    busyRef.current = true; setBusy(true); setError('');
    try { await onContinue(choice); } catch (e) { if (mounted.current) setError(failureText(e)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  const unsettled = run && !complete(run);
  const canResume = unsettled && (run.recovery?.can_resume ?? run.status !== 'pending_unknown');
  const canRetryUnknown = run?.recovery?.can_retry_unknown && run.recovery.request_sha256 && run.recovery.receipt_sha256;
  const blockedSubmission = active(run) || run?.worker_active || run?.status === 'pending_unknown';
  const execution = run?.execution ?? run?.recovery?.execution;
  const feedbacks = currentSet?.feedback_history.map(item => item.exact_text ?? item.feedback ?? item.text ?? '').filter(Boolean) ?? [];
  return <section className="source-routes" aria-label="整书路线工作台">
    <header className="source-routes-heading"><div><span className="source-routes-eyebrow">从全部资料到一部完整故事</span><h3>选择值得继续写的方向</h3><p>先比较人物选择、情绪回报和故事结局，再一起打磨骨架。</p></div>
      <button type="button" className="btn-ghost" onClick={() => { refresh(); onStatusChange?.(); }}>刷新路线</button></header>
    {syncError && <p className="source-route-notice" role="status">路线状态暂未同步，已有内容仍保留。<button type="button" onClick={refresh}>重试同步</button></p>}
    {error && <p role="alert" className="source-route-error">{error}</p>}
    {run && <div className="source-route-run" aria-live="polite"><strong>{run.interrupted ? statusText.interrupted : run.worker_active && run.status === 'pending_unknown' ? '正在恢复未完成的设计' : statusText[run.status] ?? '设计状态待同步'}</strong>
      {run.total > 0 && <span>已保存 {run.completed} / {run.total} 个步骤</span>}
      {run.status === 'pending_unknown' && run.worker_active !== true && <p className="knowledge-muted">本次设计已停止</p>}
      {unsettled && !active(run) && !run.worker_active && run.recovery?.message && <p>{run.recovery.message}</p>}
      {execution?.elapsed_seconds !== undefined && <p className="knowledge-muted">已等待 {duration(execution.elapsed_seconds)}{execution.timeout_seconds !== undefined ? ` · ${execution.timeout_scope === 'request' ? '单次请求' : execution.timeout_scope === 'local_process' ? '本地执行' : execution.timeout_scope === 'idle' ? '无响应等待' : '本次等待'}上限 ${duration(execution.timeout_seconds)}` : ''}</p>}
      <div className="source-route-actions">{active(run) ? <button type="button" className="btn-secondary" disabled={busy || run.cancel_requested || run.pause_requested} onClick={() => void control('pause')}>{run.cancel_requested || run.pause_requested ? '正在保存并暂停…' : '暂停设计'}</button>
        : !run.worker_active && canResume && <button type="button" className="btn-secondary" disabled={busy || disabled} onClick={() => void control('resume')}>继续未完成的设计</button>}
        {!run.worker_active && canRetryUnknown && <button type="button" className="btn-secondary" disabled={busy || disabled} onClick={() => void retryUnknown()}>重新执行未完成步骤</button>}</div>
      {canRetryUnknown && <p>保留资料和旧记录；原请求结果无法查询，重新执行可能产生额外模型费用。</p>}
      {run.error && <details><summary>查看本次未完成原因</summary><p>{run.error}</p></details>}
      <details><summary>运行详情</summary><p>当前阶段：{run.stage} · 模型调用：{run.model_calls} 次</p><p>原文、已保存方案和检查点保留；不会要求填写技术对账。</p></details>
    </div>}
    {sets.length > 0 && <label className="source-route-version">方案版本<select aria-label="整书路线版本" value={version} onChange={e => { setVersion(e.target.value); setDetailId(''); setDraft(value => ({ ...value, selected: undefined, scope: '' })); }}>
      {sets.map((item, index) => <option key={item.sha256} value={item.sha256}>{index === 0 ? '最新 · ' : '历史 · '}{new Date(item.created_at).toLocaleString()}</option>)}
    </select></label>}
    {latestSha && version && latestSha !== version && <p className="source-route-notice">新一版完整方案已保存。<button type="button" onClick={() => { setVersion(latestSha); setDetailId(''); setDraft(value => ({ ...value, selected: undefined, scope: '' })); }}>查看新方案</button>原版本仍可从历史中查看。</p>}
    {currentSet && <>
      {currentSet.understanding && <details className="source-route-understanding"><summary>{mode === 'source_development' ? '共同承接点与原作吸引力' : mode === 'original_rewrite' ? '来源理解与本次保留范围' : '来源理解与可借鉴机制'}</summary>
        <p>{displayValue(currentSet.understanding.narrative_endpoint)}</p><p>{displayValue(currentSet.understanding.opening_analysis)}</p><p>{displayValue(currentSet.understanding.summary_projection)}</p><p className="knowledge-muted">吸引力解释是基于资料的编辑判断；未提供的原作后续保持未知。</p></details>}
      {(unsettled || latestSha !== currentSet.sha256) && <p className="source-route-notice">当前显示已保存版本。后续补充尚未全部反映在这些场面中；可以保留此方向，带上新要求进入讨论。</p>}
      <div className="source-route-grid">{currentSet.routes.map((route, index) => <article className={`source-route-card ${selected?.route_id === route.route_id ? 'is-selected' : ''}`} key={route.route_id}>
        <div className="source-route-card-top"><span>{String(index + 1).padStart(2, '0')}</span>{currentSet.recommended_route_id === route.route_id && <span className="source-route-badge">推荐方向</span>}{selected?.route_id === route.route_id && <span className="source-route-badge is-chosen">已选</span>}</div>
        <h4>{route.title}</h4><p className="source-route-promise">{route.reader_promise}</p>
        <div className="source-route-highlight"><strong>第一幕如何抓人</strong><p>{route.causal_arc.opening}</p></div>
        {route.highlights[0] && <div className="source-route-highlight"><strong>值得期待的场面</strong><p>{route.highlights[0].scene}</p></div>}
        <div className="source-route-ending"><strong>结局回报</strong><p>{route.causal_arc.ending}</p></div>
        <p className="source-route-difference">{route.difference_from_existing}</p>
        <div className="source-route-tags">{[route.type_suggestion.genre_id, ...route.type_suggestion.tag_ids].map(id => <span key={id}>{catalog.find(item => item.id === id)?.name ?? id}</span>)}</div>
        <div className="source-route-card-actions"><button type="button" className="btn-secondary" onClick={() => { setTab('情绪与看点'); setDetailId(route.route_id); }}>查看完整设计 · {route.title}</button><button type="button" className={selected?.route_id === route.route_id ? 'btn-secondary' : 'btn-primary'} aria-pressed={selected?.route_id === route.route_id} disabled={disabled} onClick={() => select(route)}>{selected?.route_id === route.route_id ? '已选此方向' : `选这条路线 · ${route.title}`}</button></div>
      </article>)}</div>
      <p className="source-route-recommendation"><strong>推荐理由</strong> {currentSet.recommendation_reason}</p>
      {feedbacks.length > 0 && <details className="source-route-feedback-history"><summary>本次按哪些意见调整</summary><ol>{feedbacks.map((item, index) => <li key={index}>{item}</li>)}</ol></details>}
    </>}
    {!currentSet && !active(run) && <p className="source-route-empty">基于全部已提供资料，设计彼此不同、包含高潮和结局的整书路线。旧阅读分析与历史提案会保留。</p>}
    <section className="source-route-feedback" aria-label="继续分析与补充">
      <label>我想调整 / 补充的方向<textarea value={draft.feedback} onChange={e => setDraft(value => ({ ...value, feedback: e.target.value }))} placeholder="例如：保留 A 不复合的结局，加强事业线，再给两个不同方向。也可以指出原文理解有误。" /></label>
      <p className="knowledge-muted">输入会保留为草稿；点击后才提交。补充不会自动采用任何路线。</p>
      {currentSet && <label>本次修改范围<select aria-label="本次修改范围" value={draft.scope} onChange={e => setDraft(value => ({ ...value, scope: e.target.value }))}><option value="">当前版本全部方案</option>{currentSet.routes.map(route => <option key={route.route_id} value={route.route_id}>{route.title}</option>)}</select></label>}
      <div className="source-route-actions">{currentSet ? <>
        <button type="button" className="btn-secondary" disabled={busy || disabled || blockedSubmission || !draft.feedback.trim()} onClick={() => void submit('revise_routes')}>按补充完善方案</button>
        <label className="source-route-count">新增<input type="number" min="1" step="1" aria-label="新增路线数量" value={additionalCount} onChange={e => setAdditionalCount(Math.max(1, Math.floor(Number(e.target.value) || 1)))} />条</label>
        <button type="button" className="btn-primary" disabled={busy || disabled || blockedSubmission} onClick={() => void submit('expand_routes')}>保留已有，再给新方案</button>
      </> : <><label className="source-route-count">首批<input type="number" min="1" step="1" aria-label="首批路线数量" value={initialCount} onChange={e => setInitialCount(Math.max(1, Math.floor(Number(e.target.value) || 1)))} />条</label><button type="button" className="btn-primary" disabled={busy || disabled || blockedSubmission} onClick={() => void submit('initial_routes')}>生成首批整书路线</button></>}</div>
    </section>
    {selected && selectedDraft && selectedInput && <fieldset disabled={busy || disabled} className="source-route-selection min-w-0" aria-label="所选路线与题材"><h4>以「{selected.title}」为讨论方向</h4><p>采用核心走向与情绪回报，进入头脑风暴展开细节。具体实现可以优化；改变核心方向会单独说明。</p>
      <div className="source-route-type-suggestion"><strong>本路线推荐</strong><p>{selected.type_suggestion.reason}</p><button type="button" className="btn-secondary" disabled={disabled || !recommend(selected)} onClick={() => { const value = recommend(selected); if (value) updateCreation(value); }}>采用这组推荐</button>{!recommend(selected) && <p>部分推荐条目当前不可用，请在下方选择有效题材。</p>}</div>
      <CreativeTypePicker refs={selectedInput.creative_types} channel={selectedInput.audience_channel} onChange={(genres, creative_types, audience_channel) => updateCreation({ genres, creative_types, audience_channel: audience_channel ?? null })} />
      <section className="source-route-length" aria-label="路线篇幅建议">
        <div className="flex flex-wrap items-center justify-between gap-3"><h5 className="text-[17px] font-semibold">为这条故事预留多少篇幅</h5><span className="text-[12px] text-ink-muted">可修改 · 进入讨论后细化分章</span></div>
        {selected.length_recommendation ? <><p className="mt-2 leading-7">路线初步建议：约 {selected.length_recommendation.total_words.toLocaleString()} {selected.length_recommendation.count_unit === 'words' ? '词' : '字'} · {selected.length_recommendation.chapters} 章 · 每章不超过 {selected.length_recommendation.words_per_chapter.toLocaleString()} {selected.length_recommendation.count_unit === 'words' ? '词' : '字'}。</p><p className="mt-1 text-[14px] leading-7 text-ink-muted">{selected.length_recommendation.reason}</p></> : <p className="mt-2 text-[14px] leading-7 text-ink-muted">这份历史路线尚未保存篇幅估算。可以先填写偏好，进入头脑风暴后会按完整故事推荐篇幅与每章字数。</p>}
        {selected.length_recommendation && !compatibleLength && <p className="source-route-notice">原路线按{selected.length_recommendation.count_unit === 'words' ? '词' : '字'}估算，与当前正文语言的计数方式不同。总篇幅与每章数字未自动套用；可自行填写，或进入讨论后按完整故事重新估算。</p>}
        <div className="mt-5"><WorkshopCreationFields section="preferences" value={selectedInput} onChange={updateCreation} onPendingChange={setImportPending} disabled={disabled || busy} route newCreation /></div>
        {!lengthValid && <p role="status" className="mt-2 text-[13px] text-amber-800">篇幅请填写正整数，章节数不超过 511；也可留空交由模型建议。</p>}
        {selected.length_recommendation && compatibleLength && <button type="button" className="btn-ghost mt-2 !px-0 !text-violet" disabled={disabled || busy} onClick={() => updateCreation({ target_total_words: selected.length_recommendation!.total_words, words_per_chapter: selected.length_recommendation!.words_per_chapter, chapters: selected.length_recommendation!.chapters })}>使用本路线的篇幅建议</button>}
      </section>
      <details className="mb-5 rounded-2xl border border-paper-line bg-white/45 p-4" open><summary className="cursor-pointer text-[14px] font-medium">书名、作者与故事补充</summary><div className="mt-4"><WorkshopCreationFields section="story" value={selectedInput} onChange={updateCreation} onPendingChange={setImportPending} disabled={disabled || busy} required={false} /></div></details>
      <label>我想保留或改变的地方<textarea value={selectedDraft.author_note} onChange={e => updateSelected({ author_note: e.target.value })} placeholder="选填。这里的文字会作为你的实际意见保存，模型路线不会冒充你的原话。" /></label>
      {draft.feedback.trim() && <p className="knowledge-muted">上方尚未提交的分析意见继续保留为草稿。进入讨论会带上已提交要求和这里的交接意见。</p>}
      {onContinue && <button type="button" className="btn-primary source-route-continue" disabled={busy || disabled || importPending || !lengthValid || !validSelection(selectedInput.creative_types ?? []) || !selectedInput.audience_channel} onClick={() => void proceed()}>{busy ? '正在保存并交接…' : choice?.latest_route_set_sha256 || unsettled ? '以此方案为基础，带上新要求进入讨论' : continueLabel}</button>}
    </fieldset>}
    {detail && <dialog ref={modal} className="source-route-dialog" aria-labelledby="source-route-dialog-title" onCancel={event => { event.preventDefault(); event.stopPropagation(); setDetailId(''); }} onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation(); }}>
      <header><div><span className="source-routes-eyebrow">完整故事路线</span><h3 id="source-route-dialog-title">{detail.title}</h3></div><button type="button" className="btn-ghost" aria-label="关闭路线详情" onClick={() => setDetailId('')}>关闭</button></header>
      <div role="tablist" aria-label="路线设计内容">{tabNames.map((name, index) => <button type="button" role="tab" aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} key={name} onClick={() => setTab(name)} onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabNames.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabNames.length) % tabNames.length; setTab(tabNames[next]); (event.currentTarget.parentElement?.children[next] as HTMLButtonElement)?.focus(); }}>{name}</button>)}</div>
      <div role="tabpanel" className="source-route-detail-content">
        {tab === '情绪与看点' && <><p className="source-route-promise">{detail.reader_promise}</p>{detail.emotional_design.map((beat, index) => <article className="source-route-emotion" key={index}><span>{String(index + 1).padStart(2, '0')}</span><div><h4>{beat.expectation}</h4><p>{beat.scene}</p><dl><div><dt>人物的选择</dt><dd>{beat.choice}</dd></div><div><dt>付出的代价</dt><dd>{beat.cost}</dd></div><div><dt>关系与感受变化</dt><dd>{beat.change}</dd></div><div><dt>期待怎样兑现</dt><dd>{beat.payoff}</dd></div></dl></div></article>)}<h4>值得展开的看点</h4>{detail.highlights.map((item, index) => <article key={index}><p>{item.scene}</p><p className="knowledge-muted">{item.story_value}</p></article>)}</>}
        {tab === '完整故事走向' && <>{Object.entries(detail.causal_arc).map(([key, value]) => <article key={key}><h4>{{ opening: '破局与承接', development: '欲望与阻碍', turning_point: '关键抉择与代价', climax: '高潮如何破局', ending: '终局与情绪落点' }[key]}</h4><p>{value}</p></article>)}<h4>人物与关系</h4><ul>{detail.character_relationship_arcs.map((item, index) => <li key={index}>{item}</li>)}</ul><h4>重要取舍</h4><ul>{detail.tradeoffs.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
        {tab === '全书悬念与兑现' && detail.long_hooks_and_payoffs.map((item, index) => <article key={index}><h4>{item.question}</h4><p>{item.development}</p><p><strong>兑现：</strong>{item.payoff}</p></article>)}
        {tab === '原文与新设想' && <><h4>原文承接依据</h4><p>{detail.continuity_basis}</p><h4>新增设想，尚可调整</h4><ul>{detail.proposed_assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul>{detail.changes_from_parent && <><h4>这一版改变了什么</h4><p>{detail.changes_from_parent}</p></>}<details><summary>来源引用</summary><p>{detail.source_refs.join(' · ')}</p></details></>}
      </div><footer><button type="button" className="btn-primary" disabled={disabled} onClick={() => select(detail)}>选这条路线，继续调整</button></footer>
    </dialog>}
  </section>;
}
