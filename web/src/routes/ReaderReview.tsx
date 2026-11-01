import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Scene from "../components/Scene";
import { reviewDate, reviewMoney } from "../lib/readerReviews";
import { DIMENSIONS, DIMENSION_STATUS, REVIEW_STATUS, readerReviewsApi, type ReaderReview as Review, type ReviewEvidence, type ReviewFinding, type ReviewSource } from "../api/readerReviews";
import "../styles/project-workspace.css";
import "../styles/reader-reviews.css";

export function SourcePassage({ source, evidence }: { source: ReviewSource; evidence: ReviewEvidence }) {
  const mark = useRef<HTMLElement>(null);
  useEffect(() => { mark.current?.scrollIntoView?.({ block: "center", behavior: "smooth" }); }, [evidence.id]);
  const points = Array.from(source.body);
  if (source.body_sha256 !== evidence.body_sha256 || points.slice(evidence.start, evidence.end).join("") !== evidence.quote) return <p role="alert">引用与原文快照不匹配，已停止高亮。请刷新报告。</p>;
  return <pre className="rr-prose">{points.slice(0, evidence.start).join("")}<mark ref={mark} tabIndex={0} aria-label="引用原文">{points.slice(evidence.start, evidence.end).join("")}</mark>{points.slice(evidence.end).join("")}</pre>;
}

