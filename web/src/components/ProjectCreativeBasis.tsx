import { useEffect, useState } from "react";
import { creativeBasisUrl, projectCreativeBasisApi, type ProjectCreativeBasis as Basis } from "../api/projectCreativeBasis";

export default function ProjectCreativeBasis({ projectId }: { projectId: string }) {
  return <CreativeBasis key={projectId} projectId={projectId} />;
}

function CreativeBasis({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ data?: Basis; error?: string; loading: boolean }>({ loading: false });
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const start = window.setTimeout(() => {
      setState({ loading: true });
      projectCreativeBasisApi.get(projectId, controller.signal).then(data => {
        if (!controller.signal.aborted) setState({ data, loading: false });
      }).catch(error => {
        if (!controller.signal.aborted) setState({ error: error instanceof Error ? error.message : String(error), loading: false });
      });
    }, 0);
    return () => { window.clearTimeout(start); controller.abort(); };
  }, [projectId, open, attempt]);
  const report = state.data;
  return <details className="pw-creative-basis" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><strong>创作依据</strong><span>频道、冻结资料与历史已选设计</span></summary>
    {open && <div className="pw-creative-basis__body">
      {state.loading && <p role="status">正在读取冻结创作依据…</p>}
      {state.error && <div role="alert"><p>{state.error}</p><button type="button" className="pw-soft-button" onClick={() => setAttempt(value => value + 1)}>重新读取</button></div>}
      {report && (!report.recorded ? <p>本作品未记录冻结创作依据。</p> : <>
        <div className="pw-creative-basis__heading"><strong>读者频道 · {report.audience_channel === "female" ? "女频" : report.audience_channel === "male" ? "男频" : "未记录"}</strong>
          <a className="pw-soft-button" href={`${creativeBasisUrl(projectId)}/markdown`} download>下载创作依据 MD</a></div>
        <p>{report.authority_note}</p>
        <div className="pw-creative-basis__documents">{report.documents.map(doc => <details key={doc.id}>
          <summary>{doc.kind === "genre" ? "Genre" : "Tag"} · {doc.name} <span>v{doc.version}</span></summary>
          <p>{doc.id}</p><code>SHA-256 · {doc.source_sha256}</code>
        </details>)}</div>
        {!report.documents.length && <p>未记录冻结 Genre 与 Tags。</p>}
        <h3>历史已选设计</h3>
        {report.historical_designs.map((design, index) => <article key={`${design.id}:${index}`}>
          <h4>{design.title || "未命名方案"}</h4>
          {design.summary && <p>{design.summary}</p>}
          {design.tag_contributions.map(item => <p key={item.tag_id}><strong>{item.tag_name} 的规划作用</strong><br />{item.contribution}</p>)}
          {([['choice', '人物选择'], ['cost', '代价'], ['payoff', '看点与回报'], ['author_note', '作者当时的修改']] as const).map(([field, label]) => design[field] && <p key={field}><strong>{label}</strong><br />{design[field]}</p>)}
        </article>)}
        {!report.historical_designs.length && <p>未记录历史已选方案卡；请以采用骨架为准。</p>}
        <p className="pw-inline-note">{report.evidence_note}</p>
        <details><summary>来源校验信息</summary><p>创作任务 · {report.run_id}</p>
          <p>{report.binding === "execution_lock" ? "已核对执行锁与采用骨架" : "已核对冻结交接与采用骨架，执行锁尚未建立"}</p>
          {([['source_sha256', '采用骨架'], ['selection_sha256', '资料选择'], ['handoff_sha256', '创作交接'], ['lock_sha256', '执行锁']] as const).map(([key, label]) => report[key] && <p key={key}><strong>{label} SHA-256</strong><br /><code>{report[key]}</code></p>)}
        </details>
      </>)}
    </div>}
  </details>;
}
