import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import "../styles/cover-studio.css";
import "../styles/cover-repair.css";
import { api, type CoverCandidate, type CoverDirection, type CoverGenerationTask, type CoverRefinementReplanContext, type CoverRepairTask, type CoverSet, type CoverStoryFacts, type CoverStoryFactsConfirmation, type JobStatus, type ProjectDetail, type StudioCoverStatus } from "../api/client";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import Modal, { Field, fieldClass, textareaClass } from "../components/Modal";
import { useToast } from "../components/toastContext";
import { useConfirm } from "../components/confirmContext";
import CoverSkeletonPanel, { type CoverAnalysisNotice } from "../components/CoverSkeletonPanel";
import CoverWorkspaceTabs, { type CoverWorkspaceTab } from "../components/CoverWorkspaceTabs";
import GenerationCostPanel from "../components/GenerationCostPanel";
import { useCoverDirectionTask } from "../components/useCoverDirectionTask";
import CoverQualityDetails from "../components/CoverQualityDetails";
import CoverCommercialReview from "../components/CoverCommercialReview";
import CoverDirectionTaskStatus from "../components/CoverDirectionTaskStatus";
import { useCoverGenerationTask } from "../components/useCoverGenerationTask";
import CoverGenerationTaskStatus from "../components/CoverGenerationTaskStatus";
import { useCoverRepairTask } from "../components/useCoverRepairTask";
import CoverRepairTaskPanel, { CoverRepairStartDialog } from "../components/CoverRepairTaskPanel";
import CoverRefinementRetryActions from "../components/CoverRefinementRetryActions";
import { isRefinementConcept, refinementReferenceUrl, refinementReplanContext, refinementSavedResult, refinementUnavailableReason, repairKind } from "../lib/coverRepair";
import { workshopApi } from "../api/workshop";


const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function isCoverWorkspaceTab(value: string | null): value is CoverWorkspaceTab {
  return value !== null && ["gallery", "directions", "analysis", "costs"].includes(value);
}

type CoverProgress = {
  mode: "retry";
  completed: number;
  total: number;
  phase: "preparing" | "rendering";
  candidateNumber?: number;
};

type JobProgressContext = { mode: "retry"; candidateNumber: number };

export default function CoverStudio() {
  const {id = "", sessionId} = useParams();
  return <CoverStudioWorkspace key={sessionId ? `workshop:${sessionId}` : id} />;
}

