import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CoverAnalysisRecovery, type CoverAnalysisRuntime, type CoverSourcePreview, type CoverStoryAnalysis, type JobStatus } from "../api/client";

const POLICY = "require_current_head";
type Execution = NonNullable<CoverAnalysisRuntime["execution_status"]> | "unknown";
const messageOf = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
const jobAnalysisId = (job: JobStatus) => String(job.meta?.analysis_id || "");

function recoveryOf(value: CoverStoryAnalysis): CoverAnalysisRecovery | undefined {
  // Explicit null is an authoritative backend decision, including a supported
  // legacy verification-policy upgrade. Only an absent field needs fallback.
  if (value.recovery !== undefined) return value.recovery ?? undefined;
  // Older backends still expose the durable failure and spent correction
  // budget. Never offer a same-ID retry that can only repeat that failure.
  const revision = value.semantic_revision;
  if (value.last_error?.code === "analysis_semantic_repair_exhausted"
    || (revision && typeof revision === "object" && "status" in revision && revision.status === "exhausted")) {
    return {kind: "source_correction_required", can_resume: false,
      message: "本次自动修正已用尽，需要先更正分析；直接继续会重复同一错误。已完成的提取和原文证据均已保留。"};
  }
  if (value.last_error?.code === "analysis_amendment_verification_failed") {
    return {kind: "amendment_failed", can_resume: false,
      message: "修订结果尚未通过核查，请先处理指出的问题；不会自动重试。"};
  }
}

function matchesSource(value: CoverAnalysisRuntime, source: CoverSourcePreview) {
  const selector = value.source_selector;
  return (!value.source_bundle_sha256 || value.source_bundle_sha256 === source.source_bundle_sha256)
    && (!selector || (selector.source_type === source.selector.source_type
      && selector.project_id === source.selector.project_id && selector.session_id === source.selector.session_id
      && selector.archive_revision === source.selector.archive_revision));
}

function executionOf(value: CoverStoryAnalysis, job?: JobStatus, workerAbsent = false): Execution {
  if (job?.status === "running") return "running";
  if (value.execution_status) return value.execution_status;
  if (value.coverage_status === "complete") return "complete";
  if (value.phase === "cancelled" || value.last_error?.code === "analysis_cancelled"
    || (job?.status === "error" && job.error === "任务已暂停。")) return "paused";
  if (job?.status === "error" || value.phase === "failed" || value.last_error) return "failed";
  return workerAbsent ? "interrupted" : "unknown";
}

// Compatibility with a running backend that predates analysis runtime metadata.
// This existing GET only inspects jobs; it never submits a deletion or a task.
async function legacyJobs(owner: string): Promise<JobStatus[] | null> {
  if (owner.startsWith("workshop:")) return null;
  const inventory = await api.projectDeletionPreview(owner);
  const jobs = await Promise.all(inventory.running_job_ids.map(id => api.getJob(id)));
  return jobs.filter(job => job.kind === "cover.analyze" && job.status === "running");
}

