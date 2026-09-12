import { useEffect, useState } from 'react';
import { api, type MethodPolicyRecord, type MethodReviewData, type MethodReport } from '../api/client';
import { evidenceSegments } from '../lib/methodEvidence';

const STATUS: Record<string, string> = {
  valid: '评审已完成 · 仅供参考', invalid: '报告或引文无效', unavailable: '未完成评审',
  incomplete: '输入覆盖不足', not_applicable: '不在评审范围', off: '已关闭',
};
const RULES: Record<string, string> = {
  nonfunctional_reaction: '动作的叙事作用', emotion_restatement: '情绪重复解释',
  unearned_commentary: '旁白判断', voice_convergence: '人物声音',
  mechanical_scene_execution: '机械场景表达', choice_causality: '选择与后果',
  ownership: '决断与回报归属', earned_bridge: '阅读期待的依据',
};
const control = 'rounded-lg border border-[var(--color-border)] bg-white/60 px-3 py-2 text-xs disabled:opacity-40';

export default function MethodReviews({ projectId, chapter }: { projectId: string; chapter: number }) {
  const [open, setOpen] = useState(false);
  return <section className="mt-4 border-t border-[var(--color-border)] pt-3">
    <button className="text-xs font-medium text-ink-muted" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? '− ' : '+ '}<span>写作评审 · 只读</span>
    </button>
    {open && <ReviewPanel projectId={projectId} chapter={chapter} />}
  </section>;
}

