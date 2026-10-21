import { useEffect, useState } from "react";
import type { CoverCandidate, CoverRepairTask } from "../api/client";
import { api } from "../api/client";
import Icon from "./Icon";
import CoverCommercialReview from "./CoverCommercialReview";
import Modal, { textareaClass } from "./Modal";
import { coverArtworkUrl, refinementPromptLimit, refinementReferenceUrl, repairKind, repairKindNames } from "../lib/coverRepair";

const phaseNames: Record<string, string> = {
  planning: "准备修复方案", draft_started: "修订所选画面", draft_response: "整理修复方案",
  draft_validation: "检查方案与来源", semantic_started: "审核故事与构图", semantic_response: "核对审核意见",
  semantic_validation: "核对审核结果", generating: "生成这张封面", composing: "合成标题排版",
  reviewing: "审核修复结果", comparing: "核对候选差异", saving: "保存修复结果",
};
function IssueSummary({report}: {report: CoverCandidate["quality_report"]}) {
  const findings = Array.isArray(report?.findings) ? report.findings as Array<Record<string, unknown>> : [];
  const evidence = findings.map(item => String(item.message || item.evidence || item.description || "")).filter(Boolean);
  const lines = evidence.length ? evidence : report?.evidence || [];
  return lines.length ? <ul className="cover-repair__issues">{lines.slice(0, 5).map((line, index) => <li key={index}>{line}</li>)}</ul>
    : <p className="cover-repair__note">将依据该图片已保存的完整审核报告处理。</p>;
}

export function CoverRepairStartDialog({candidate, number, busy, error, onClose, onStart, mode = "repair", initialPrompt = ""}: {
  candidate: CoverCandidate; number: number; busy: boolean; error: string; onClose: () => void; onStart: (prompt?: string) => void;
  mode?: "repair" | "refinement";
  initialPrompt?: string;
}) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const refinement = mode === "refinement";
  const kind = refinement ? "refinement" : repairKind(candidate);
  const imageCalls = kind === "design" || kind === "render";
  const image = api.assetUrl(coverArtworkUrl(candidate) || candidate.url);
  if (refinement) return <Modal open title={`精修候选图 ${number}`} onClose={onClose}>
    <div className="cover-refinement__reference">
      {image && <img src={image} alt={`候选图 ${number} 的精修参考图片`} />}
      <div><p className="text-[14px] font-semibold">从这张图片继续优化</p>
        <p className="cover-repair__note">{coverArtworkUrl(candidate) ? "使用已保存的无标题原画作为编辑参考，再合成封面标题。" : "使用这张封面作为图片编辑参考。"}</p>
        <p className="cover-repair__note">原图、其他候选和已选交付封面会保留，新结果另存一个版本。</p></div>
    </div>
    <label className="cover-refinement__field" htmlFor="cover-refinement-prompt">优化要求</label>
    <textarea id="cover-refinement-prompt" className={textareaClass} value={prompt} onChange={event => setPrompt(event.target.value)}
      placeholder="例如：保留人物与主要构图，让眼神更坚定，减少背景杂物，光线更柔和。" rows={4}
      maxLength={refinementPromptLimit} disabled={busy} aria-describedby="cover-refinement-help" />
    <div className="cover-refinement__hint" id="cover-refinement-help"><span>说明想保留和调整的部分；人物与故事事实仍以原始来源为准。</span><span>{prompt.length}/{refinementPromptLimit}</span></div>
    <p className="cover-repair__note">先调用文本模型制定并审核精修方案。确认方案后，才会使用 1 次图片编辑调用，并审核新图片。</p>
    {error && <p role="alert" className="cover-repair__error">{error}</p>}
    <div className="mt-5 flex flex-wrap justify-end gap-2">
      <button type="button" className="btn-secondary" onClick={onClose}>关闭</button>
      <button type="button" className="btn-primary" onClick={() => onStart(prompt.trim())} disabled={busy || !prompt.trim()}>
        {busy ? "正在提交…" : "制定精修方案"}
      </button>
    </div>
  </Modal>;
  return <Modal open title={`修复候选图 ${number}`} onClose={onClose}>
    <p className="text-[14px] font-semibold">{kind ? repairKindNames[kind] : "图片"}修复</p>
    <p className="cover-repair__note">{imageCalls
      ? "先调用文本模型修订并审核这张图的方案；方案完成后，由你确认是否使用 1 次图片调用重新生成这张封面。"
      : kind === "typography" ? "从保留的原画重新合成已批准排版，再进行质量审核。不会调用图片模型。"
        : "保留现有图片，重新进行质量审核。不会调用图片模型。"}</p>
    <p className="cover-repair__note">原图、其他候选和已选交付封面会保留。修复结果单独保存，仍需检查和选择。</p>
    <details className="cover-repair__details" open><summary>本张图片的审核问题</summary><IssueSummary report={candidate.quality_report} /></details>
    {error && <p role="alert" className="cover-repair__error">{error}</p>}
    <div className="mt-5 flex flex-wrap justify-end gap-2">
      <button type="button" className="btn-secondary" onClick={onClose}>关闭</button>
      <button type="button" className="btn-primary" onClick={() => onStart()} disabled={busy || !kind}>
        {busy ? "正在提交…" : imageCalls ? "制定这张图的修复方案" : "准备这张图的修复"}
      </button>
    </div>
  </Modal>;
}

