import type { CoverCandidate, CoverConcept } from "../api/client";
import { api } from "../api/client";

const record = (value: unknown): Record<string, unknown> => (
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
);
const words = (value: unknown): string[] => {
  if (typeof value === "string") return value.trim() ? [value] : [];
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return [item];
    const object = record(item);
    return [object.observation, object.description, object.evidence, object.question, object.text]
      .filter((entry): entry is string => typeof entry === "string" && Boolean(entry.trim()));
  });
};
const labels: Record<string, string> = {
  conflict: "冲突", curiosity: "悬念", promise: "故事承诺", hierarchy: "视觉层次",
  performance: "人物表现", typography: "标题排版", core_conflict_fidelity: "核心冲突",
  causal_relationship_clarity: "因果关系", protagonist_agency: "主角行动",
  hook_promise_alignment: "钩子与承诺", canon_fidelity: "故事事实",
  required_cast_coverage: "角色关系", age_and_environment_fidelity: "年龄与生活环境",
  cinematic_storytelling: "画面叙事", genre_emotion: "类型情绪", medium_fidelity: "摄影质感",
  photorealism: "真实感", anatomy_and_physics: "人体与物理", thumbnail_clarity: "缩略图辨识",
  title_legibility_advisory: "标题可读性", core_conflict_missing: "核心冲突不清晰",
  causal_relationship_missing: "因果关系不清晰", protagonist_action_missing: "主角行动不清晰",
  reader_promise_mismatch: "故事承诺不一致", title_failure: "标题存在问题",
  title_layout_failure: "标题布局无法完成", face_quality_failure: "面部表现存在问题",
  anatomy_failure: "人体结构存在问题", repair_not_promoted: "上次修复未替换原图",
  composite_layer_conflation: "组合图层关系不清晰", weak_story_action: "人物行动缺乏说服力",
  thumbnail_action_loss: "缩略图中行动不清晰", thumbnail_clutter: "缩略图信息拥挤",
  unsupported_story_combination: "画面组合缺少故事依据", hook_missing: "故事钩子不清晰",
  portfolio_similarity: "候选图表达过于相似", duplicate_artwork: "候选画稿重复",
  title_reading_order_ambiguous: "标题阅读顺序不清晰", title_reading_order_reversed: "标题阅读顺序颠倒",
  age_mismatch: "人物年龄与故事不符", missing_character: "缺少必要角色",
  generic_ai_face: "人物面容缺乏真实感", genre_drift: "类型表达偏离定位",
};
const issueCategories: Record<string, string> = {
  core_conflict_missing: "故事表达", reader_promise_mismatch: "故事表达",
  unsupported_story_combination: "故事表达", hook_missing: "故事表达",
  protagonist_action_missing: "动作", weak_story_action: "动作", thumbnail_action_loss: "动作",
  causal_relationship_missing: "因果",
  composite_layer_conflation: "构图", thumbnail_clutter: "构图",
  portfolio_similarity: "候选差异", duplicate_artwork: "候选差异",
  title_failure: "文字", title_layout_failure: "文字",
  title_reading_order_ambiguous: "文字", title_reading_order_reversed: "文字",
  face_quality_failure: "画面工艺", anatomy_failure: "画面工艺",
  age_mismatch: "故事事实", missing_character: "角色关系",
  generic_ai_face: "画面工艺", genre_drift: "故事表达",
  repair_not_promoted: "修复结果",
};
const storyScores = ["core_conflict_fidelity", "causal_relationship_clarity", "protagonist_agency", "hook_promise_alignment", "canon_fidelity", "required_cast_coverage", "age_and_environment_fidelity"];
const craftScores = ["cinematic_storytelling", "genre_emotion", "medium_fidelity", "photorealism", "anatomy_and_physics", "thumbnail_clarity", "title_legibility_advisory"];

function Observations({ title, values }: { title: string; values: unknown }) {
  const items = words(values);
  return <div className="mt-2"><p className="font-medium text-ink-text">{title}</p>{items.length
    ? <ul className="mt-1 list-disc space-y-1 pl-4">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
    : <p className="mt-1 text-ink-muted">未提供可判断的观察</p>}</div>;
}