function ReviewPanel({ projectId, chapter }: { projectId: string; chapter: number }) {
  const [data, setData] = useState<MethodReviewData | null>(null);
  const [policy, setPolicy] = useState<MethodPolicyRecord | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [jobId, setJobId] = useState('');
  const [revision, setRevision] = useState('');
  const [run, setRun] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    Promise.all([api.methodReviews(projectId, chapter), api.methodPolicy(projectId)])
      .then(([next, config]) => { if (active) { setData(next); setPolicy(config); } })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [projectId, chapter, refresh]);

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const job = await api.getJob(jobId);
        if (!active) return;
        if (job.status === 'running') { timer = setTimeout(poll, 2000); return; }
        setJobId(''); setBusy(false);
        if (job.status === 'error') setError(job.error || '评审任务未完成');
        setRefresh(n => n + 1);
      } catch (e) {
        if (active) { setError(`${String(e)}；可刷新查看已保存的报告。`); setJobId(''); setBusy(false); }
      }
    };
    timer = setTimeout(poll, 1000);
    return () => { active = false; clearTimeout(timer); };
  }, [jobId]);

  async function changeMode(mode: 'off' | 'advisory') {
    if (!policy) return;
    setBusy(true); setError('');
    try {
      setPolicy(await api.saveMethodPolicy(projectId, policy.sha256, { ...policy.data.policy, mode }));
      setNotice('已保存，仅影响未来运行；现有任务仍使用其冻结策略。');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function review(targetRun = run, targetRevision = revision, retryOf?: string) {
    setBusy(true); setError('');
    try { setJobId((await api.startMethodReview(projectId, chapter, targetRun, targetRevision, retryOf)).job_id); }
    catch (e) { setError(String(e)); setBusy(false); }
  }

  async function keep(reportId: string, revisionId: string, index: number) {
    setBusy(true); setError('');
    try { await api.keepMethodFinding(projectId, reportId, revisionId, index); setRefresh(n => n + 1); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  return <div className="mt-3 space-y-4 rounded-xl bg-white/45 p-4 text-sm">
    <p className="text-xs leading-relaxed text-ink-muted">检查英文表达及免费章节中的选择、回报和阅读期待。报告绑定指定稿件版本，不改正文，不代替发布审批。</p>
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {notice && <p role="status" className="text-xs text-ink-muted">{notice}</p>}
    {!data || !policy ? <p className="text-xs">正在读取评审资料…</p> : <>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs">未来运行默认模式： <select aria-label="方法评审默认模式" className={control}
          value={policy.data.policy.mode} disabled={busy} onChange={e => changeMode(e.target.value as 'off' | 'advisory')}>
          <option value="advisory">仅评审</option><option value="off">关闭</option>
        </select></label>
        <button className={control} disabled={busy} onClick={() => { setError(''); setRefresh(n => n + 1); }}>刷新报告</button>
      </div>
      <div className="flex flex-wrap gap-2">
        <select aria-label="评审运行快照" className={`${control} min-w-0 max-w-full`} value={run} disabled={busy} onChange={e => setRun(e.target.value)}>
          <option value="">选择已冻结的运行快照</option>
          {data.runs.filter(r => r.status === 'ready' && r.mode === 'advisory' && (r.free_trial_end ?? 0) >= chapter)
            .map(r => <option key={r.run_id} value={r.run_id}>{r.run_id} · 免费截止第 {r.free_trial_end} 章</option>)}
        </select>
        <select aria-label="待评审稿件版本" className={`${control} min-w-0 max-w-full`} value={revision} disabled={busy} onChange={e => setRevision(e.target.value)}>
          <option value="">选择稿件版本</option>
          {data.revisions.slice().reverse().map(r => <option key={r.revision_id} value={r.revision_id}>{r.kind} · {r.revision_id.slice(0, 12)}</option>)}
        </select>
        <button className={control} disabled={busy || !run || !revision} onClick={() => review()}>{jobId ? '后台评审中…' : '评审此版本（调用模型）'}</button>
      </div>
      {data.runs.length === 0 && <p className="text-xs text-ink-muted">尚无运行快照。新全书任务确认英文及免费窗口合同后建立快照；单阶段写作不自动启动全书评审。</p>}
      {data.runs.filter(r => r.status !== 'ready').map(r => <p key={r.run_id} className="text-xs text-ink-muted">{r.run_id}：{STATUS[r.status] || r.status} · {r.reason}</p>)}
      {data.reports.length === 0 && <p className="text-xs text-ink-muted">暂无评审报告</p>}
      {data.reports.map(report => <details key={report.report_id} className="rounded-lg border border-[var(--color-border)] p-3" open={data.reports.length === 1}>
        <summary className="cursor-pointer text-xs font-medium">{STATUS[report.status] || report.status} <span className="ml-2 font-mono text-ink-muted">{report.revision_id.slice(0, 12)}</span></summary>
        <div className="mt-3 space-y-3">
          <p className="break-all text-[11px] text-ink-muted">Run {report.run_id} · 稿件 SHA {report.candidate_sha256.slice(0, 16)} · {report.created_at}</p>
          <p className="text-[11px] text-ink-muted">模型调用：{report.usage.model_calls} · Token：{report.usage.tokens ?? '未知'} · {report.usage.elapsed_seconds.toFixed(1)} 秒 · {report.coverage.complete_input ? '已提交完整输入，不代表语义检查完整' : '完整输入未确认'}</p>
          {!!report.usage.uncertain_model_calls && <p className="text-[11px] text-ink-muted">另有 {report.usage.uncertain_model_calls} 次预留请求发送状态未定，实际费用待核对，不自动重发。</p>}
          {report.error_code && <p className="text-xs text-ink-muted">{report.error_code}</p>}
          {['unavailable', 'invalid'].includes(report.status) && <button className={control} disabled={busy}
            onClick={() => review(report.run_id, report.revision_id, report.report_id)}>重新评审此版本（再次调用模型，保留旧报告）</button>}
          {report.summary && <p className="text-xs leading-relaxed">{report.summary}</p>}
          <ReviewSource projectId={projectId} report={report} />
          {report.findings.map((finding, index) => {
            const kept = data.decisions.some(d => d.report_id === report.report_id && d.finding_index === index);
            return <article key={index} className="border-l-2 border-amber-300 pl-3 text-xs leading-relaxed">
              <h4 className="font-semibold">{RULES[finding.rule_id] || finding.rule_id} · 建议</h4>
              <p>{finding.explanation}</p>
              {finding.evidence.map((span, i) => <blockquote key={i} className="my-2 whitespace-pre-wrap rounded bg-black/[0.025] p-2">
                {span.quote}<footer className="mt-1 font-mono text-[10px] text-ink-muted">原稿字符 [{span.start}, {span.end})</footer>
              </blockquote>)}
              <p>{finding.suggestion}</p>
              <button className={`${control} mt-2`} disabled={busy || kept} onClick={() => keep(report.report_id, report.revision_id, index)}>{kept ? '已记录保留表达' : '保留该表达（仅记录）'}</button>
            </article>;
          })}
        </div>
      </details>)}
    </>}
  </div>;
}

function ReviewSource({ projectId, report }: { projectId: string; report: MethodReport }) {
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError('');
    try {
      const data = await api.methodReviewSource(projectId, report.report_id);
      if (data.revision_id !== report.revision_id || data.sha256 !== report.candidate_sha256) throw new Error('原稿版本不匹配');
      setSource(data.text);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <div>
    {source === null ? <button className={control} disabled={busy} onClick={load}>{busy ? '读取原稿…' : '查看此版本原稿与高亮'}</button>
      : <details open><summary className="cursor-pointer text-xs">已验证的历史原稿（只读，不是当前编辑稿）</summary>
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-white/80 p-3 font-serif text-xs leading-relaxed">
          {evidenceSegments(source, report.findings.flatMap(f => f.evidence)).map((part, i) =>
            part.highlighted ? <mark key={i} className="bg-amber-100">{part.text}</mark> : <span key={i}>{part.text}</span>)}
        </pre>
      </details>}
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
  </div>;
}