export function useCoverAnalysis(owner: string, onAnalysis: (id: string, policy: string) => void) {
  const [source, setSource] = useState<CoverSourcePreview | null>(null);
  const [analysis, setAnalysis] = useState<CoverStoryAnalysis | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [cancelRequested, setCancelRequested] = useState(false);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const sourceRef = useRef<CoverSourcePreview | null>(null);
  const cancellationPending = useRef(false);

  const apply = useCallback((value: CoverStoryAnalysis, job?: JobStatus, workerAbsent = false) => {
    const next = executionOf(value, job, workerAbsent);
    // A poll begun before the cancel request may still report false. Preserve
    // the local request until the worker's terminal state confirms the drain.
    cancellationPending.current = next === "running" && Boolean(
      cancellationPending.current || job?.cancel_requested || value.cancel_requested);
    setCancelRequested(cancellationPending.current);
    setAnalysis(value); setExecution(next);
    setJobId(next === "running" ? job?.job_id || value.job_id || "" : "");
    setError(next === "failed" ? job?.error || value.job_error || value.last_error?.message || "分析未完成，进度已保存。" : "");
    const eligible = sourceRef.current && matchesSource(value, sourceRef.current);
    onAnalysis(next === "complete" && eligible ? value.analysis_id : "", POLICY);
  }, [onAnalysis]);

  useEffect(() => {
    let live = true;
    let retry: ReturnType<typeof setTimeout>;
    const selected = ++epoch.current;
    // Reset the owner-scoped subscription before accepting any asynchronous reply.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBusy(true); setSource(null); sourceRef.current = null;
    setAnalysis(null); setExecution(null); setJobId(""); setError(""); setConnectionError("");
    cancellationPending.current = false; setCancelRequested(false);
    onAnalysis("", POLICY);
    const recover = async () => {
      let recovered = false;
      try {
        if (!api.coverSource) { recovered = true; return; }
        const preview = await api.coverSource(owner);
        if (!live || selected !== epoch.current) return;
        setSource(preview); sourceRef.current = preview;
        let jobs: JobStatus[] | null = null;
        const records = preview.analyses || [];
        if (records.some(item => !item.execution_status && item.coverage_status !== "complete")) {
          try { jobs = await legacyJobs(owner); }
          catch { /* Restore durable progress even when job discovery is unavailable. */ }
        }
        const candidates = [...records].sort((a, b) => {
          const active = (value: typeof a) => value.execution_status === "running" || jobs?.some(job => jobAnalysisId(job) === value.analysis_id);
          return Number(Boolean(active(b))) - Number(Boolean(active(a)));
        });
        for (const record of candidates) {
          if (!live || selected !== epoch.current) return;
          const job = jobs?.find(item => jobAnalysisId(item) === record.analysis_id);
          if (!matchesSource(record, preview) && !job && record.execution_status !== "running") continue;
          let value: CoverStoryAnalysis;
          try { value = await api.coverAnalysis(owner, record.analysis_id); }
          catch (cause) {
            if (/404|not found|未找到/i.test(messageOf(cause))) continue;
            throw cause;
          }
          if (!live || selected !== epoch.current) return;
          if (!matchesSource(value, preview) && !job && value.execution_status !== "running") continue;
          apply(value, job, jobs !== null);
          break;
        }
        recovered = true;
        if (live && selected === epoch.current) setConnectionError("");
      } catch (cause) {
        if (live && selected === epoch.current) {
          setConnectionError(messageOf(cause));
          retry = setTimeout(recover, 1500);
        }
      } finally {
        if (recovered && live && selected === epoch.current) setBusy(false);
      }
    };
    void recover();
    return () => { live = false; clearTimeout(retry); epoch.current += 1; };
  }, [owner, reload, apply, onAnalysis]);

  const analysisId = analysis?.analysis_id || "";
  const polling = execution === "running" || execution === "unknown";
  useEffect(() => {
    if (!polling || (!analysisId && !jobId)) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const selected = epoch.current;
    const poll = async () => {
      let again = true;
      try {
        let job: JobStatus | undefined;
        let missingJob = false;
        if (jobId) {
          try { job = await api.getJob(jobId); }
          catch (cause) {
            if (/404|not found|未找到/i.test(messageOf(cause))) missingJob = true;
            else throw cause;
          }
        }
        const id = analysisId || (job && jobAnalysisId(job));
        if (!id) throw new Error("正在等待任务记录，稍后自动同步。");
        const value = await api.coverAnalysis(owner, id);
        if (!live || selected !== epoch.current) return;
        if (!value.execution_status && (missingJob || !jobId)) {
          const jobs = await legacyJobs(owner);
          if (!live || selected !== epoch.current) return;
          job = jobs?.find(item => jobAnalysisId(item) === id);
          missingJob = jobs !== null && !job;
        }
        const next = executionOf(value, job, missingJob);
        apply(value, job, missingJob); setConnectionError("");
        again = next === "running" || next === "unknown";
      } catch (cause) {
        if (live && selected === epoch.current) setConnectionError(messageOf(cause));
      } finally {
        if (live && selected === epoch.current && again) timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [analysisId, jobId, polling, owner, apply]);

  const start = async (resume = false) => {
    if (!source || busy || execution === "running" || execution === "unknown") return;
    if (resume && analysis && recoveryOf(analysis)?.can_resume === false) return;
    const selected = epoch.current;
    setBusy(true); setError(""); setConnectionError(""); onAnalysis("", POLICY);
    try {
      const job = resume && analysis
        ? await api.resumeCoverAnalysis(owner, analysis.analysis_id)
        : await api.analyzeCover(owner, {...source.selector, expected_content_sha256: source.source_content_sha256});
      if (selected !== epoch.current) return;
      if (!resume) setAnalysis(null);
      cancellationPending.current = Boolean(job.cancel_requested); setCancelRequested(cancellationPending.current);
      setJobId(job.job_id); setExecution("running");
    } catch (cause) {
      if (selected === epoch.current) setError(messageOf(cause));
    } finally {
      if (selected === epoch.current) setBusy(false);
    }
  };

  const selectSaved = async (id: string) => {
    const selected = ++epoch.current;
    setJobId(""); setExecution(null); setBusy(true); setError("");
    cancellationPending.current = false; setCancelRequested(false);
    onAnalysis("", POLICY);
    try {
      const value = await api.coverAnalysis(owner, id);
      let jobs: JobStatus[] | null = null;
      if (!value.execution_status && value.coverage_status !== "complete") {
        try { jobs = await legacyJobs(owner); } catch { /* Durable view remains available. */ }
      }
      if (selected === epoch.current) apply(value, jobs?.find(job => jobAnalysisId(job) === id), jobs !== null);
    } catch (cause) {
      if (selected === epoch.current) setError(messageOf(cause));
    } finally {
      if (selected === epoch.current) setBusy(false);
    }
  };

  const cancel = async () => {
    if (!analysis || execution !== "running" || cancellationPending.current) return;
    const selected = epoch.current;
    cancellationPending.current = true; setCancelRequested(true); setError("");
    try {
      await api.cancelCoverAnalysis(owner, analysis.analysis_id);
      // Continue polling the existing job. Cancellation acceptance is not proof
      // that concurrent model requests have returned and saved their results.
    } catch (cause) {
      if (selected === epoch.current) {
        cancellationPending.current = false; setCancelRequested(false); setError(messageOf(cause));
      }
    }
  };

  return {source, analysis, execution, busy, error, connectionError, cancelRequested,
    recovery: analysis ? recoveryOf(analysis) : undefined,
    sourceMatches: Boolean(source && analysis && matchesSource(analysis, source)),
    analyze: () => start(), resume: () => start(true), selectSaved, cancel,
    preview: () => setReload(value => value + 1)};
}
