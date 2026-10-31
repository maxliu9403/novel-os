import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { readerApi, type ReaderChapter, type ReaderIndex } from "../api/reader";
import "../styles/book-reader.css";

const versions = { final: "定稿", revised: "修订稿", draft: "草稿" };
export default function BookReader() {
  const { id = "" } = useParams();
  return <Reader key={id} id={id} />;
}
function Reader({ id }: { id: string }) {
  const [params, setParams] = useSearchParams();
  const [index, setIndex] = useState<ReaderIndex>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const control = new AbortController();
    readerApi.index(id, control.signal).then(value => {
      if (!control.signal.aborted) { setIndex(value); setError(""); }
    }).catch(cause => { if (!control.signal.aborted) setError(String(cause instanceof Error ? cause.message : cause)); });
    return () => control.abort();
  }, [id, reload]);
  const continuous = params.get("mode") === "continuous";
  const requested = Number(params.get("chapter"));
  const chapter = index?.chapters.find(c => c.number === requested)?.number ?? index?.chapters[0]?.number;
  const position = index?.chapters.findIndex(c => c.number === chapter) ?? -1;
  function go(number: number) {
    setParams({ chapter: String(number), ...(continuous ? { mode: "continuous" } : {}) }, { replace: true });
    if (continuous) document.getElementById(`reader-chapter-${number}`)?.scrollIntoView({ block: "start" });
    else scroll.current?.scrollTo({ top: 0 });
  }
  return <section className="book-reader">
    <header className="book-reader__header">
      <div><Link className="book-reader__back" to={`/projects/${encodeURIComponent(id)}?tab=chapters`}>← 返回作品</Link>
        <h1>{index?.title || "阅读全文"}</h1><p>{index?.author || "作者未注明"} · 只读阅读 · 不会修改稿件</p></div>
      <div className="book-reader__controls" role="group" aria-label="阅读方式">
        <button type="button" aria-pressed={!continuous} onClick={() => setParams(chapter ? { chapter: String(chapter) } : {})}>逐章阅读</button>
        <button type="button" aria-pressed={continuous} onClick={() => setParams({ mode: "continuous", ...(chapter ? { chapter: String(chapter) } : {}) })}>连续阅读</button>
      </div>
    </header>
    {error ? <div className="book-reader__notice" role="alert">{error}<button type="button" onClick={() => setReload(n => n + 1)}>重新读取</button></div>
      : !index ? <p className="book-reader__notice" role="status">正在读取目录…</p>
      : !index.chapters.length ? <p className="book-reader__notice">尚无章节，完成章节创作后可在这里阅读。</p>
      : <div className="book-reader__layout">
        <nav className="book-reader__directory" aria-label="全书目录"><h2>全书目录 <span>{index.chapters.length} 章</span></h2>
          {index.chapters.map(c => <button type="button" key={c.number} aria-current={chapter === c.number ? "location" : undefined} onClick={() => go(c.number)}>
            <span>第 {c.number} 章</span><strong>{c.title || "未命名章节"}</strong><small>{c.available ? "可阅读" : "尚无正文"}</small>
          </button>)}
        </nav>
        <div className="book-reader__viewport" ref={scroll}>
          <ChapterContent key={`${id}:${continuous ? "all" : chapter}:${reload}`} id={id} index={index} chapter={continuous ? undefined : chapter} continuous={continuous} />
          {!continuous && <nav className="book-reader__pagination" aria-label="章节导航">
            <button type="button" disabled={position <= 0} onClick={() => go(index.chapters[position - 1].number)}>← 上一章</button>
            <span>{position + 1} / {index.chapters.length}</span>
            <button type="button" disabled={position >= index.chapters.length - 1} onClick={() => go(index.chapters[position + 1].number)}>下一章 →</button>
          </nav>}
        </div>
      </div>}
  </section>;
}

function ChapterContent({ id, index, chapter, continuous }: { id: string; index: ReaderIndex; chapter?: number; continuous: boolean }) {
  const [entries, setEntries] = useState<Record<number, ReaderChapter | { error: string }>>({});
  const [retry, setRetry] = useState(0);
  // Load at most two complete chapters at a time; all available text is reachable in continuous mode.
  useEffect(() => {
    const control = new AbortController();
    const chapters = continuous ? index.chapters : index.chapters.filter(c => c.number === chapter);
    let cursor = 0;
    async function worker() {
      while (cursor < chapters.length && !control.signal.aborted) {
        const current = chapters[cursor++];
        try {
          const value = await readerApi.chapter(id, current.number, control.signal);
          if (!control.signal.aborted) setEntries(old => ({ ...old, [current.number]: value }));
        } catch (cause) {
          if (!control.signal.aborted) setEntries(old => ({ ...old, [current.number]: { error: cause instanceof Error ? cause.message : String(cause) } }));
        }
      }
    }
    void worker(); if (continuous) void worker();
    return () => control.abort();
  }, [id, index, chapter, continuous, retry]);
  const chapters = continuous ? index.chapters : index.chapters.filter(c => c.number === chapter);
  const loaded = Object.keys(entries).length;
  return <>
    {continuous && <p className="book-reader__progress" role="status">连续阅读 · 已读取 {loaded}/{chapters.length} 章 · 展示完整已保存正文</p>}
    {chapters.map(c => {
      const entry = entries[c.number];
      return <article id={`reader-chapter-${c.number}`} className="book-reader__chapter" key={c.number}>
        <div className="book-reader__chapter-meta"><span>CHAPTER {String(c.number).padStart(2, "0")}</span>
          {entry && !("error" in entry) && entry.version && <span className="book-reader__version">{versions[entry.version]}</span>}
          <Link to={`/projects/${encodeURIComponent(id)}/chapters/${c.number}`}>进入编辑器 ↗</Link>
        </div>
        <h2>{c.title || `第 ${c.number} 章`}</h2>
        {!entry ? <p role="status">正在读取正文…</p> : "error" in entry ? <div role="alert">{entry.error}<button type="button" onClick={() => setRetry(n => n + 1)}>重新读取正文</button></div>
          : !entry.version || !entry.markdown.trim() ? <p className="book-reader__empty">{entry.version ? `当前保存的${versions[entry.version]}正文为空。` : "本章尚未保存正文。"}骨架中的章节介绍可在作品工作台查看。</p>
          : <div className="book-reader__prose"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: ({ alt }) => <span>{alt ? `[插图：${alt}]` : "[插图]"}</span> }}>{entry.markdown}</ReactMarkdown></div>}
      </article>;
    })}
  </>;
}