function CoverStudioWorkspace() {
  const { id: projectId = "", sessionId } = useParams();
  const id = sessionId ? `workshop:${sessionId}` : projectId;
  const [searchParams, setSearchParams] = useSearchParams();
  const tabAnchor = useRef<HTMLDivElement>(null);
  const tabNavigation = useRef(0);
  useEffect(() => { tabNavigation.current += 1; }, [searchParams]);
  const selectTab = (tab: CoverWorkspaceTab) => {
    tabNavigation.current += 1;
    // Returning from a long panel should reveal the beginning of the next one.
    if (tabAnchor.current && tabAnchor.current.getBoundingClientRect().top < 0) {
      tabAnchor.current.scrollIntoView({block: "start"});
    }
    setSearchParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("tab", tab);
      return next;
    });
    document.getElementById(`cover-tab-${tab}`)?.focus({preventScroll: true});
  };
  const owner = useRef(id);
  useEffect(() => {
    owner.current = id;
    return () => { owner.current = ""; };
  }, [id]);
  const toast = useToast();
  const confirm = useConfirm();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [settings, setSettings] = useState<StudioCoverStatus | null>(null);
  const [sets, setSets] = useState<CoverSet[]>([]);
  const [directions, setDirections] = useState<CoverDirection[]>([]);
  const [directionsLoaded, setDirectionsLoaded] = useState(false);
  const directionRead = useRef(0);
  const coverRead = useRef(0);
  const taskSelectionTime = useRef(0);
  const [selectedSetId, setSelectedSetId] = useState("");
  const [loadedProjectId, setLoadedProjectId] = useState("");
  const [operation, setBusy] = useState("");
  const [progress, setProgress] = useState<CoverProgress | null>(null);
  const [error, setError] = useState("");
  const [repairTarget, setRepairTarget] = useState<{owner: string; candidate: CoverCandidate; number: number; setId: string; revision: number; mode: "repair" | "refinement"; initialPrompt?: string} | null>(null);
  const [repairError, setRepairError] = useState("");
  const [analysisId, setAnalysisId] = useState("");
  const [analysisNotice, setAnalysisNotice] = useState<CoverAnalysisNotice | null>(null);
  const [freshness, setFreshness] = useState("require_current_head");
  const chooseAnalysis = useCallback((value: string, policy: string) => {
    setAnalysisId(value); setFreshness(policy);
  }, []);
  const [missingFacts, setMissingFacts] = useState<{ projectId: string; facts: CoverStoryFacts } | null>(null);
  const completeDirection = useCallback((next: CoverDirection[], notify: boolean) => {
    directionRead.current += 1;
    setDirections(next);
    setDirectionsLoaded(true);
    if (notify) toast("美术方向已生成，等待审核", "success");
  }, [toast]);
  const directionTask = useCoverDirectionTask(id, completeDirection);
  const directionPlanning = directionTask.task?.status === "running"
    || directionTask.task?.status === "queued" || operation === "direction:create";

  const applySets = useCallback((next: CoverSet[], preferredSetId?: string) => {
    setSets(next);
    setSelectedSetId((current) => (
      preferredSetId && next.some((item) => item.cover_set_id === preferredSetId)
        ? preferredSetId
        : current && next.some((item) => item.cover_set_id === current)
          ? current
        : next[0]?.cover_set_id || ""
    ));
  }, []);

  const updateGeneratedSets = useCallback((next: CoverSet[], task: CoverGenerationTask, notify: boolean) => {
    const timestamp = Date.parse(task.created_at) || 0;
    if (timestamp < taskSelectionTime.current) return;
    taskSelectionTime.current = timestamp;
    coverRead.current += 1;
    applySets(next, task.cover_set_id || undefined);
    if (notify && task.status === "completed") toast(task.result_status === "partial" || (task.progress?.failed || 0) > 0
      ? "封面批次已处理，部分图片生成失败" : "封面候选图已保存，请查看质量审核结果", "success");
  }, [applySets, toast]);
  const generationTask = useCoverGenerationTask(id, updateGeneratedSets);
  const updateRepairSets = useCallback((next: CoverSet[], task: CoverRepairTask, notify: boolean) => {
    const timestamp = Date.parse(task.created_at) || 0;
    if (timestamp < taskSelectionTime.current) return;
    taskSelectionTime.current = timestamp;
    coverRead.current += 1;
    applySets(next, task.cover_set_id || undefined);
    if (notify && task.status === "completed") toast(`单张${task.kind === "refinement" ? "精修" : "修复"}结果已保存，请检查图片和审核结果`, "success");
  }, [applySets, toast]);
  const repairTask = useCoverRepairTask(id, updateRepairSets);
  const busy = operation || (generationTask.busy ? "generate" : repairTask.busy ? "repair" : directionTask.busy ? "direction:create" : "");

  const load = useCallback(() => Promise.all([
      sessionId ? workshopApi.get(sessionId).then(value => ({
        id, title: value.input.title || "讨论中的小说", genre: value.input.genres.join("、"),
        author: value.input.author, chapter_count: 0, status: value.status, style: {},
      } as ProjectDetail)) : api.project(id), api.studioCover(), api.covers(id),
    ]), [id, sessionId]);

  useEffect(() => {
    let live = true;
    const request = coverRead.current;
    load()
      .then(([nextProject, nextSettings, nextSets]) => {
        if (!live) return;
        setAnalysisId("");
        setFreshness("require_current_head");
        setProject(nextProject);
        setSettings(nextSettings);
        if (request === coverRead.current) applySets(nextSets);
        setError("");
      })
      .catch((cause) => live && setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => live && setLoadedProjectId(id));
    return () => { live = false; };
  }, [applySets, id, load]);

  useEffect(() => {
    let live = true;
    const request = ++directionRead.current;
    api.coverDirections(id)
      .then((next) => live && request === directionRead.current && setDirections(Array.isArray(next) ? next : []))
      .catch(() => live && request === directionRead.current && setDirections([]))
      .finally(() => live && request === directionRead.current && setDirectionsLoaded(true));
    return () => { live = false; };
  }, [id, repairTask.task?.direction_id]);

  const loading = loadedProjectId !== id;

  const current = useMemo(
    () => sets.find((item) => item.cover_set_id === selectedSetId) || sets[0] || null,
    [selectedSetId, sets],
  );
  const batchDirections = useMemo(() => directions.filter(direction => !direction.source_binding?.single_candidate_repair), [directions]);
  const repairDirectionIsLatest = Boolean(directions[0]?.source_binding?.single_candidate_repair);
  const approvedDirection = !repairDirectionIsLatest && batchDirections[0]?.status === "approved" ? batchDirections[0] : null;
  const requestedTab = searchParams.get("tab");
  const selectedTab: CoverWorkspaceTab = isCoverWorkspaceTab(requestedTab) ? requestedTab
    : sets.length > 0 || generationTask.task || repairTask.task ? "gallery"
    : batchDirections.length > 0 || directionTask.task ? "directions" : "analysis";
  // A completed job remains useful until its exact saved direction is approved.
  // Never dismiss running tasks, sync failures, or a different direction's result.
  const directionCompletionApproved = directionTask.task?.status === "completed"
    && !directionTask.checking && !directionTask.connectionError
    && directions.some(direction => direction.direction_id === directionTask.task?.direction_id && direction.status === "approved");
  const generationCount = approvedDirection?.plans.length || settings?.count || 4;
  const generationAllowed = Boolean(settings?.configured && approvedDirection);
  const retryingBatch = Boolean(approvedDirection && generationTask.task?.direction_id === approvedDirection.direction_id
    && ["failed", "interrupted"].includes(generationTask.task.status));
  const generationLabel = `${retryingBatch ? "重新生成" : "生成"} ${generationCount} 张封面`;
  const generationDisabledReason = generationTask.connectionError || generationTask.checking
    ? "正在同步封面生成状态，请稍候"
    : generationTask.busy
    ? "本批封面正在后台处理"
    : repairTask.busy
    ? `正在处理或同步单张封面${repairTask.task?.kind === "refinement" ? "精修" : "修复"}`
    : directionTask.connectionError || directionTask.checking
    ? "正在同步美术方向状态，请稍候"
    : directionPlanning
    ? "美术方向正在规划，完成后请先审核方案"
    : repairDirectionIsLatest
    ? "最新方案仅用于单张封面；请在任务面板确认，或重新规划整批方向"
    : !settings?.configured
    ? "请先配置封面模型"
    : !directionsLoaded
      ? "正在读取美术方向"
      : !batchDirections[0]
        ? analysisId ? "请先创建美术方向" : "请先分析完整骨架"
        : batchDirections[0].status === "stale"
          ? "当前美术方向已过期，请先创建并批准新方向"
          : !approvedDirection
            ? "请先审核并批准美术方向"
            : "";

  const waitForJob = async (initial: JobStatus, context: JobProgressContext) => {
    let job = initial;
    while (job.status === "running") {
      await wait(700);
      const [nextJob, nextSets] = await Promise.all([api.getJob(job.job_id), api.covers(id)]);
      job = nextJob;
      setProgress(currentProgress => currentProgress?.candidateNumber === context.candidateNumber
        ? {...currentProgress, phase: "rendering"} : currentProgress);
      applySets(nextSets);
    }
    if (job.status === "error") throw new Error(job.error || "封面任务失败");
  };

  const reconcileSets = async () => {
    try {
      applySets(await api.covers(id));
    } catch {
      // Preserve the operation error; the next page refresh can retry reconciliation.
    }
  };

  const reconcileDirections = async () => {
    try {
      setDirections(await api.coverDirections(id));
    } catch {
      // Preserve the operation error; the next page refresh can retry reconciliation.
    }
  };

  const generate = async () => {
    if (!settings || !generationAllowed || !approvedDirection || busy) return;
    selectTab("gallery");
    setError("");
    try {
      await generationTask.start(generationCount, {
        direction_id: approvedDirection.direction_id,
        approved_direction_sha256: approvedDirection.direction_sha256,
      });
    } catch (cause) {
      if (owner.current !== id) return;
      await reconcileDirections();
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    }
  };

  const approveDirection = async (direction: CoverDirection, extraImageAttempts = 0) => {
    const navigationAtStart = tabNavigation.current;
    setBusy(`direction:${direction.direction_id}`);
    try {
      const approved = await (direction.source_binding
        ? api.approveCoverDirection(id, direction.direction_id, direction.brief_sha256, direction.direction_sha256, true, extraImageAttempts)
        : api.approveCoverDirection(id, direction.direction_id, direction.brief_sha256, direction.direction_sha256));
      if (owner.current !== id) return;
      setDirections((items) => items.map((item) => (
        item.direction_id === approved.direction_id ? approved : item
      )));
      if (tabNavigation.current === navigationAtStart) selectTab("gallery");
      toast("美术方向已批准", "success");
    } catch (cause) {
      if (owner.current !== id) return;
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    } finally {
      if (owner.current === id) setBusy("");
    }
  };

  const planDirection = async () => {
    if (!settings || owner.current !== id) return;
    await directionTask.start(settings.count, analysisId, freshness);
  };

  const createDirection = async () => {
    if (!settings || busy) return;
    selectTab("directions");
    setBusy("direction:create");
    setError("");
    try {
      if (analysisId) {
        await planDirection();
        return;
      }
      if (!directions[0] || directions[0].source_binding || sessionId) {
        throw new Error("请先分析完整骨架，或选择已完成的骨架分析。旧封面仍可查看。 ");
      }
      const facts = await api.coverStoryFacts(id);
      if (owner.current !== id) return;
      if ((facts.blocking_pending_fields || facts.pending_fields).length > 0) {
        setMissingFacts({ projectId: id, facts });
        return;
      }
      await planDirection();
    } catch (cause) {
      if (owner.current !== id) return;
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    } finally {
      if (owner.current === id) setBusy("");
    }
  };

  const confirmStoryFacts = async (body: CoverStoryFactsConfirmation) => {
    setBusy("direction:facts");
    try {
      const facts = await api.confirmCoverStoryFacts(id, body);
      if (owner.current !== id) return;
      if ((facts.blocking_pending_fields || facts.pending_fields).length > 0) {
        setMissingFacts({ projectId: id, facts });
        return;
      }
      setMissingFacts(null);
      setBusy("direction:create");
      try {
        await planDirection();
      } catch (cause) {
        if (owner.current !== id) return;
        const message = cause instanceof Error ? cause.message : String(cause);
        setError(message);
        toast(message, "error");
      }
    } finally {
      if (owner.current === id) setBusy("");
    }
  };

  const retry = async (candidate: CoverCandidate, repairCodes: string[] = []) => {
    if (!current || busy || isRefinementConcept(current.concepts.find(concept => concept.concept_id === candidate.concept_id))) return;
    const candidateNumber = current.candidates.findIndex((item) => item.candidate_id === candidate.candidate_id) + 1;
    setBusy(candidate.candidate_id);
    setProgress({
      mode: "retry", completed: 0, total: 1, phase: "preparing",
      candidateNumber,
    });
    try {
      await waitForJob(
        await (repairCodes.length
          ? api.retryCover(
              id, current.cover_set_id, candidate.candidate_id, current.revision, repairCodes,
            )
          : api.retryCover(id, current.cover_set_id, candidate.candidate_id, current.revision)),
        { mode: "retry", candidateNumber },
      );
      applySets(await api.covers(id));
      toast("候选图已重新生成", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
      setProgress(null);
    }
  };

  const startRepair = async (refinementPrompt?: string) => {
    if (!repairTarget || repairTarget.owner !== id || busy) return;
    if (repairTarget.mode === "refinement" && !refinementPrompt?.trim()) return;
    setRepairError("");
    try {
      await repairTask.start(repairTarget.setId, repairTarget.candidate.candidate_id, repairTarget.revision,
        repairTarget.mode === "refinement" ? refinementPrompt?.trim() : undefined);
      if (owner.current === id) setRepairTarget(null);
    } catch (cause) {
      if (owner.current === id) setRepairError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  const approveRepair = async () => {
    if (busy) return;
    try { await repairTask.approve(); }
    catch (cause) { if (owner.current === id) toast(cause instanceof Error ? cause.message : String(cause), "error"); }
  };

  const replanRefinement = (task: CoverRepairTask, context?: CoverRefinementReplanContext, message = "") => {
    const parent = sets.find(set => set.cover_set_id === task.parent_cover_set_id);
    const original = parent?.candidates.find(candidate => candidate.candidate_id === task.parent_candidate_id);
    if (!parent || !original || !refinementReferenceUrl(task, original)
      || (context && (context.parent_cover_set_id !== parent.cover_set_id || context.parent_candidate_id !== original.candidate_id))) {
      toast("原始参考图片暂不可用，无法重新规划精修。请恢复原图后重新加载。", "error"); return;
    }
    const reason = refinementUnavailableReason(original, parent.concepts.find(concept => concept.concept_id === original.concept_id));
    if (reason) {toast(reason, "error"); return;}
    setRepairError(message);
    setRepairTarget({owner: id, candidate: original, number: task.candidate_number, setId: parent.cover_set_id,
      revision: context?.expected_revision ?? parent.revision, mode: "refinement",
      initialPrompt: context?.refinement_prompt ?? task.refinement_prompt ?? ""});
  };
  const retryRefinement = async (task: CoverRepairTask) => {
    if (busy || task.kind !== "refinement" || !["failed", "interrupted"].includes(task.status)) return;
    const navigationVersion = tabNavigation.current;
    try {
      const resumed = await repairTask.retry(task.task_id);
      if (owner.current === id && resumed) window.requestAnimationFrame(() => {
        if (owner.current !== id || tabNavigation.current !== navigationVersion) return;
        const panel = document.getElementById("cover-repair-task");
        // Instant movement also respects reduced-motion preferences. A user who
        // navigates elsewhere during the request keeps their chosen page.
        panel?.scrollIntoView?.({block: "start", behavior: "auto"});
        panel?.focus({preventScroll: true});
      });
      if (owner.current === id && resumed && ["failed", "interrupted"].includes(resumed.status)) {
        toast("已恢复后续失败任务。请在上方任务面板点击“重试精修”继续。", "error");
      }
    }
    catch (cause) {
      if (owner.current !== id) return;
      const context = refinementReplanContext(cause);
      const message = cause instanceof Error ? cause.message : String(cause);
      if (context) {replanRefinement(task, context, message); return;}
      const saved = refinementSavedResult(cause);
      if (saved && sets.some(set => set.cover_set_id === saved.cover_set_id && set.candidates.some(candidate => candidate.candidate_id === saved.candidate_id))) {
        setSelectedSetId(saved.cover_set_id);
      }
      toast(message, saved ? "success" : "error");
    }
  };

  const reevaluate = async (candidate: CoverCandidate) => {
    if (!current) return;
    setBusy(candidate.candidate_id);
    try {
      let job = await api.reevaluateCover(id, current.cover_set_id, candidate.candidate_id, current.revision);
      while (job.status === "running") {
        await wait(700);
        job = await api.getJob(job.job_id);
      }
      if (job.status === "error") throw new Error(job.error || "封面评估失败");
      applySets(await api.covers(id), current.cover_set_id);
      toast("已有图片的评估已更新，未重新出图", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const select = async (candidate: CoverCandidate) => {
    if (!current) return;
    const stale = current.status === "stale";
    const accepted = await confirm({
      title: "使用这张封面？",
      message: sessionId ? "保存为本次讨论选定的封面。导入正式项目后，还需要在项目中明确选定。" : stale
        ? "这张候选图基于旧版故事设计生成，仍将其设为交付封面吗？"
        : "将这张候选图设为交付封面，并重新生成交付包吗？",
      confirmLabel: "使用此封面",
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.selectCover(id, current.cover_set_id, candidate.candidate_id, current.revision, current.active_revision, stale);
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast(sessionId ? "已保存本次讨论的封面选择" : "已选择交付封面", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const reject = async (candidate: CoverCandidate) => {
    if (!current) return;
    const accepted = await confirm({
      title: "拒绝这张候选图？",
      message: "保留其他候选图，并将此方案标记为已拒绝吗？",
      confirmLabel: "拒绝候选图",
      danger: true,
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.rejectCover(
        id, current.cover_set_id, candidate.candidate_id, current.revision,
      );
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast("候选图已拒绝", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  if (loading) return <LoadingWorkspace />;

  return (
    <Scene quiet>
      <div className="workspace-page cover-studio">
        <Link
          to={sessionId ? "/" : `/projects/${id}`}
          className="cover-studio__back"
        >
          <Icon name="arrow-left" className="h-3.5 w-3.5" /> {sessionId ? "返回书库" : "返回作品"}
        </Link>

        <header className="cover-studio__header">
          <div className="cover-studio__title">
            <p className="cover-studio__eyebrow">NOVEL OS / COVER ATELIER</p>
            <h1>
              封面工作室
            </h1>
            <p className="cover-studio__book">{project?.title || id}</p>
            <p className="cover-studio__specs"><span>{settings?.model_capabilities?.exact_model_selection === false ? "Codex · 自动选择图片模型" : settings?.model || "模型待配置"}</span><span>{settings?.size || "尺寸待配置"}</span><span>{qualityLabel(settings?.quality || "high")}</span></p>
          </div>
          <div className="cover-studio__actions">
            {current && !sessionId && (
              <a
                href={api.deliveryPackageUrl(id)}
                download="book-package.zip"
                className="btn-secondary"
                aria-label="下载交付包"
              >
                <Icon name="download" className="h-4 w-4" /> 交付包
              </a>
            )}
            {directionsLoaded && !batchDirections[0] && (
              <button
                type="button"
                className="btn-secondary"
                disabled={Boolean(busy)}
                onClick={createDirection}
                aria-label="创建美术方向"
              >
                <Icon name="sparkles" className="h-4 w-4" />
                {operation === "direction:create" ? "提交中" : directionTask.checking ? "同步中" : directionPlanning ? "规划中" : "创建方向"}
              </button>
            )}
            <div className="flex flex-col items-end gap-1">
              <button
                type="button"
                className="btn-primary"
                disabled={!generationAllowed || Boolean(busy)}
                onClick={generate}
                aria-label={generationLabel}
                aria-describedby={generationDisabledReason ? "cover-generation-requirement" : undefined}
                title={generationDisabledReason || undefined}
              >
                <Icon name="sparkles" className="h-4 w-4" />
                {busy === "generate" ? "生成中" : generationLabel}
              </button>
              {generationDisabledReason && (
                <span id="cover-generation-requirement" className="max-w-56 text-right text-[10.5px] text-ink-muted">
                  {generationDisabledReason}
                </span>
              )}
              {retryingBatch && !generationDisabledReason && (
                <span className="max-w-56 text-right text-[10.5px] text-ink-muted">
                  沿用已批准方向，新建一批候选；已有结果保留。
                </span>
              )}
            </div>
          </div>
        </header>

        <CoverGenerationTaskStatus task={generationTask.task} checking={generationTask.checking}
          connectionError={generationTask.connectionError} lastSyncedAt={generationTask.lastSyncedAt}
          submitting={generationTask.submitting} />

        <CoverRepairTaskPanel task={repairTask.task} checking={repairTask.checking} error={repairTask.error}
          syncedAt={repairTask.syncedAt} submitting={repairTask.submitting} disabled={Boolean(busy)} onApprove={approveRepair}
          onRetry={retryRefinement} onReplan={task => replanRefinement(task)}
          compact={selectedTab !== "gallery"} onShowDetails={() => selectTab("gallery")}
          original={sets.find(set => set.cover_set_id === repairTask.task?.parent_cover_set_id)?.candidates.find(candidate => candidate.candidate_id === repairTask.task?.parent_candidate_id)} />

        {!directionCompletionApproved && <CoverDirectionTaskStatus task={directionTask.task} checking={directionTask.checking}
          connectionError={directionTask.connectionError} lastSyncedAt={directionTask.lastSyncedAt}
          submitting={operation === "direction:create"} />}

        {selectedTab !== "directions" && batchDirections[0]?.status === "awaiting_approval" && !directionPlanning && (
          <div className="cover-workspace-notice"><span>美术方向待审核，批准后即可生成候选封面。</span>
            <button type="button" className="btn-secondary" onClick={() => selectTab("directions")}>审核美术方向</button></div>
        )}
        {selectedTab !== "analysis" && analysisNotice && (analysisNotice.active || analysisNotice.needsAttention) && (
          <div className="cover-workspace-notice" role="status"><span>{analysisNotice.message || "正在读取故事分析进度…"}</span>
            <button type="button" className="btn-secondary" onClick={() => selectTab("analysis")}>查看故事分析</button></div>
        )}

        {error && (
          <div
            role="alert"
            className="mt-6 rounded-[8px] border border-[#df93a7] bg-[#fff1f4] px-4 py-3 text-[13px] text-[#96354e]"
          >
            {error}
          </div>
        )}

        {!settings?.configured && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-[8px] border border-[#d6a85f] bg-[#fff7e8] px-4 py-3">
            <p className="text-[13px] text-[#72511d]">{settings?.error || "封面模型配置尚未完成。"}</p>
            <Link to="/settings" className="btn-secondary" aria-label="配置封面模型">
              <Icon name="sparkles" className="h-4 w-4" /> 配置封面模型
            </Link>
          </div>
        )}

        {progress && <GenerationProgress progress={progress} />}

        <div ref={tabAnchor} aria-hidden="true" />
        <CoverWorkspaceTabs selected={selectedTab} onSelect={selectTab} indicators={{
          gallery: generationTask.task?.status === "running" || generationTask.task?.status === "queued" ? "生成中" : current ? `${readyCount(current)}/${current.requested_count}` : undefined,
          directions: directionPlanning ? "规划中" : batchDirections[0] ? directionStatusLabel(batchDirections[0].status) : undefined,
          analysis: analysisNotice?.active ? "分析中" : analysisNotice?.needsAttention ? "需关注" : analysisId ? "已完成" : undefined,
        }} />

        <div id="cover-panel-gallery" role="tabpanel" aria-labelledby="cover-tab-gallery" className="cover-workspace-panel" hidden={selectedTab !== "gallery"} tabIndex={0}>
        <section className="cover-gallery" aria-label="封面候选画廊">
        <div className="cover-gallery__header">
          <div className="cover-section-heading">
            <span className="cover-section-number" aria-hidden="true">02</span>
            <div><p className="cover-gallery__eyebrow">候选画廊</p>
            <h2>
              {current ? `已生成 ${readyCount(current)}/${current.requested_count} 张` : "尚未生成封面"}
            </h2>
            <p className="mt-1 text-[12px] text-ink-muted">
              {current
                ? statusLabel(current.status)
                : progress
                  ? "封面生成任务已启动"
                  : "尚无已保存的封面候选图"}
            </p>
            </div>
          </div>
          {sets.length > 1 && (
            <label className="flex items-center gap-2 text-[12px] text-ink-muted">
              版本
              <select
                value={current?.cover_set_id || ""}
                onChange={(event) => setSelectedSetId(event.target.value)}
                className="h-9 rounded-[8px] border border-paper-line bg-white px-3 text-[12px] text-ink-text"
              >
                {sets.map((item, index) => (
                  <option key={item.cover_set_id} value={item.cover_set_id}>
                    {index === 0 ? "最新" : `版本 ${sets.length - index}`} · {statusLabel(item.status)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        {current ? (
          <div className="cover-gallery__grid" data-count={current.candidates.length}>
            {current.candidates.map((candidate, index) => (
              <CandidateCard
                key={`${current.cover_set_id}:${candidate.candidate_id}`}
                owner={id} setId={current.cover_set_id} sets={sets} repairTask={repairTask.task}
                candidate={candidate}
                concept={current.concepts.find((item) => item.concept_id === candidate.concept_id)}
                index={index}
                busy={busy === candidate.candidate_id}
                disabled={Boolean(busy)}
                generating={Boolean(
                  (generationTask.task?.status === "running" && generationTask.task.cover_set_id === current.cover_set_id
                    && generationTask.task.progress?.phase === "generating"
                    && generationTask.task.progress.current_candidate_id === candidate.candidate_id)
                  || (progress?.mode === "retry" && progress.candidateNumber === index + 1)
                )}
                onRetry={(repairCodes) => retry(candidate, repairCodes)}
                onSelect={() => select(candidate)}
                onReject={() => reject(candidate)}
                onReevaluate={() => reevaluate(candidate)}
                onRepair={() => {setRepairError(""); setRepairTarget({owner: id, candidate, number: index + 1, setId: current.cover_set_id, revision: current.revision, mode: "repair"});}}
                onRefine={() => {setRepairError(""); setRepairTarget({owner: id, candidate, number: index + 1, setId: current.cover_set_id, revision: current.revision, mode: "refinement"});}}
                onRetryRefinement={retryRefinement} onReplanRefinement={task => replanRefinement(task)}
              />
            ))}
          </div>
        ) : !progress ? (
          <EmptyCoverWorkspace
            direction={batchDirections[0] || null}
            directionsLoaded={directionsLoaded}
            analysisReady={Boolean(analysisId)}
            directionPlanning={directionPlanning}
          />
        ) : null}
        </section>
        </div>

        <div id="cover-panel-costs" role="tabpanel" aria-labelledby="cover-tab-costs" className="cover-workspace-panel" hidden={selectedTab !== "costs"} tabIndex={0}>
        <GenerationCostPanel owner={id} refreshKey={`${current?.revision ?? 0}:${directionTask.task?.updated_at ?? ""}:${generationTask.task?.updated_at ?? ""}:${repairTask.task?.updated_at ?? ""}`} />
        </div>

        <div id="cover-panel-directions" role="tabpanel" aria-labelledby="cover-tab-directions" className="cover-workspace-panel" hidden={selectedTab !== "directions"} tabIndex={0}>
        {batchDirections.length > 0 && (
          <DirectionWorkspace
            key={batchDirections[0].direction_id}
            directions={batchDirections}
            busy={busy}
            approvalLocked={repairDirectionIsLatest}
            onApprove={approveDirection}
            onCreate={createDirection}
          />
        )}
        {batchDirections.length === 0 && <div className="cover-workspace-empty glass-panel">
          <h2>{directionPlanning ? "美术方向正在规划" : "还没有美术方向"}</h2>
          <p>{directionPlanning ? "进度保留在页头，规划完成后可在此审核方案。" : analysisId ? "故事分析已就绪，可创建本书的封面方向。" : "先完成故事分析，再规划本书的画面与排版。"}</p>
          {!directionPlanning && (analysisId ? <button type="button" className="btn-primary" disabled={Boolean(busy)} onClick={createDirection}>创建方向</button>
            : <button type="button" className="btn-secondary" onClick={() => selectTab("analysis")}>前往故事分析</button>)}
        </div>}
        </div>

        <div id="cover-panel-analysis" role="tabpanel" aria-labelledby="cover-tab-analysis" className="cover-workspace-panel" hidden={selectedTab !== "analysis"} tabIndex={0}>
          <CoverSkeletonPanel key={id} owner={id} onAnalysis={chooseAnalysis} onStatus={setAnalysisNotice} />
          {analysisId && <div className="cover-workspace-notice"><span>故事分析已就绪，可前往美术方向查看或创建方案。</span>
            <button type="button" className="btn-secondary" onClick={() => selectTab("directions")}>前往美术方向</button></div>}
        </div>
      </div>

      {repairTarget?.owner === id && <CoverRepairStartDialog key={`${id}:${repairTarget.setId}:${repairTarget.candidate.candidate_id}:${repairTarget.mode}`}
        candidate={repairTarget.candidate} number={repairTarget.number} mode={repairTarget.mode} initialPrompt={repairTarget.initialPrompt}
        busy={Boolean(busy)} error={repairError} onClose={() => setRepairTarget(null)} onStart={startRepair} />}

      {missingFacts?.projectId === id && (
        <CoverFactsModal
          key={`${id}:${missingFacts.facts.revision_sha256}`}
          facts={missingFacts.facts}
          busy={Boolean(busy)}
          onClose={() => { if (!busy) setMissingFacts(null); }}
          onConfirm={confirmStoryFacts}
        />
      )}
    </Scene>
  );
}

function CoverFactsModal({ facts, busy, onClose, onConfirm }: {
  facts: CoverStoryFacts;
  busy: boolean;
  onClose: () => void;
  onConfirm: (body: CoverStoryFactsConfirmation) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const requiredFields = facts.blocking_pending_fields || facts.pending_fields;
  const ready = requiredFields.every((item) => values[item.field]?.trim());
  const confirm = async () => {
    setError("");
    const characters = new Map<string, CoverStoryFactsConfirmation["characters"][number]>();
    let primarySpaces: string[] | undefined;
    for (const item of requiredFields) {
      const value = values[item.field]?.trim() || "";
      if (item.field === "lived_environment.primary_spaces") {
        primarySpaces = [...new Set(value.split("\n").map((line) => line.trim()).filter(Boolean))];
      } else if (item.character_id) {
        const character = characters.get(item.character_id) || { character_id: item.character_id };
        if (item.field.endsWith(".age")) character.age_band = value;
        if (item.field.endsWith(".occupation_and_status")) character.occupation_and_status = value;
        characters.set(item.character_id, character);
      }
    }
    try {
      await onConfirm({
        expected_revision_sha256: facts.revision_sha256,
        characters: [...characters.values()],
        ...(primarySpaces ? { primary_spaces: primarySpaces } : {}),
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  return (
    <Modal open onClose={onClose} title="补全封面人物与场景">
      <p className="mb-4 text-[13px] leading-6 text-ink-muted">
        请补充故事尚未设定的视觉信息，保存后继续规划美术方向。
      </p>
      {facts.brief.principal_characters?.filter((character) => character.occupation_and_status).map((character) => (
        <div key={character.character_id} className="mb-4 rounded-xl bg-white/60 px-4 py-3 text-[13px]">
          <p className="font-semibold text-ink-text">{character.name}</p>
          <p className="mt-1 text-ink-muted">{character.occupation_and_status}</p>
        </div>
      ))}
      <form onSubmit={(event) => { event.preventDefault(); if (ready && !busy) void confirm(); }}>
        {requiredFields.map((item) => {
          const age = item.field.endsWith(".age");
          const spaces = item.field === "lived_environment.primary_spaces";
          const label = spaces ? "主要生活场景（每行一个）"
            : `${item.character_name || "主角"} · ${age ? "封面年龄段" : "职业与身份"}`;
          return (
            <Field key={item.field} label={label}>
              {spaces ? (
                <textarea className={textareaClass} required disabled={busy} value={values[item.field] || ""}
                  placeholder="例如：小城公寓、社区设计工作室"
                  onChange={(event) => setValues({ ...values, [item.field]: event.target.value })} />
              ) : (
                <input className={fieldClass} required disabled={busy} value={values[item.field] || ""}
                  placeholder={age ? "例如：30–35 岁" : "例如：重返职场的设计师"}
                  onChange={(event) => setValues({ ...values, [item.field]: event.target.value })} />
              )}
            </Field>
          );
        })}
        {error && <p role="alert" className="mb-4 text-[13px] text-[#96354e]">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>取消</button>
          <button type="submit" className="btn-primary" disabled={busy || !ready}>
            {busy ? "保存中…" : "保存并创建方向"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EmptyCoverWorkspace({
  direction, directionsLoaded, analysisReady, directionPlanning,
}: {
  direction: CoverDirection | null;
  directionsLoaded: boolean;
  analysisReady: boolean;
  directionPlanning: boolean;
}) {
  const approved = direction?.status === "approved";
  const nextStep = directionPlanning
    ? "正在处理美术方向请求；当前状态见上方，方案完成后可审核。"
    : !directionsLoaded
    ? "正在读取现有美术方向。"
    : !direction
      ? analysisReady ? "下一步：点击“创建方向”。" : "下一步：点击上方“分析完整骨架”。"
      : direction.status === "stale"
        ? "现有方向已过期，请先创建并批准新方向。"
        : approved
          ? "美术方向已批准，现在可以生成候选封面。"
          : "下一步：审核下方方案并批准美术方向。";
  return (
    <section
      aria-label="封面生成流程"
      className="cover-empty"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#eef1f8] text-ink-muted">
          <Icon name="image" className="h-4 w-4" />
        </span>
        <div>
          <p className="text-[13px] font-semibold text-ink-text">正文流程不会自动生成封面</p>
          <p className="mt-1 text-[12px] leading-5 text-ink-muted">
            从小说骨架分析人物与冲突，再创建美术方向，审核通过后生成候选图。
          </p>
        </div>
      </div>
      <ol className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-paper-line pt-3 text-[11.5px] font-medium text-ink-text">
        <li><span className="nums mr-1.5 text-ink-muted">1</span>分析完整骨架</li>
        <li><span className="nums mr-1.5 text-ink-muted">2</span>创建美术方向</li>
        <li><span className="nums mr-1.5 text-ink-muted">3</span>审核并批准</li>
        <li><span className="nums mr-1.5 text-ink-muted">4</span>生成候选封面</li>
      </ol>
      <p className="mt-2 text-[11px] text-ink-muted">{nextStep}</p>
    </section>
  );
}

function DirectionWorkspace({
  directions, busy, approvalLocked, onApprove, onCreate,
}: {
  directions: CoverDirection[];
  busy: string;
  approvalLocked: boolean;
  onApprove: (direction: CoverDirection, extraImageAttempts?: number) => void;
  onCreate: () => void;
}) {
  const direction = directions[0];
  const [extraImageAttempts, setExtraImageAttempts] = useState(0);
  const characters = direction.brief?.principal_characters || [];
  const environment = direction.brief?.lived_environment || {};
  const identity = direction.visual_identity;
  const evidenceLedger = direction.evidence_ledger;
  const conflictContract = direction.core_conflict_visual_contract;
  const assumptions = [
    ...(Array.isArray(direction.brief?.visual_assumptions)
      ? direction.brief.visual_assumptions
      : []),
    ...(direction.visual_assumptions || []),
  ];
  return (
    <section
      aria-label="故事事实与美术方向"
      className="cover-directions"
    >
      <div className="cover-directions__header">
        <div>
          <p className="cover-studio__eyebrow">03 / 设计档案</p>
          <h2 className="mt-1 font-display text-[18px] font-semibold text-ink-text">
            美术方向审核
          </h2>
          <p className="mt-1 text-[12px] text-ink-muted">
            {direction.brief?.genre || "基于故事的专属方向"} · {direction.brief?.target_audience || "目标受众待确认"}
          </p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
          direction.status === "approved"
            ? "border-[#8dc5a6] bg-[#eaf8f0] text-[#26714a]"
            : direction.status === "stale"
              ? "border-[#df93a7] bg-[#fff1f4] text-[#96354e]"
              : "border-[#d6a85f] bg-[#fff7e8] text-[#72511d]"
        }`}>
          {directionStatusLabel(direction.status)}
        </span>
      </div>

      <CoverCommercialReview review={direction.source_binding?.campaign_evidence_review}
        commercial={direction.profile_version === "cover-profiles.v11"}
        concepts={direction.plans.map((plan, index) => ({concept_id: plan.concept_id, label: `方向 ${index + 1}`}))} />
      <details className="cover-direction-record" open={direction.status !== "approved"}>
        <summary>{direction.status === "approved" ? "查看已批准方案与故事依据" : "审阅画面方案与故事依据"}<span>{direction.plans.length} 个美术方向</span></summary>
      {direction.source_binding && <p className="mt-3 rounded border border-paper-line bg-white p-3 text-xs text-ink-muted">
        本次审核同时确认所展示的骨架快照与封面事实，首批生成 {direction.plans.length} 张图片，每张执行两次独立看图评估。
        {direction.source_binding.freshness_policy === "pinned_snapshot" ? " 本方向固定采用所选历史快照。" : " 生成前会再次核对当前骨架。"}
      </p>}
      {direction.source_binding && direction.status === "awaiting_approval" && <label className="mt-3 flex items-center gap-2 text-xs text-ink-muted">
        <input type="checkbox" checked={extraImageAttempts === 1} onChange={event => setExtraImageAttempts(event.target.checked ? 1 : 0)} />
        允许每张候选图额外重绘 1 次，最多增加 {direction.plans.length} 次图片调用；故事方案变更仍需重新审核。默认不允许额外出图。
      </label>}
      {characters.length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {characters.map((character) => (
            <div key={String(character.character_id)} className="rounded-[6px] border border-paper-line bg-white/65 p-3">
              <p className="text-[12px] font-semibold text-ink-text">{String(character.name || character.character_id)}</p>
              <p className="mt-1 text-[11px] leading-4 text-ink-muted">
                {character.age ? `年龄 ${character.age}` : character.age_band ? `年龄阶段：${character.age_band}` : "年龄待补充"}
                {character.occupation_and_status ? ` · ${character.occupation_and_status}` : ""}
              </p>
              <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-ink-muted">
                {character.lived_environment || character.daily_wardrobe || "生活环境待补充"}
              </p>
            </div>
          ))}
        </div>
      )}

      {environment && Object.keys(environment).length > 0 && (
        <p className="mt-3 text-[11.5px] text-ink-muted">
          主要生活空间：{Array.isArray(environment.primary_spaces) ? environment.primary_spaces.join("、") : String(environment.primary_spaces || "待补充")}
        </p>
      )}

      {identity && (
        <div className="mt-4 rounded-[8px] border border-[#c8d2ef] bg-[#f4f7ff] p-4" aria-label="本书视觉语言">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-3xl">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#526aa5]">本书视觉语言</p>
              <p className="mt-1.5 text-[13px] font-semibold leading-5 text-ink-text">{identity.design_thesis}</p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                情绪矛盾：{identity.dominant_emotional_contradiction}
              </p>
            </div>
            {evidenceLedger && (
              <span className="rounded-full border border-[#b8c6e8] bg-white px-2.5 py-1 text-[10.5px] font-semibold text-[#526aa5]">
                {evidenceLedger.items.length} 条故事证据 · {new Set(evidenceLedger.items.map((item) => item.source_type)).size} 类来源
              </span>
            )}
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <p className="text-[10.5px] font-semibold text-ink-text">小说专属视觉锚点</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {identity.uniqueness_anchors.map((anchor) => (
                  <span key={anchor} className="rounded-full bg-white px-2 py-1 text-[10.5px] text-ink-muted">{anchor}</span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[10.5px] font-semibold text-ink-text">可用视觉语法</p>
              <p className="mt-1.5 text-[11px] leading-4 text-ink-muted">{identity.visual_grammar.join(" · ")}</p>
              <p className="mt-1 text-[11px] leading-4 text-ink-muted">字体：{identity.typography_voice}</p>
            </div>
          </div>
          <details className="mt-3 text-[11px] text-ink-muted">
            <summary className="cursor-pointer font-semibold text-[#526aa5]">查看视觉证据与设计边界</summary>
            <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
              {(evidenceLedger?.items || []).slice(0, 8).map((item) => (
                <div key={item.evidence_id} className="rounded-[5px] border border-[#d8e0f4] bg-white/80 p-2.5">
                  <p className="font-semibold text-ink-text">{formatStrategy(item.source_type)} · {item.spoiler_level}</p>
                  <p className="mt-1 line-clamp-3 leading-4">{item.summary}</p>
                </div>
              ))}
            </div>
            <p className="mt-2">人物策略：{identity.cast_policy}</p>
            <p className="mt-1">避免套路：{identity.cliche_blacklist.join("、") || "无额外限制"}</p>
          </details>
        </div>
      )}

      {conflictContract && (
        <div className="mt-4 rounded-[8px] border border-[#dfb3bd] bg-[#fff6f7] p-4" aria-label="核心冲突视觉契约">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-3xl">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#96354e]">核心冲突视觉契约</p>
              <p className="mt-1.5 text-[13px] font-semibold leading-5 text-ink-text">{conflictContract.pressure_source}</p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                可见原因：{conflictContract.visible_cause}
              </p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                主角后果：{conflictContract.decisive_consequence}
              </p>
            </div>
            <span className="rounded-full border border-[#dfb3bd] bg-white px-2.5 py-1 text-[10.5px] font-semibold text-[#96354e]">
              {formatStrategy(conflictContract.conflict_kind)}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {conflictContract.required_visual_signals.map((signal) => (
              <span key={signal} className="rounded-full bg-white px-2 py-1 text-[10.5px] text-ink-muted">{signal}</span>
            ))}
          </div>
          <p className="mt-2 text-[10.5px] leading-4 text-ink-muted">
            关系压力人物：{conflictContract.pressure_character_ids.join("、") || "非人物压力"} · 边界：{conflictContract.spoiler_boundary}
          </p>
        </div>
      )}

      {assumptions.length > 0 && (
        <div className="mt-3 border-l-2 border-[#d6a85f] bg-[#fff7e8] px-3 py-2.5 text-[11.5px] text-[#72511d]">
          <p className="font-semibold">
            {(() => {
              const blocking = assumptions.filter((item) => item.status === "pending_confirmation" && item.provenance !== "model_inferred");
              const inferred = assumptions.filter((item) => item.provenance === "model_inferred" || item.status === "model_inferred");
              return blocking.length > 0
                ? `本次审核包含 ${blocking.length} 项待确认的来源事实`
                : `本次审核包含 ${inferred.length} 项由模型推演的视觉细节`;
            })()}
          </p>
          <ul className="mt-1.5 space-y-1">
            {assumptions.map((item, index) => (
              <li key={`${String(item.field)}:${index}`}>
                <span className="font-medium">{String(item.field)}</span>: {String(item.proposed_value)}
                {item.reason ? <span className="text-[#86642f]"> · {String(item.reason)}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="cover-direction-plans">
        {direction.plans.map((plan, index) => (
          <article key={plan.concept_id} className="cover-direction-plan">
            <div className="flex items-start justify-between gap-3">
              <p className="text-[12px] font-semibold text-ink-text">
                方向 {index + 1}：{formatStrategy(plan.portfolio_slot || plan.visual_strategy)}
              </p>
              <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-ink-muted">{plan.visual_hook.hook_type.replaceAll("_", " ")}</span>
            </div>
            {plan.portfolio_slot && (
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-[5px] bg-[#f5f6fb] px-2.5 py-2 text-[10.5px] leading-4 text-ink-muted sm:grid-cols-3">
                <div><dt className="font-semibold text-ink-text">构图</dt><dd>{formatStrategy(plan.composition_family)}</dd></div>
                {plan.focal_strategy && <div><dt className="font-semibold text-ink-text">主体策略</dt><dd>{formatStrategy(plan.focal_strategy)}</dd></div>}
                <div><dt className="font-semibold text-ink-text">场景</dt><dd>{formatStrategy(plan.scene_family)}</dd></div>
                <div><dt className="font-semibold text-ink-text">地点</dt><dd>{plan.location_family}</dd></div>
                <div><dt className="font-semibold text-ink-text">美术风格</dt><dd>{formatStrategy(plan.art_style)}</dd></div>
                <div><dt className="font-semibold text-ink-text">情绪</dt><dd>{formatStrategy(plan.emotion_register)}</dd></div>
                <div><dt className="font-semibold text-ink-text">字体排版</dt><dd>{formatStrategy(plan.typography_style)}</dd></div>
              </dl>
            )}
            <p className="mt-2 text-[11.5px] leading-4 text-ink-muted">{plan.frozen_action}</p>
            {plan.conflict_read && (
              <div className="mt-2 rounded-[5px] border border-[#ead0d6] bg-[#fff8f9] px-2.5 py-2 text-[10.5px] leading-4 text-ink-muted">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`rounded-full px-2 py-0.5 font-semibold ${
                    plan.causal_visibility === "direct"
                      ? "bg-[#f7dce3] text-[#96354e]"
                      : "bg-[#eeeaf8] text-[#66518f]"
                  }`}>
                    {plan.causal_visibility === "direct" ? "直接冲突" : "间接证据"}
                  </span>
                  <span>{plan.conflict_delivery}</span>
                </div>
                <p className="mt-1 font-medium text-ink-text">缩略图故事：{plan.conflict_read}</p>
                <p className="mt-1">原因：{plan.cause_signal} · 后果：{plan.consequence_signal}</p>
              </div>
            )}
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">出场人物：{plan.cast.length ? plan.cast.join("、") : "无人物方案"} · 核心道具/意象：{plan.primary_prop}</p>
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">{plan.visual_hook.open_question}</p>
            {plan.campaign && <div className="mt-3 space-y-1 rounded border border-paper-line bg-white p-3 text-xs">
              <p className="font-semibold">{plan.campaign.composition_mode === "campaign_composite" ? "电影宣传组合海报" : "同场景冲突海报"} · {plan.campaign.cover_epoch}</p>
              <p>画面局势：{plan.campaign.hook.visible_fact}</p>
              <p>异常与钩子：{plan.campaign.hook.unexpected_contradiction}</p>
              <p>刻意留白：{plan.campaign.hook.withheld_answer}</p>
              {plan.campaign.interaction_edges.map((edge, index) => <p key={index}>{edge.actor_id} → {edge.action} → {edge.target_id}；{edge.visible_reaction}</p>)}
              {!!plan.campaign.layers?.length && <details><summary>海报图层与故事时点</summary>{plan.campaign.layers.map(layer => <p key={layer.layer_id}>{layer.function} · {layer.epoch} · {layer.location}</p>)}</details>}
            </div>}
            {plan.design_rationale && (
              <div className="mt-2 border-l-2 border-[#9dafdc] pl-2.5 text-[10.5px] leading-4 text-ink-muted">
                <p><span className="font-semibold text-ink-text">设计理由：</span>{plan.design_rationale}</p>
                {plan.evidence_summary && <p className="mt-1"><span className="font-semibold text-ink-text">故事依据：</span>{plan.evidence_summary}</p>}
                {plan.typography_rationale && <p className="mt-1"><span className="font-semibold text-ink-text">字体表达：</span>{plan.typography_rationale}</p>}
                {plan.novelty_rationale && <p className="mt-1"><span className="font-semibold text-ink-text">差异说明：</span>{plan.novelty_rationale}</p>}
              </div>
            )}
          </article>
        ))}
      </div>

      </details>

      {direction.status !== "approved" && direction.status !== "stale" && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={Boolean(busy) || approvalLocked}
            onClick={() => {if (!approvalLocked) onApprove(direction, extraImageAttempts);}}
            aria-label="批准美术方向"
          >
            <Icon name="circle-check" className="h-4 w-4" /> 批准美术方向
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="重新规划封面方向"
          >
            <Icon name="sparkles" className="h-4 w-4" /> 重新规划方向
          </button>
        </div>
      )}
      {direction.status === "approved" && (
        <button
          type="button"
          className="btn-secondary mt-4"
          disabled={Boolean(busy)}
          onClick={onCreate}
          aria-label="重新规划封面方向"
        >
          <Icon name="sparkles" className="h-4 w-4" /> 重新规划方向
        </button>
      )}
      {direction.status === "stale" && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] font-medium text-[#96354e]">此方向已过期，请根据当前故事事实创建新方向。</p>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="创建新的美术方向"
          >
            <Icon name="sparkles" className="h-4 w-4" /> 新方向
          </button>
        </div>
      )}
    </section>
  );
}

function GenerationProgress({ progress }: { progress: CoverProgress }) {
  return <section role="status" aria-live="polite" aria-label="封面生成进度" className="cover-direction-task">
    <div className="cover-direction-task__main">
      <span className="cover-direction-task__mark" aria-hidden="true"><span className="cover-direction-task__spinner" /></span>
      <div className="cover-direction-task__message">
        <h2>正在重新生成候选图 {progress.candidateNumber}</h2>
        <p>正在处理一张候选图，完成后更新预览。</p>
      </div>
    </div>
  </section>;
}

function CandidateCard({
  candidate, concept, index, busy, disabled, generating, onRetry, onSelect, onReject, onReevaluate, onRepair, onRefine,
  owner, setId, sets, repairTask, onRetryRefinement, onReplanRefinement,
}: {
  owner: string; setId: string; sets: CoverSet[]; repairTask: CoverRepairTask | null;
  candidate: CoverCandidate;
  concept?: CoverSet["concepts"][number];
  index: number;
  busy: boolean;
  disabled: boolean;
  generating: boolean;
  onRetry: (repairCodes?: string[]) => void;
  onSelect: () => void;
  onReject: () => void;
  onReevaluate: () => void;
  onRepair: () => void;
  onRefine: () => void;
  onRetryRefinement: (task: CoverRepairTask) => void;
  onReplanRefinement: (task: CoverRepairTask) => void;
}) {
  const reduceMotion = useReducedMotion();
  const imageUrl = api.assetUrl(candidate.url);
  const label = `候选图 ${index + 1}`;
  const repairCodes = candidate.quality_report?.repair_codes || [];
  const [repairCode, setRepairCode] = useState(repairCodes[0] || "");
  const report = candidate.quality_report;
  const reviewBadge = candidate.status === "ready" && report?.status === "blocked" ? "需修复"
    : candidate.status === "ready" && report?.status === "human_review_required" ? "待人工审核" : "";
  const scene = concept?.scene_plan || {};
  const bound = Boolean(scene._compiled_recipe || scene.campaign || report?.recipe_sha256 || report?.evaluation);
  const decision = report?.repair_decision as { kind?: string } | undefined;
  const sourceBinding = scene._source_binding as {direction_id?: string} | undefined;
  const refinement = isRefinementConcept(concept) || (repairTask?.kind === "refinement"
    && repairTask.cover_set_id === setId && repairTask.result_candidate_id === candidate.candidate_id);
  const failedRefinement = refinement && candidate.status === "failed";
  const canRepairOne = !failedRefinement && Boolean(repairKind(candidate) && sourceBinding?.direction_id);
  const refinementReason = refinementUnavailableReason(candidate, concept);
  const showRefinement = ["ready", "selected", "rejected"].includes(candidate.status);
  const authorization = scene._generation_authorization as { extra_image_attempts?: number; image_attempts_reserved?: number } | undefined;
  const remainingAttempts = Math.max(0, 1 + (authorization?.extra_image_attempts || 0)
    - Math.max(authorization?.image_attempts_reserved || 0, candidate.attempt_history?.filter((item) => item.operation !== "typography_recomposition").length || 0));
  const canRetry = !refinement && (!bound || (remainingAttempts > 0 && (!decision?.kind || decision.kind === "render")));
  return (
    <motion.article
      data-testid="cover-slot"
      aria-busy={generating || busy}
      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.28 }}
      className={`cover-candidate${candidate.status === "selected" ? " is-selected" : ""}`}
      aria-label={label}
    >
      <div className="cover-candidate__artwork">
        {imageUrl ? (
          <img src={imageUrl} alt={`封面${label}`} className="h-full w-full object-contain" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-5 text-center text-ink-muted">
            <span className={`flex h-10 w-10 items-center justify-center rounded-full ${
              candidate.status === "failed" ? "bg-[#f8dce4] text-[#a6425d]" : "bg-white text-ink-muted"
            }`}>
              <Icon name={candidate.status === "failed" ? "circle-alert" : "image"} className="h-5 w-5" />
            </span>
            <span className="text-[12px] font-medium">
              {generating
                ? "正在生成图片…"
                : busy
                  ? "重新生成中"
                  : candidate.status === "failed"
                    ? failedRefinement ? "精修失败 · 原图保留" : "生成失败"
                    : `候选图 ${index + 1}`}
            </span>
          </div>
        )}
        <span className={`absolute left-2 top-2 rounded-full border px-2 py-1 text-[10px] font-semibold capitalize backdrop-blur-md ${reviewBadge === "需修复" ? "border-[#df93a7] bg-[#fff1f4]/95 text-[#96354e]" : statusClass(candidate.status)}`}>
          {generating ? "生成中" : reviewBadge || candidateStatusLabel(candidate.status)}
        </span>
        {imageUrl && (
          <a
            href={imageUrl}
            target="_blank"
            rel="noreferrer"
            title={`查看${label}原始尺寸`}
            aria-label={`查看${label}原始尺寸`}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full border border-white/50 bg-[#17213f]/75 text-white backdrop-blur-md transition-colors hover:bg-[#17213f]"
          >
            <Icon name="eye" className="h-4 w-4" />
          </a>
        )}
      </div>
      <div className="cover-candidate__body">
        <p className="cover-candidate__number">候选 / {String(index + 1).padStart(2, "0")}</p>
        <h3 className="cover-candidate__title" title={formatStrategy(concept?.visual_strategy)}>
          {formatStrategy(concept?.visual_strategy) || `方向 ${index + 1}`}
        </h3>
        <p className="mt-1 line-clamp-2 min-h-[34px] text-[11.5px] leading-[17px] text-ink-muted">
          {candidate.error || concept?.focal_scene || "等待生成"}
        </p>
        {candidate.quality_report && (
          <p className={`mt-2 text-[10.5px] font-semibold ${
            candidate.quality_report.status === "blocked"
              ? "text-[#a6425d]"
              : candidate.quality_report.status === "recommended_for_human_review"
                ? "text-[#26714a]"
                : "text-[#72511d]"
          }`}>
            {qualityStatusLabel(candidate.quality_report.status)}
          </p>
        )}
        <CoverQualityDetails candidate={candidate} concept={concept} disabled={disabled} onReevaluate={onReevaluate} />
        <div className="mt-3 flex min-h-9 flex-wrap items-center gap-1.5">
          {showRefinement && <button type="button" className="btn-secondary px-3 py-2 text-[11.5px]" onClick={onRefine}
            disabled={disabled || Boolean(refinementReason)} aria-label={`精修${label}`} aria-describedby={refinementReason ? `refinement-reason-${candidate.candidate_id}` : undefined}>
            <Icon name="sparkles" className="h-3.5 w-3.5" /> 精修这张
          </button>}
          {canRepairOne && <button type="button" className="btn-secondary px-3 py-2 text-[11.5px]" onClick={onRepair} disabled={disabled} aria-label={`修复${label}`}>
            <Icon name="history" className="h-3.5 w-3.5" /> 修复这一张
          </button>}
          {candidate.status === "failed" && canRetry && !canRepairOne && (
            <button type="button" className="btn-secondary px-3 py-2 text-[11.5px]" onClick={() => onRetry()} disabled={disabled} aria-label={`重试${label}`}>
              <Icon name="history" className="h-3.5 w-3.5" /> 重试
            </button>
          )}
          {(candidate.status === "ready" || candidate.status === "rejected") && (
            <>
              <button type="button" className="btn-primary px-3 py-2 text-[11.5px]" onClick={onSelect} disabled={disabled} aria-label={`选择${label}`}>
                <Icon name="circle-check" className="h-3.5 w-3.5" /> 选择
              </button>
              {candidate.status === "ready" && <button type="button" className="btn-ghost px-2.5 py-2 text-[11.5px]" onClick={onReject} disabled={disabled} aria-label={`拒绝${label}`}>
                拒绝
              </button>}
            </>
          )}
          {candidate.status === "selected" && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-[#26714a]">
              <Icon name="circle-check" className="h-3.5 w-3.5" /> 交付封面
            </span>
          )}
        </div>
        {showRefinement && refinementReason && <p id={`refinement-reason-${candidate.candidate_id}`} className="cover-repair__note">{refinementReason}</p>}
        {failedRefinement && <CoverRefinementRetryActions owner={owner} setId={setId} candidateId={candidate.candidate_id}
          number={index + 1} knownTask={repairTask} sets={sets} disabled={disabled} onRetry={onRetryRefinement} onReplan={onReplanRefinement} />}
        {candidate.status === "ready" && repairCodes.length > 0 && canRetry && !canRepairOne && (
          <div className="mt-2 flex items-center gap-1.5 border-t border-paper-line pt-2">
            <select
              value={repairCode}
              onChange={(event) => setRepairCode(event.target.value)}
              aria-label={`${label}的修复原因`}
              className="min-w-0 flex-1 rounded-[6px] border border-paper-line bg-white px-2 py-1.5 text-[10.5px] text-ink-text"
            >
              {repairCodes.map((code) => (
                <option key={code} value={code}>{formatStrategy(code)}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary px-2.5 py-1.5 text-[10.5px]"
              onClick={() => onRetry([repairCode])}
              disabled={disabled || !repairCode}
              aria-label={`按修复建议重新生成${label}`}
            >
              <Icon name="history" className="h-3.5 w-3.5" /> 修复
            </button>
          </div>
        )}
      </div>
    </motion.article>
  );
}

function LoadingWorkspace() {
  return (
    <Scene quiet>
      <div className="workspace-page cover-studio">
        <p className="text-[13px] text-ink-muted">正在加载封面工作区</p>
        <div className="mt-7 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {emptyCandidates(4).map((candidate) => (
            <div key={candidate.candidate_id} data-testid="cover-slot" className="aspect-[2/3] animate-pulse rounded-[8px] border border-paper-line bg-white/55" />
          ))}
        </div>
      </div>
    </Scene>
  );
}

function emptyCandidates(count: number): CoverCandidate[] {
  return Array.from({ length: count }, (_, index) => ({
    candidate_id: `empty-${index + 1}`, concept_id: `empty-concept-${index + 1}`,
    status: "pending", url: null, relative_path: "", media_id: "", sha256: "",
    width: 0, height: 0, content_type: "", error: "",
  }));
}

function readyCount(coverSet: CoverSet) {
  return coverSet.candidates.filter((item) => ["ready", "selected", "rejected"].includes(item.status)).length;
}

function statusLabel(status: CoverSet["status"]) {
  const labels: Record<CoverSet["status"], string> = {
    generating: "生成中",
    partial: "部分完成",
    ready: "已就绪",
    selected: "已选择",
    failed: "生成失败",
    stale: "已过期",
  };
  return labels[status] || status;
}

function directionStatusLabel(status: CoverDirection["status"]) {
  const labels: Record<CoverDirection["status"], string> = {
    awaiting_approval: "等待批准",
    approved: "已批准",
    stale: "已过期",
    rejected: "已拒绝",
  };
  return labels[status] || status;
}

function candidateStatusLabel(status: CoverCandidate["status"]) {
  const labels: Record<CoverCandidate["status"], string> = {
    pending: "等待生成",
    ready: "已就绪",
    selected: "已选择",
    rejected: "已拒绝",
    failed: "生成失败",
  };
  return labels[status] || status;
}

function qualityLabel(quality: string) {
  return ({ low: "低质量", medium: "中等质量", high: "高质量", auto: "自动" } as Record<string, string>)[quality] || quality;
}

function formatStrategy(value?: string) {
  return value?.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()) || "";
}

function statusClass(status: CoverCandidate["status"]) {
  if (status === "selected") return "border-[#8dc5a6] bg-[#eaf8f0]/90 text-[#26714a]";
  if (status === "failed") return "border-[#e3a0b1] bg-[#fff0f3]/90 text-[#a6425d]";
  if (status === "ready") return "border-white/60 bg-white/85 text-ink-text";
  if (status === "rejected") return "border-[#c7cbd5] bg-[#f1f2f5]/90 text-[#6e7482]";
  return "border-white/60 bg-white/80 text-ink-muted";
}

function qualityStatusLabel(status: NonNullable<CoverCandidate["quality_report"]>["status"]) {
  if (status === "recommended_for_human_review") return "建议人工审核";
  if (status === "human_review_required") return "需要人工审核";
  return "发现质量阻塞问题";
}