function Assessment({ title, values, report, dimensions }: {
  title: string; values: unknown; report: Record<string, unknown>; dimensions: string[];
}) {
  return <section className="mt-4 border-t border-paper-line pt-3">
    <h4 className="font-semibold text-ink-text">{title}</h4>
    {Object.entries(record(values)).flatMap(([key, value]) => words(value).map((text, index) => (
      <p key={`${key}-${index}`} className="mt-2"><span className="font-medium">{labels[key] || "核验说明"}：</span>{text}</p>
    )))}
    <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">{dimensions.map((name) => (
      <div key={name} className="flex items-baseline justify-between gap-2">
        <dt>{labels[name]}</dt><dd className="shrink-0 tabular-nums">{typeof report[name] === "number" ? `${report[name]}/100` : "未评估"}</dd>
      </div>
    ))}</dl>
  </section>;
}

export default function CoverQualityDetails({ candidate, concept, disabled, onReevaluate }: {
  candidate: CoverCandidate; concept?: CoverConcept; disabled?: boolean; onReevaluate?: () => void;
}) {
  const report = record(candidate.quality_report);
  const evaluation = record(report.evaluation);
  const blind = record(evaluation.blind_read);
  const decision = record(report.repair_decision);
  const assets = record(report.assets);
  const scene = record(concept?.scene_plan);
  const bound = Boolean(scene._compiled_recipe || scene.campaign || report.recipe_sha256 || report.evaluation);
  const authorization = record(scene._generation_authorization);
  const budget = typeof authorization.extra_image_attempts === "number" ? authorization.extra_image_attempts : 0;
  const consumed = Math.max(typeof authorization.image_attempts_reserved === "number" ? authorization.image_attempts_reserved : 0, candidate.attempt_history?.filter((item) => item.operation !== "typography_recomposition").length || 0);
  const remaining = Math.max(0, 1 + budget - consumed);
  const blindness = ({ artwork: "已独立观察无标题画稿", partial: "部分盲读：标题遮罩会遮住部分画面", contaminated: "盲读受到画面文字影响", unverified: "无法确认盲读未受文字影响", unavailable: "暂无独立盲读" } as Record<string, string>)[String(evaluation.blindness_level)] || "历史评估：未记录独立盲读";
  const evaluationStatus = ({ complete: "评估已完成", incomplete: "评估不完整", unavailable: "评估暂不可用", protocol_error: "评估结果无法可靠解析", projection_failed: "盲读图像准备失败", not_run: "尚未进行评估" } as Record<string, string>)[String(evaluation.evaluation_status)] || "未记录独立评估状态";
  const action = ({ design: "当前问题需要修订美术方向，重新审批后再出图。", evaluation: "请重新评估已有图片，无需重新出图。", typography: "标题需使用已批准的布局重新合成；此处不支持图像局部编辑。", render: "画面工艺需要修复。当前支持按原方案重绘整图，不支持局部编辑。", none: "请结合故事表达与画面效果做最终选择。" } as Record<string, string>)[String(decision.kind)];
  const artworkUrl = typeof assets.artwork_url === "string" ? api.assetUrl(assets.artwork_url) : null;
  const finalUrl = api.assetUrl(candidate.url);
  const findings = Array.isArray(report.findings) ? report.findings.map(record) : [];
  const issueCodes = [...new Set([
    ...words(report.blockers), ...words(decision.codes), ...words(report.repair_codes),
    ...findings.flatMap((finding) => finding.severity !== "info" && typeof finding.code === "string" && finding.code.trim() ? [finding.code] : []),
  ])];
  const findingCodes = new Set(findings.flatMap((finding) => typeof finding.code === "string" ? [finding.code] : []));
  const uncoveredCodes = issueCodes.filter((code) => !findingCodes.has(code));
  const reliableBlindRead = evaluation.blindness_level === "artwork";
  const acceptance = record(report.human_acceptance);
  const comparison = record(report.portfolio_comparison);
  const matches = Array.isArray(comparison.matches) ? comparison.matches.map(record) : [];
  return <div className="mt-3 space-y-2 text-[11px] leading-[17px] text-ink-muted">
    {!candidate.quality_report && candidate.status !== "pending" && <p>暂无质量评估，不能据此判断故事与画面是否达标。</p>}
    {issueCodes.length > 0 && <section aria-label="主要评估问题" className="rounded-md border border-[#e8dccc] bg-[#fcf8f2] p-2.5">
      <h4 className="font-medium text-ink-text">主要评估问题</h4>
      <ul className="mt-2 space-y-1.5">{issueCodes.slice(0, 3).map((code, index) => <li key={code} className="flex items-start gap-2">
        <span className="shrink-0 rounded bg-white/70 px-1.5 text-[#806447]">{issueCategories[code] || "待核验"}</span>
        <span className={index === 0 ? "font-semibold text-ink-text" : "text-ink-text"}>{labels[code] || code}</span>
        {index === 0 && <span className="ml-auto shrink-0 text-[10px]">首要</span>}
      </li>)}</ul>
      {issueCodes.length > 3 && <p className="mt-2">另 {issueCodes.length - 3} 项见下方详细评估。</p>}
    </section>}
    {bound && <div className="rounded-md bg-[#f4f3f8] p-2.5">
      <p className="text-[10px]">评估流程</p>
      <p className="font-medium text-ink-text">{evaluationStatus}</p>
      <p className="mt-1">{action || "请检查图片并完成质量评估。"}</p>
      <p className="mt-1">不会自动追加出图。已批准额外出图 {budget} 次，当前剩余 {remaining} 次。</p>
      {onReevaluate && (["ready", "selected", "rejected"].includes(candidate.status) || decision.kind === "typography") && <button type="button" className="btn-secondary mt-2 px-2 py-1 text-[11px]" disabled={disabled} onClick={onReevaluate}>{decision.kind === "typography" ? "重新合成标题并评估" : "重新评估已有图片"}</button>}
    </div>}
    {bound && <section aria-label="评估依据可靠性" className={`rounded-md border p-2.5 ${reliableBlindRead ? "border-paper-line" : "border-[#e8dccc] bg-[#fcf8f2]"}`}>
      <h4 className="font-medium text-ink-text">评估依据可靠性</h4>
      <p className="mt-1">{blindness}</p>
      {!reliableBlindRead && <p className="mt-1">流程完成不代表盲读已验证，需结合原图与具体证据复核。</p>}
    </section>}
    {Object.keys(comparison).length > 0 && <div className="rounded-md bg-[#fff8e8] p-2.5">
      <p className="font-medium text-ink-text">候选图差异检查</p>
      <p className="mt-1">{comparison.status === "unavailable" ? "当前无法完成实际图像比较，请人工对照候选图。"
        : matches.some((match) => match.kind === "exact") ? "与同批候选图存在完全相同的画稿，请检查是否重复。"
          : matches.length ? "与同批候选图的画面可能过于相似，请并排检查构图与人物关系。" : "未发现像素层面的重复。仍需人工判断故事表达是否雷同。"}</p>
      <p className="mt-1">此项只比较图像相似度，不代表故事差异或商业吸引力评分。</p>
    </div>}
    {candidate.quality_report && <details className="rounded-md border border-paper-line px-2.5 py-2">
      <summary className="cursor-pointer font-medium text-ink-text">查看故事与画面评估</summary>
      <section className="mt-3"><h4 className="font-semibold text-ink-text">A · 未知故事时看到什么</h4>
        {Object.keys(blind).length > 0 ? <>
          <Observations title="人物关系" values={blind.relationships} />
          <Observations title="可见行动" values={blind.actions} />
          <Observations title="利害与异常" values={[...words(blind.stakes), ...words(blind.anomalies)]} />
          <Observations title="画面留下的问题" values={blind.open_questions} />
        </> : <p className="mt-2">未取得独立观察，不能用故事梗概代替观众的理解。</p>}
        {words(evaluation.uncertainty).length > 0 && <Observations title="不确定之处" values={evaluation.uncertainty} />}
      </section>
      <Assessment title="B · 对照故事后的核验" values={evaluation.story_assessment} report={report} dimensions={storyScores} />
      <Assessment title="画面与排版" values={evaluation.craft_assessment} report={report} dimensions={craftScores} />
      {(findings.length > 0 || issueCodes.length > 0 || words(report.evidence).length > 0) && <section aria-label="具体证据与问题" className="mt-4 border-t border-paper-line pt-3">
        <h4 className="font-semibold text-ink-text">具体证据与问题</h4>
        <ul className="mt-2 space-y-3">{findings.map((finding, index) => <li key={`finding-${index}`}>
          <p><span className="font-medium text-ink-text">{labels[String(finding.code)] || (typeof finding.code === "string" ? finding.code : "核验发现")}</span>
            <span className="ml-2">{finding.severity === "blocker" ? "阻塞" : finding.severity === "warning" ? "注意" : "观察"}</span></p>
          {typeof finding.message === "string" && <p className="mt-1">{finding.message}</p>}
          <p className="mt-1">{typeof finding.evidence === "string" && finding.evidence.trim() ? `视觉证据：${finding.evidence}` : "未提供对应的单独视觉证据，请结合已有观察复核。"}</p>
        </li>)}{uncoveredCodes.map((code) => <li key={`uncovered-${code}`}>
          <p className="font-medium text-ink-text">{labels[code] || code}</p>
          <p className="mt-1">未提供对应的单独视觉证据，请结合已有观察复核。</p>
        </li>)}</ul>
        {words(report.evidence).length > 0 && <Observations title="其他评估记录" values={report.evidence} />}
      </section>}
    </details>}
    {(artworkUrl || finalUrl) && <div className="flex flex-wrap gap-3">
      {artworkUrl && <a href={artworkUrl} target="_blank" rel="noreferrer" className="underline">查看无标题画稿</a>}
      {finalUrl && <a href={finalUrl} target="_blank" rel="noreferrer" className="underline">查看完整封面</a>}
    </div>}
    {Boolean(candidate.attempt_history?.length) && <details className="rounded-md border border-paper-line px-2.5 py-2">
      <summary className="cursor-pointer font-medium text-ink-text">生成与修复对比（{candidate.attempt_history!.length} 次）</summary>
      <div className="mt-2 grid grid-cols-2 gap-2">{candidate.attempt_history!.map((attempt, index) => {
        const url = typeof attempt.url === "string" ? api.assetUrl(attempt.url) : null;
        const current = attempt.image_sha256 === candidate.sha256 && Boolean(candidate.sha256);
        const quality = record(attempt.quality_report);
        const attemptLabel = attempt.operation === "typography_recomposition" ? "本地标题合成" : index === 0 ? "初次生成" : `修复 ${index}`;
        return <div key={String(attempt.attempt_id || index)} className="min-w-0">
          <p className="font-medium text-ink-text">{attemptLabel}{current ? " · 当前" : ""}</p>
          {url && <a href={url} target="_blank" rel="noreferrer" aria-label={`查看${attemptLabel}图片`}><img src={url} alt={`${attemptLabel}封面`} className="mt-1 aspect-[2/3] w-full rounded object-cover" /></a>}
          {attempt.status === "failed" ? <p>生成失败{typeof attempt.error === "string" ? `：${attempt.error}` : ""}</p> : <p>{quality.status === "recommended_for_human_review" ? "达到建议标准，待人工选择" : quality.status === "blocked" ? "存在质量问题" : "需要人工检查"}</p>}
        </div>;
      })}</div>
    </details>}
    {typeof acceptance.reason === "string" && <p className="rounded-md bg-[#fff8e8] p-2">人工接受理由：{acceptance.reason}</p>}
  </div>;
}
