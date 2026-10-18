import { useEffect } from "react";
import Icon from "./Icon";
import "../styles/cover-studio.css";
import { useCoverAnalysis } from "./useCoverAnalysis";
import type { CoverAnalysisRecovery, CoverStoryAnalysis } from "../api/client";

type AnalysisExecution = ReturnType<typeof useCoverAnalysis>["execution"];
const stageLabels = ["分段提取", "骨架全局整合", "原文逐段核查", "全局因果核查"];

function analysisProgress(analysis: CoverStoryAnalysis, execution: AnalysisExecution, recovery?: CoverAnalysisRecovery) {
  const expected = new Set(analysis.coverage?.expected_segment_ids || []);
  const countExpected = (ids: string[]) => new Set(ids.filter(id => expected.has(id))).size;
  const total = expected.size;
  const extracted = countExpected(analysis.coverage?.processed_segment_ids || []);
  const complete = analysis.coverage_status === "complete";
  const checks = analysis.verification_progress?.segments || {};
  const verified = complete ? total : countExpected([
    ...Object.entries(checks).filter(([, result]) => result.status === "pass").map(([id]) => id),
    ...(analysis.verification?.status === "pass" ? analysis.verification.verified_segment_ids || [] : []),
  ]);
  const attempts = analysis.attempts || [];
  const lastAttempt = attempts.at(-1);
  const terminal = ["failed", "cancelled"].includes(analysis.phase || "");
  const phase = terminal ? lastAttempt?.phase : analysis.phase;
  const verificationStarted = phase === "verify" || !!analysis.verification_progress || attempts.some(item => item.phase === "verify");
  const verificationMode = lastAttempt?.phase === "verify" ? lastAttempt.mode : undefined;
  const verificationStage = verificationMode === "global_cross_segment" ? 3
    : verificationMode === "source_segment" ? 2 : total > 0 && verified === total ? 3 : 2;
  let stage: number | null = null;
  if (complete) stage = 3;
  else if (phase === "global" || phase === "aggregate") stage = 1;
  else if (phase === "segment") stage = 0;
  else if (phase === "verify") stage = verificationStage;
  // History is only a fallback: a failed verification can trigger another
  // synthesis, whose later failure/cancellation must remain in stage two.
  else if (terminal && verificationStarted) stage = verificationStage;
  else if (terminal) stage = total > 0 && extracted === total ? 1 : 0;

  const finished = [complete || (total > 0 && extracted === total), complete || (verificationStarted && stage !== 1), complete || (total > 0 && verified === total), complete];
  const currentLabel = stage === null ? "正在解析完整骨架" : `第 ${stage + 1}/4 阶段：${stageLabels[stage]}`;
  const stateLabel = execution === "running" ? "进行中"
    : recovery?.kind === "amendment_pending" ? "待核查"
    : recovery?.kind === "amendment_available" ? "已有修订，待查看"
    : recovery?.can_resume === false ? "需要更正分析"
    : execution === "failed" ? "未通过，进度已保存"
    : execution === "paused" ? "已暂停"
    : execution === "interrupted" ? "已中断，等待继续"
    : "运行状态待确认";
  const groups = Object.keys(analysis.aggregation_progress || {}).length;
  const rows = stageLabels.map((label, index) => {
    const active = index === stage && !complete;
    // The overall completion gate can fail after the final model check passes.
    const state = active && execution === "failed" ? stateLabel : finished[index] ? "已完成" : active ? stateLabel : "未开始";
    const count = index === 0 ? `已提取 ${extracted}/${total} 段` : index === 2 ? `已核查 ${verified}/${total} 段` : "";
    const detail = index === 1 && groups > 0 ? `已保存 ${groups} 组资料整合结果` : "";
    return {label, active, finished: finished[index], text: [total > 0 ? count : "", state, detail].filter(Boolean).join(" · ")};
  });
  return {currentLabel, rows};
}