function Proposal({task, disabled, onApprove}: {task: CoverRepairTask; disabled: boolean; onApprove: () => void}) {
  const [confirmed, setConfirmed] = useState(false);
  const refinement = task.kind === "refinement";
  const plan = task.proposal?.plan;
  if (!plan) return null;
  const rows = [["修订思路", plan.design_rationale || plan.visual_strategy], ["画面中的行动", plan.frozen_action],
    ["人物与构图", plan.blocking], ["缩略图表达", plan.visual_hook?.expected_thumbnail_read],
    ["故事依据", plan.evidence_summary]];
  return <div className="cover-repair__proposal">
    {task.image_calls === 1 ? <>
      <h3>仅候选图 {task.candidate_number} 的{refinement ? "精修" : "修复"}方案</h3>
      <dl>{rows.filter(([, value]) => value).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <details className="cover-repair__details"><summary>查看完整画面方案</summary><pre>{JSON.stringify(plan, null, 2)}</pre></details>
    </> : <p>{task.kind === "typography" ? "将重新合成原画与已批准排版，再审核实际成品。" : "将对保留的图片重新进行独立质量审核。"}</p>}
    <CoverCommercialReview review={task.proposal?.commercial_review}
      commercial={task.proposal?.commercial_review?.policy_version === "cover-commercial-review.v1"}
      concepts={[{concept_id: task.concept_id, label: `候选图 ${task.candidate_number}`}]} conceptId={task.concept_id} />
    <label className="cover-repair__confirmation"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={disabled} />
      <span>{refinement ? task.retry_of_task_id ? "我已审阅原方案，确认重试精修这一张封面，新增 1 次图片编辑调用并进行质量审核。" : "我已审阅方案，确认基于原图精修这一张封面，使用 1 次图片编辑调用并进行质量审核。"
        : task.image_calls === 1 ? "我已审阅方案，确认只重新生成这一张封面，使用 1 次图片调用并进行质量审核。"
        : "确认只处理这一张并重新审核，新增图片调用为 0 次。"}</span>
    </label>
    <p className="cover-repair__note">{refinement ? "将以原图作为编辑参考，模型可能连带调整其他画面细节。" : task.image_calls === 1 ? "这是整张重绘该候选；其他候选保留。" : "质量审核仍会调用文本／视觉模型。"}原图和已选封面不会被自动替换。</p>
    <button type="button" className="btn-primary" disabled={disabled || !confirmed || !task.proposal_sha256} onClick={onApprove}>
      {refinement ? "批准并精修这 1 张" : task.image_calls === 1 ? "批准并生成这 1 张" : "确认执行 · 不重新出图"}
    </button>
  </div>;
}

export default function CoverRepairTaskPanel({task, checking, error, syncedAt, submitting, disabled, original, onApprove, compact = false, onShowDetails, onRetry, onReplan}: {
  task: CoverRepairTask | null; checking: boolean; error: string; syncedAt: number | null;
  submitting: boolean; disabled: boolean; original?: CoverCandidate; onApprove: () => void;
  compact?: boolean; onShowDetails?: () => void;
  onRetry?: (task: CoverRepairTask) => void; onReplan?: (task: CoverRepairTask) => void;
}) {
  const [now, setNow] = useState(Date.now);
  const active = task?.status === "planning" || task?.status === "generating";
  useEffect(() => {
    if (!active && !checking && !submitting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, checking, submitting]);
  if (!task && !checking && !error && !submitting) return null;
  const refinement = task?.kind === "refinement";
  const action = refinement ? "精修" : "修复";
  const uncertain = Boolean(error) || ((active || checking) && syncedAt !== null && now - syncedAt > 15000);
  const phase = refinement && task?.phase === "generating" ? "编辑这张封面" : phaseNames[task?.phase || ""]?.replaceAll("修复", action);
  const heading = uncertain ? `单张${action}状态暂未确认` : submitting ? `正在提交单张${action}操作`
    : checking && !active ? `正在同步单张${action}任务` : !task ? "正在读取单张修复任务"
      : task.status === "awaiting_approval" ? `${action}方案已就绪，等待你的确认`
        : active ? `正在${phase || (task.status === "planning" ? `规划单张${action}` : "处理这张封面")}`
          : task.status === "completed" ? `单张${action}结果已保存`
            : task.status === "interrupted" ? `单张${action}已中断` : `单张${action}失败`;
  const created = task ? Date.parse(task.created_at) : NaN;
  const end = active ? now : task?.finished_at ? Date.parse(task.finished_at) : NaN;
  const seconds = Number.isFinite(created) && Number.isFinite(end) ? Math.max(0, Math.floor((end - created) / 1000)) : null;
  const warning = uncertain || task?.status === "failed" || task?.status === "interrupted" || task?.result_quality_status === "blocked";
  const image = api.assetUrl(refinement && task ? refinementReferenceUrl(task, original) : original?.url);
  const retryable = refinement && (task?.status === "failed" || task?.status === "interrupted");
  return <section id="cover-repair-task" tabIndex={-1} className={`cover-repair${warning ? " is-warning" : ""}`} aria-label={`单张封面${action}任务`}>
    <div className="cover-repair__header">
      <div className="cover-repair__title"><span aria-hidden="true">{active || checking || submitting ? <span className="cover-direction-task__spinner" /> : <Icon name={warning ? "circle-alert" : "circle-check"} className="h-5 w-5" />}</span>
        <div role="status" aria-live="polite"><p className="cover-repair__eyebrow">{task ? `候选图 ${task.candidate_number} · ${repairKindNames[task.kind]}` : "单张封面修复"}</p><h2>{heading}</h2></div>
      </div>
      <div className="cover-repair__timing">{seconds !== null && <p>{active ? "已用时" : "总用时"} {Math.floor(seconds / 60)} 分 {String(seconds % 60).padStart(2, "0")} 秒</p>}
        <p>{syncedAt === null ? "等待首次状态同步" : `${uncertain ? "上次同步" : "状态同步"} ${new Date(syncedAt).toLocaleTimeString("zh-CN", {hour12: false})}`}</p></div>
    </div>
    {uncertain && <p className="cover-repair__note">{task ? `保留上次确认的任务状态；${task.status === "completed" ? `${action}已完成，正在同步结果。` : "正在重新查询。"}` : "正在确认是否存在后台任务。"}不会自动重复调用模型。</p>}
    {error && <p className="cover-repair__error">{error}</p>}
    {task?.error && <p className="cover-repair__error" role="alert">{task.error}</p>}
    {active && <p className="cover-repair__note">可以切换页面或关闭浏览器，返回后恢复此任务。同步时间不代表模型已有新输出。</p>}
    {(task?.status === "failed" || task?.status === "interrupted") && <p className="cover-repair__note">原图与已保存结果会保留；不会自动重试或追加出图。可检查结果后重新制定{action}方案。</p>}
    {task?.status === "completed" && <p className="cover-repair__note">{task.result_quality_status === "blocked" ? "本次结果仍有质量问题，请查看具体审核意见。" : `请检查${action}后的图片及审核结果。`}原图和其他候选已保留，新结果不会自动成为交付封面。</p>}
    {compact && task && onShowDetails && <button type="button" className="btn-secondary" onClick={onShowDetails}>查看单张{action}</button>}
    <div hidden={compact}>
    {refinement && task?.refinement_prompt && <div className="cover-refinement__request"><h3>你的优化要求</h3><p>{task.refinement_prompt}</p></div>}
    {retryable && task && <div className="cover-refinement-retry">
      {image && <div className="cover-refinement__reference"><img src={image} alt={`候选图 ${task.candidate_number} 的重试参考原图`} />
        <p className="cover-repair__note">继续使用精修前的原始图片与已保存要求。点击重试只恢复方案，批准后才会新增 1 次图片编辑调用。</p></div>}
      <div className="cover-refinement-retry__actions">
        {onRetry && <button type="button" className="btn-secondary" disabled={disabled || checking || uncertain || submitting || !image}
          onClick={() => onRetry(task)}>重试精修</button>}
        {onReplan && <button type="button" className="btn-ghost" disabled={disabled || checking || uncertain || submitting || !image}
          onClick={() => onReplan(task)}>修改要求并重新规划</button>}
      </div>
      {!image && <p className="cover-repair__note">原始参考图片暂不可用，无法重试精修。请恢复原图后重新加载。</p>}
    </div>}
    {task?.proposal_reused && task.status === "awaiting_approval" && <p className="cover-repair__note">已恢复原方案与原始参考图片，本次尚未出图。批准后将新增 1 次图片编辑调用；此前任务和费用记录保留。</p>}
    {task?.status === "awaiting_approval" && task.proposal && <div className={`cover-repair__review${image ? " has-image" : ""}`}>
      {image && <figure><img src={image} alt={`候选图 ${task.candidate_number} 的${action}前图片`} /><figcaption>{action}前 · 原图保留</figcaption></figure>}
      <div><details className="cover-repair__details"><summary>查看原审核问题</summary><IssueSummary report={task.quality_report} /></details>
        <Proposal key={`${task.task_id}:${task.proposal_sha256}`} task={task} disabled={disabled || checking || uncertain || submitting || (refinement && !image)} onApprove={onApprove} />
        {refinement && !image && <p className="cover-repair__note">原始参考图片暂不可用，请恢复原图后再批准。</p>}
        {refinement && onReplan && <button type="button" className="btn-ghost mt-3" disabled={disabled || checking || uncertain || submitting || !image}
          onClick={() => onReplan(task)}>修改要求并重新规划</button>}
      </div>
    </div>}
    </div>
  </section>;
}
