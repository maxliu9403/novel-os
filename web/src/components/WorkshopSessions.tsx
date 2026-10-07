import { useEffect, useState } from "react";
import { workshopApi, type WorkshopSessionSummary } from "../api/workshop";
import { fieldClass } from "./Modal";

const STATUS = { draft: "待讨论", running: "推导中", ready: "待确认", error: "本轮失败", launched: "已启动创作" };
const PAGE_SIZE = 20;

export default function WorkshopSessions({ currentId, busy, onSelect, onNew }: {
  currentId?: string; busy: boolean; onSelect: (id: string) => void; onNew?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [request, setRequest] = useState({ query: "", offset: 0, refresh: 0 });
  const [items, setItems] = useState<WorkshopSessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    workshopApi.list(request.query, PAGE_SIZE, request.offset).then((result) => {
      if (!active) return;
      setItems((previous) => request.offset === 0 ? result.sessions
        : [...previous.filter((item) => !result.sessions.some((next) => next.id === item.id)), ...result.sessions]);
      setTotal(result.total); setError(null);
    }).catch((failure: unknown) => {
      if (active) setError(failure instanceof Error ? failure.message : String(failure));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [request]);

  function load(nextQuery: string, offset = 0) {
    setLoading(true); setError(null);
    setRequest((previous) => ({ query: nextQuery.trim(), offset, refresh: previous.refresh + 1 }));
  }

  return <section aria-label="已有头脑风暴任务">
    <div className="flex flex-wrap items-center gap-2">
      <input aria-label="搜索头脑风暴书名或任务 ID" className={`${fieldClass} min-w-0 flex-1 !rounded-xl !text-[13px]`} type="search" value={query}
        onChange={(event) => setQuery(event.target.value)} placeholder="搜索书名或任务 ID"
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); load(query); } }} />
      <button type="button" className="btn-secondary !px-3" onClick={() => load(query)} disabled={loading && query.trim() === request.query}>搜索</button>
      <button type="button" className="btn-ghost !px-2" onClick={() => load(request.query)} disabled={loading}>刷新列表</button>
    </div>
    <div className="my-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-muted">
      <span>{loading ? "正在读取任务…" : `共 ${total} 个任务，按更新时间排序`}</span>
      {onNew && <button type="button" className="btn-secondary !py-1.5 !text-[12px]" onClick={onNew} disabled={busy}>新建另一部作品的讨论</button>}
    </div>
    {error && <p role="alert" className="mb-3 rounded-xl bg-violet-soft p-3 text-[12px] leading-relaxed">列表读取失败：{error}。可以点击刷新重试。</p>}
    {!loading && !error && items.length === 0 && <p className="rounded-xl border border-dashed border-paper-line p-5 text-center text-[13px] text-ink-muted">{request.query ? "没有找到匹配的讨论，请尝试书名或任务 ID。" : "还没有保存的头脑风暴。"}</p>}
    <ul className="space-y-2">{items.map((item) => <li key={item.id}>
      <button type="button" aria-label={`恢复讨论 ${item.title}（${item.id}）`} disabled={busy || loading} onClick={() => onSelect(item.id)}
        className={`w-full rounded-2xl border p-4 text-left transition-colors hover:border-violet/40 hover:bg-violet-soft/50 disabled:opacity-50 ${item.id === currentId ? "border-violet/30 bg-violet-soft/60" : "border-paper-line bg-white/60"}`}>
        <div className="flex items-start justify-between gap-3"><span className="min-w-0 break-words text-[14px] font-semibold">{item.title}</span>
          <span className="shrink-0 rounded-full bg-white/80 px-2 py-1 text-[10px] text-violet">{STATUS[item.status]}</span></div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-muted">
          <span title={item.id}>任务 ID <code>{item.id.slice(0, 12)}</code></span>
          <time dateTime={item.updated_at}>{Number.isNaN(Date.parse(item.updated_at)) ? item.updated_at : new Date(item.updated_at).toLocaleString("zh-CN", { hour12: false })}</time>
          <span>第 {item.revision} 版 · {item.message_count} 条对话 · {item.has_prompt ? "已有骨架" : "尚未生成骨架"}</span>
          {item.id === currentId && <span className="font-medium text-violet">当前讨论</span>}
        </div>
        {item.premise_preview && <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-ink-muted">{item.premise_preview}</p>}
      </button>
    </li>)}</ul>
    {!error && items.length < total && <button type="button" className="btn-secondary mt-3 w-full" disabled={loading || busy} onClick={() => load(request.query, items.length)}>{loading ? "正在加载…" : "加载更多讨论"}</button>}
  </section>;
}