const labels: Record<string, string> = {
  core_conflict: "核心冲突", protagonist_goal: "主角目标", stakes: "代价", reader_promise: "读者承诺",
  characters: "人物", relationships: "关系", events: "冲突事件", suspense: "悬念",
  name: "姓名", narrative_role: "故事角色", aliases: "别名", identity: "身份",
  from_character_id: "发起者", to_character_id: "对象", participants: "参与者",
  relationship: "关系", epoch: "故事时点", location: "地点", action: "行动", reaction: "反应",
  consequence: "后果", question: "未解问题", withheld_answer: "暂不揭晓", description: "说明",
  statement: "故事事实", exact_quote: "原文", reason: "原因", issue: "问题", kind: "内容类型",
};
function StoryFacts({value, names, evidence = []}: {value: unknown; names: Record<string, string>; evidence?: Array<Record<string, unknown>>}) {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) return <div className="space-y-3">{value.map((item, index) => <div key={index} className="border-b border-paper-line pb-2 last:border-0"><StoryFacts value={item} names={names} evidence={evidence} /></div>)}</div>;
  if (typeof value !== "object") return <span>{names[String(value)] || String(value)}</span>;
  const record = value as Record<string, unknown>;
  const refs = Array.isArray(record.evidence_refs) ? record.evidence_refs.map(String) : [];
  const cited = evidence.filter(item => refs.includes(String(item.evidence_id)) || refs.includes(`evidence:${item.evidence_id}`));
  return <><dl className="space-y-2">{Object.entries(record).filter(([key]) => key in labels).map(([key, item]) => <div key={key}>
    <dt className="font-medium text-ink-muted">{labels[key]}</dt><dd className="mt-0.5 whitespace-pre-wrap leading-relaxed"><StoryFacts value={item} names={names} evidence={evidence} /></dd>
  </div>)}</dl>{cited.length > 0 && <details className="mt-2 border-l-2 border-violet pl-3"><summary className="cursor-pointer text-ink-muted">查看这项理解的原文依据（{cited.length}）</summary>
    {cited.map(item => <blockquote key={String(item.evidence_id)} className="mt-2 whitespace-pre-wrap leading-relaxed"><p className="text-ink-muted">{Array.isArray(item.heading_path) ? item.heading_path.join(" › ") : "骨架原文"} · 行 {String(item.line_start ?? item.display_line_start ?? "—")}</p>{String(item.exact_quote || "")}</blockquote>)}
  </details>}</>;
}

export type CoverAnalysisNotice = {message: string; active: boolean; needsAttention: boolean};

