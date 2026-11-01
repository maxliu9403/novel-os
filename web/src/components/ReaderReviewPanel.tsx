import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import Modal from "./Modal";
import { DEFAULT_REVIEW_OPTIONS, REVIEW_STATUS, readerReviewsApi, type ReaderReview, type ReviewOptions, type ReviewPreview } from "../api/readerReviews";
import "../styles/reader-reviews.css";
import { reviewDate, reviewMoney } from "../lib/readerReviews";

export default function ReaderReviewPanel({ projectId }: { projectId: string }) {
  const [reviews, setReviews] = useState<ReaderReview[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true, loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      try { const result = await readerReviewsApi.list(projectId); if (alive) { setReviews(result.reviews); setError(""); } }
      catch (e) { if (alive) setError(e instanceof Error ? e.message : "评议记录读取失败"); }
      finally { loading = false; }
    };
    void load();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 15_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [projectId, refresh]);
  return <section className="rr-panel" aria-labelledby="reader-review-heading">
    <div className="pw-section-heading"><div><span className="pw-eyebrow">从读者的视角</span><h2 id="reader-review-heading">阅读评议</h2><p>先看值得保留的亮点，再找到最影响阅读的几个问题。每条意见都可核对原文。</p></div><button className="pw-primary-button" type="button" onClick={() => setOpen(true)}>新建阅读评议</button></div>
    <p className="rr-note">独立评议不会改写正文，也不会阻止交付。阅读动力是模型的意见，仍需要目标读者验证。</p>
    {error && <p className="rr-error" role="status">{error} <button type="button" onClick={() => setRefresh(x => x + 1)}>刷新记录</button></p>}
    {!reviews.length && !error && <div className="rr-empty">还没有阅读报告。可以评议当前完整正文，或先从连续几章开始。</div>}
    <div className="rr-history">{reviews.slice(0, 5).map(review => <ReviewHistory key={review.id} projectId={projectId} review={review} />)}</div>
    {reviews.length > 5 && <details><summary>查看更早的 {reviews.length - 5} 份评议</summary><div className="rr-history">{reviews.slice(5).map(review => <ReviewHistory key={review.id} projectId={projectId} review={review} />)}</div></details>}
    <NewReaderReview key={open ? "open" : "closed"} projectId={projectId} open={open} onClose={() => setOpen(false)} />
  </section>;
}
function ReviewHistory({ projectId, review }: { projectId: string; review: ReaderReview }) {
  return <Link className="rr-history-row" to={`/projects/${encodeURIComponent(projectId)}/reader-reviews/${review.id}`}>
    <div><strong>{review.complete_book ? "全书阅读" : "部分正文阅读"}</strong><span>{reviewDate(review.created_at)} · 第 {review.options.first_chapter}–{review.options.last_chapter ?? review.expected_chapters} 章 · {review.model.model}</span></div>
    <div className="rr-history-action"><span className="rr-badge">{REVIEW_STATUS[review.status]}</span><span>{review.completed_units}/{review.unit_count} 段</span><b>查看 →</b></div>
  </Link>;
}
function NewReaderReview({ projectId, open, onClose }: { projectId: string; open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [options, setOptions] = useState<ReviewOptions>({ ...DEFAULT_REVIEW_OPTIONS });
  const [preview, setPreview] = useState<ReviewPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestKey = useRef(crypto.randomUUID());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const update = (change: Partial<ReviewOptions>) => { setOptions(v => ({ ...v, ...change })); setPreview(null); requestKey.current = crypto.randomUUID(); setError(""); };
  async function run() {
    setBusy(true); setError("");
    try {
      if (!preview) { const result = await readerReviewsApi.preview(projectId, options); if (alive.current) setPreview(result); }
      else { const result = await readerReviewsApi.create(projectId, preview, requestKey.current); if (alive.current) { onClose(); navigate(`/projects/${encodeURIComponent(projectId)}/reader-reviews/${result.id}`); } }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "请求失败"); }
    finally { if (alive.current) setBusy(false); }
  }
  // Workspace transforms and backdrop filters must not contain the fixed dialog.
  return createPortal(<Modal open={open} onClose={onClose} title="新建阅读评议">
    <div className="rr-form"><p className="rr-note">使用当前阅读器选中的正文版本。已生成的草稿也可能进入范围，具体版本会保存在快照中。</p>
      <fieldset disabled={busy}><legend className="pw-sr-only">评议设置</legend>
        <div className="rr-form-grid"><label>起始章<input type="number" min="1" value={options.first_chapter} onChange={e => update({ first_chapter: Number(e.target.value) })} /></label><label>结束章（留空为全部）<input type="number" min={options.first_chapter} value={options.last_chapter ?? ""} onChange={e => update({ last_chapter: e.target.value ? Number(e.target.value) : null })} /></label></div>
        <label>目标读者（选填）<input value={options.audience} maxLength={1000} onChange={e => update({ audience: e.target.value })} placeholder="例如：喜欢女性成长故事的成年读者" /></label>
        <label>公开故事简介（选填）<textarea value={options.public_description} maxLength={3000} onChange={e => update({ public_description: e.target.value })} placeholder="只填面向读者的简介；不要粘贴骨架或未公开的结局。独立阅读后才会对照这些期待。" /></label>
        <details><summary>调用预算与报告语言</summary><div className="rr-form-grid">
          <label>最多调用次数<input type="number" min="2" max="256" value={options.max_calls} onChange={e => update({ max_calls: Number(e.target.value) })} /></label>
          <label>总时限（分钟）<input type="number" min="1" max="720" value={options.total_seconds / 60} onChange={e => update({ total_seconds: Number(e.target.value) * 60 })} /></label>
          <label>估算预留上限（元，可空）<input type="number" min="0.01" step="0.01" value={options.max_estimated_cny ?? ""} onChange={e => update({ max_estimated_cny: e.target.value ? Number(e.target.value) : null })} /></label>
          <label>报告语言<input value={options.language} maxLength={50} onChange={e => update({ language: e.target.value })} /></label>
          <label>单次输入参考 token<input type="number" min="8000" max="64000" value={options.input_tokens} onChange={e => update({ input_tokens: Number(e.target.value) })} /></label>
          <label>预留输出参考 token<input type="number" min="2000" max="12000" value={options.output_tokens} onChange={e => update({ output_tokens: Number(e.target.value) })} /></label>
        </div><p className="rr-note">时限包含等待和暂停。CLI 内部重试、推理和实际扣款无法严格预估；金额为参考预留，上游可能超出单次预留。缺少单价时可仅设置调用数和时限。</p></details>
      </fieldset>
      {preview && <section className="rr-preview" aria-label="评议范围与预算"><strong>{preview.complete_book ? "完整正文" : "部分正文"} · {preview.available_chapters} 章 / {preview.units} 段</strong>
        {preview.versions && <p>采用版本：定稿 {preview.versions.final ?? 0} 章 · 修订稿 {preview.versions.revised ?? 0} 章 · 草稿 {preview.versions.draft ?? 0} 章</p>}
        <p>{preview.model.provider} · {preview.model.model} · 至少 {preview.minimum_calls} 次调用，最多 {preview.options.max_calls} 次</p><p>全文约 {preview.source_reference_tokens.toLocaleString()} 参考 tokens · {reviewMoney(preview.maximum_reserved_cny)}（按全部调用上限预留）</p>
        {preview.missing_chapters.length > 0 && <p>缺少正文：第 {preview.missing_chapters.join("、")} 章。报告会明确标记覆盖不足。</p>}
        {(preview.minimum_calls_timeout_ceiling ?? 0) > options.total_seconds && <p>如果每次调用都接近超时，总时限可能不足以完成本次范围；可缩小范围或明确增加总时限。</p>}
        <details><summary>范围与费用说明</summary>{preview.notes.map(note => <p key={note}>{note}</p>)}</details>
      </section>}
      {error && <p className="rr-error" role="alert">{error}</p>}
      <div className="rr-actions"><button type="button" className="pw-soft-button" onClick={onClose}>取消</button><button type="button" className="pw-primary-button" disabled={busy} onClick={() => void run()}>{busy ? "正在处理…" : preview ? "开始评议" : "预览范围与预算"}</button></div>
    </div>
  </Modal>, document.body);
}