export default function ReaderReview() {
  const { id = "", reviewId = "" } = useParams();
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<ReviewEvidence | null>(null);
  const [source, setSource] = useState<ReviewSource | null>(null);
  const [sourceError, setSourceError] = useState("");
  const request = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let active = true, loading = false;
    const load = async () => {
      if (loading) return;
      loading = true; const seq = ++request.current;
      try { const item = await readerReviewsApi.get(id, reviewId); if (active && seq === request.current) { setReview(item); setError(""); } }
      catch (e) { if (active && seq === request.current) setError(e instanceof Error ? e.message : "读取失败"); }
      finally { loading = false; }
    };
    void load();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [id, reviewId, refresh]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    void readerReviewsApi.source(id, reviewId, selected.chapter).then(value => {
      if (active) setSource(value);
    }).catch(e => { if (active) setSourceError(e instanceof Error ? e.message : "原文读取失败"); });
    return () => { active = false; };
  }, [id, reviewId, selected]);
  const choose = (evidence: ReviewEvidence) => { setSource(null); setSourceError(""); setSelected(evidence); };
  async function action(kind: "pause" | "resume") {
    if (busy) return;
    setBusy(true); setError(""); ++request.current;
    try { const item = await readerReviewsApi[kind](id, reviewId); if (alive.current) { ++request.current; setReview(item); setRefresh(v => v + 1); } }
    catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "操作未完成"); }
    finally { if (alive.current) setBusy(false); }
  }
  const report = review?.report;
  const evidenceButtons = (ids: string[]) => <div className="rr-evidence-links">{ids.map(eid => report?.evidence[eid] && <button type="button" className="pw-text-button" key={eid} onClick={() => choose(report.evidence[eid])}>第 {report.evidence[eid].chapter} 章 · 查看原文 ↗</button>)}</div>;
  const finding = (item: ReviewFinding, index: number) => <article className="rr-finding" key={index}>
    <div className="rr-finding-label"><span>{String(index + 1).padStart(2, "0")}</span><b>{DIMENSIONS[item.dimension]}</b><small>{item.scope === "cross_chapter" ? "跨章节观察" : "局部观察"}</small></div>
    <h3>{item.observation}</h3><p>{item.reader_impact}</p>
    {item.suggested_action && <p><strong>建议：</strong>{item.suggested_action}</p>}{item.preserve && <p><strong>保留：</strong>{item.preserve}</p>}
    {item.uncertainty && <p className="rr-note">不同读法 / 不确定性：{item.uncertainty}</p>}{evidenceButtons(item.evidence_ids)}
  </article>;
  return <Scene quiet className="project-workspace"><div className="pw-shell rr-report-shell">
    <header className="rr-report-header"><div><Link className="pw-back-link" to={`/projects/${encodeURIComponent(id)}?tab=overview`}>← 返回作品概览</Link><span className="pw-eyebrow">阅读体验 · 独立观察</span><h1>阅读评议</h1><p>从阅读感受回到正文依据，找到最值得保留与改善的部分。</p></div><Link className="pw-soft-button" to={`/projects/${encodeURIComponent(id)}/read`}>阅读当前正文</Link></header>
    {error && <div className="rr-error" role="alert">{error} <button type="button" onClick={() => setRefresh(x => x + 1)}>重新读取</button></div>}
    {!review && !error && <p role="status">正在读取评议记录…</p>}
    {review && <>
      <section className="rr-run" aria-label="阅读评议任务状态"><div className="rr-run-top"><div><span className="rr-badge">{REVIEW_STATUS[review.status]}</span><h2>{review.title && `${review.title} · `}{review.complete_book ? "全书正文" : "部分正文"} · 第 {review.options.first_chapter}–{review.options.last_chapter ?? review.expected_chapters} 章</h2><p>{review.model.model} · {reviewDate(review.created_at)} 创建 · {reviewDate(review.updated_at)} 更新</p></div><div className="rr-actions">
        {review.worker_active && !review.pause_requested && <button type="button" className="pw-soft-button" disabled={busy} onClick={() => void action("pause")}>暂停评议</button>}
        {!review.worker_active && ["paused", "interrupted", "failed"].includes(review.status) && review.can_resume !== false && <button type="button" className="pw-primary-button" disabled={busy} onClick={() => void action("resume")}>恢复未完成部分</button>}
      </div></div>
      <div className="rr-progress" role="progressbar" aria-label="正文分段阅读覆盖" aria-valuemin={0} aria-valuemax={review.unit_count} aria-valuenow={review.completed_units}><span style={{ width: `${review.unit_count ? review.completed_units / review.unit_count * 100 : 0}%` }} /></div>
      <div className="rr-run-stats"><span>已阅读 {review.completed_units}/{review.unit_count} 段</span><span>调用 {review.call_count}/{review.options.max_calls} 次</span><span>{review.usage.usage_coverage?.total_tokens ? `${review.usage.total_tokens.toLocaleString()} 已记录 tokens` : "Token 用量待记录"}</span><span>{reviewMoney(review.usage.estimated_cost_cny)} · 估算{review.usage.cost_complete ? "" : "小计"}</span></div>
      {review.draining && <p className="rr-note" role="status">模型等待已结束，正在等待本地传输退出；期间不会允许并行恢复。服务商远端是否停止无法确认。</p>}
      {review.worker_active && <p className="rr-note" role="status">{review.pause_requested ? "正在暂停，等待当前调用退出。不会再调度下一段。" : "任务在后台继续，离开或刷新页面后可返回查看。阶段状态不代表模型持续输出或 token 正在增长。"}</p>}
      {review.error && <p className="rr-error">{review.error}</p>}
      {review.missing_chapters.length > 0 && <p className="rr-note">没有覆盖第 {review.missing_chapters.join("、")} 章，不作为完整全书评议。</p>}
      {(review.unrecorded_calls > 0 || review.usage.unknown_usage_requests > 0) && <p className="rr-note">有调用尚未返回完整用量；未知用量不按零费用计算。</p>}
      {review.status !== "completed" && !review.worker_active && <p className="rr-note">已完成的段落与响应保留。恢复仅补缺失步骤，可能再次调用模型；不增加原调用和时长预算。时限截止 {reviewDate(review.deadline)}。</p>}
    </section>
    {review.source_current === false && <p className="rr-source-notice">正文已发生变化。这份报告评议的是历史快照；下方引用仍打开当时的正文。</p>}
    {review.source_current === null && <p className="rr-source-notice">暂时无法核对当前正文版本。已保存的评议快照仍可查看。</p>}
    {!report && <section className="rr-empty"><h2>阅读报告尚未完成</h2><p>分段阅读结束后还需要综合观察和核对引用。完成的段落会作为恢复检查点保留。</p></section>}
    {report && <div className={`rr-report-layout ${selected ? "rr-report-layout--source" : ""}`}><div>
      <p className="rr-note">原文引用已由代码核对；文学判断仍是建议。没有综合评分，也不影响封面选择或交付。</p>
      <section className="rr-findings"><div className="rr-section-heading"><span>01</span><h2>值得保留</h2></div>{report.strengths.length ? report.strengths.map(finding) : <p className="rr-empty">本次未形成有充分引用依据的亮点结论，不代表作品没有优点。</p>}</section>
      <section className="rr-findings"><div className="rr-section-heading"><span>02</span><h2>优先改善</h2><small>最多 5 项</small></div>{report.issues.length ? report.issues.map(finding) : <p className="rr-empty">本次未提出需要优先改善的问题。这不等于质量已获读者验证。</p>}</section>
      <details className="rr-dimensions"><summary>展开 8 个阅读维度</summary><div className="rr-dimension-grid">{report.dimensions.map(d => <article key={d.dimension}><span className="rr-badge">{DIMENSION_STATUS[d.status]}</span><h3>{DIMENSIONS[d.dimension]}</h3><p>{d.explanation}</p>{evidenceButtons(d.evidence_ids)}</article>)}</div></details>
      <details className="rr-limitations"><summary>覆盖范围与评议边界</summary>{[...report.uncertainties, ...report.limitations].map((line, i) => <p key={i}>{line}</p>)}<p>完整阅读所选正文，共 {review.unit_count} 段；前文采用事实摘要，跨段综合压缩 {report.lossy_summary_levels} 层。未执行作者私有骨架合同检查，未收集真实读者反馈。</p><p>正文快照 {review.source_sha256.slice(0, 16)} · 代码只核实引用位置，不保证因果判断或读者吸引力正确。</p></details>
    </div>{selected && <aside className="rr-source" aria-label="评议时的原文快照"><div className="rr-source-heading"><div><strong>第 {selected.chapter} 章 · 评议时的原文</strong><p>引用已高亮 · {source?.version ?? "读取中"}</p></div><button type="button" className="pw-soft-button" onClick={() => { setSelected(null); setSource(null); }}>关闭原文</button></div>{sourceError ? <p role="alert">{sourceError}<button type="button" onClick={() => choose({ ...selected })}>重试原文</button></p> : !source ? <p role="status">正在读取快照…</p> : source.snapshot_sha256 !== review.source_sha256 ? <p role="alert">快照版本不匹配，已停止展示。</p> : <SourcePassage source={source} evidence={selected} />}</aside>}</div>}
    </>}
  </div></Scene>;
}