export default function CoverSkeletonPanel({ owner, onAnalysis, onStatus }: {
  owner: string; onAnalysis: (analysisId: string, freshness: string) => void;
  onStatus?: (notice: CoverAnalysisNotice) => void;
}) {
  const discussion = owner.startsWith("workshop:");
  const {source, analysis, execution, busy, error, connectionError, sourceMatches, cancelRequested, recovery,
    analyze, resume, selectSaved, cancel, preview} = useCoverAnalysis(owner, onAnalysis);
  const loading = busy || execution === "running";
  const progress = analysis ? analysisProgress(analysis, execution, recovery) : null;
  const currentPhase = progress?.currentLabel || "正在解析完整骨架";
  const status = execution === "running" ? `${cancelRequested ? "正在暂停" : "后台分析中"} · ${currentPhase}`
    : execution === "complete" ? "骨架分析完成；可查看人物、冲突和原文依据后创建方向。"
    : recovery ? recovery.message
    : execution === "failed" ? `${currentPhase} · 分析失败，进度已保存，可继续分析。`
    : execution === "paused" ? `${currentPhase} · 已暂停，已完成的分段会保留。`
    : execution === "interrupted" ? `${currentPhase} · 任务已中断，进度已保存，可继续分析。`
    : execution === "unknown" ? `${currentPhase} · 运行状态待确认，正在同步已保存进度。` : "";
  const noticeMessage = connectionError ? `分析进度暂时无法同步：${connectionError}` : recovery && execution !== "running" ? status : error || status;
  const noticeActive = loading;
  const noticeAttention = Boolean(connectionError || error || ["failed", "paused", "interrupted", "unknown"].includes(execution || ""));
  useEffect(() => {
    onStatus?.({message: noticeMessage, active: noticeActive, needsAttention: noticeAttention});
  }, [onStatus, noticeMessage, noticeActive, noticeAttention]);
  const characterNames = Object.fromEntries((analysis?.characters || []).map(item => [String(item.character_id), String(item.name || item.character_id)]));

  return <section className="cover-source" aria-label="骨架来源与故事理解">
    <div className="cover-source__header">
      <div className="cover-section-heading">
        <span className="cover-section-number" aria-hidden="true">01</span>
        <div><h2>骨架来源与故事理解</h2>
          <p>{discussion ? "已关联本次讨论骨架" : "本项目的完整小说骨架"} · 文本分析，不生成图片</p></div>
      </div>
      <div className="cover-source__actions">
        <button type="button" className="btn-ghost" disabled={loading} onClick={preview}>
          <Icon name="history" className="h-3.5 w-3.5" />重新读取骨架
        </button>
        <button type="button" className="btn-secondary" disabled={!source || loading || execution === "unknown"} onClick={analyze}>
          {loading ? "分析处理中" : "分析完整骨架"}
        </button>
      </div>
    </div>
    <p className="cover-source__scope">默认只分析完整骨架及明确声明的附件，不自动扫描章节正文。生成前会检查骨架是否变化。</p>
    {source && <div className="cover-source__tools">
      <details className="cover-source__identity">
        <summary>预览原文与来源身份 <span className="cover-source__size">{source.documents.reduce((n, x) => n + x.byte_length, 0).toLocaleString()} 字节</span></summary>
        <div className="cover-source__document">
          <p>{source.origin.relative_path}{source.origin.observed_session_revision != null && ` · 讨论修订 ${source.origin.observed_session_revision}`}</p>
          <p className="break-all">内容校验：{source.source_content_sha256}</p>
          <pre>{source.primary_text || "原文将在冻结分析后按证据展示。"}</pre>
        </div>
      </details>
      {!!source.analyses?.length && <select aria-label="已保存的骨架分析" disabled={loading || execution === "unknown"} value={analysis?.analysis_id || ""} onChange={e => e.target.value && selectSaved(e.target.value)}>
        <option value="">查看已保存分析</option>{source.analyses.map(value => <option key={value.analysis_id} value={value.analysis_id}>{value.coverage_status} · {value.analysis_id}</option>)}
      </select>}
    </div>}
    {status && <div role="status" className={`cover-source__status${execution === "complete" ? " is-complete" : ""}`}>
      <span className="cover-status-dot" aria-hidden="true" /><div><p>{status}</p>
      {execution === "running" && <p className="cover-source__status-note">{cancelRequested
        ? "正在等待已发出的请求返回并保存结果；暂停完成后可继续分析。"
        : "可以切换页面或关闭浏览器；任务会在后台继续，返回后自动恢复进度。"}</p>}</div>
    </div>}
    {progress && <div className="cover-analysis-progress">
      <ol aria-label="分析阶段进度" className="cover-analysis-steps">
        {progress.rows.map((row, index) => <li key={row.label} aria-current={row.active ? "step" : undefined}
          className={`${row.finished ? "is-finished" : ""} ${row.active ? "is-active" : ""}`}>
          <span className="cover-analysis-steps__number" aria-hidden="true">{row.finished && !row.active ? <Icon name="circle-check" className="h-3.5 w-3.5" /> : `0${index + 1}`}</span>
          <div><p className="cover-analysis-steps__label">{row.label}</p><p className="cover-analysis-steps__detail">{row.text}</p></div>
        </li>)}
      </ol>
      {analysis?.coverage_status !== "complete" && <p className="cover-source__scope">以上为各阶段独立进度；分段提取完成后，仍需完成整合与两轮核查。</p>}
    </div>}
    {connectionError && <p className="cover-source__notice">暂时无法同步进度，正在自动重连。{connectionError}</p>}
    {analysis && !sourceMatches && <p className="cover-source__notice">此分析采用的骨架与当前来源不同，可以查看记录；创建新方向前请分析当前骨架。</p>}
    {error && (recovery && ["source_correction_required", "amendment_available"].includes(recovery.kind)
      ? <details className="cover-source__error"><summary>查看此前核查问题</summary><p role="alert">{error}</p></details>
      : <p role="alert" className="cover-source__error">{error}</p>)}
    {analysis && <div className="cover-source__understanding">
      <div className="cover-source__completion">
        <p>{analysis.coverage_status === "complete" ? "已完成全段分析与全局核查"
          : recovery?.kind === "amendment_pending" && execution !== "running" ? "局部修订已保存，待核查"
          : "分析尚未完整，暂不能用于新版方向"}</p>
        {execution === "running" && <button type="button" className="btn-ghost" disabled={cancelRequested} onClick={cancel}>{cancelRequested ? "正在暂停" : "暂停分析"}</button>}
        {!loading && execution !== "unknown" && recovery?.kind === "amendment_available" && recovery.analysis_id
          && <button type="button" className="btn-secondary" onClick={() => selectSaved(recovery.analysis_id!)}>查看修订结果</button>}
        {!loading && execution !== "unknown" && analysis.coverage_status !== "complete" && recovery?.can_resume !== false
          && <button type="button" className="btn-secondary" onClick={resume}>{recovery?.kind === "amendment_pending" ? "继续核查修订结果" : "继续未完成分析"}</button>}
      </div>
      <div className="cover-fact-folders">
        {([["故事主线", analysis.story], ["人物与关系", {characters: analysis.characters, relationships: analysis.relationships}], ["冲突事件与悬念", {events: analysis.events, suspense: analysis.suspense}], ["原文证据", analysis.evidence], ["待解决问题", analysis.unresolved]] as Array<[string, unknown]>).map(([label, value]) => Boolean(value) && <details key={label}>
          <summary>{label}</summary><div className="cover-fact-folders__content"><StoryFacts value={value} names={characterNames} evidence={analysis.evidence} /></div>
        </details>)}
      </div>
    </div>}
  </section>;
}
