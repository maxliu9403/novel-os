import { useSearchParams, Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from 'react';
import Scene from "../components/Scene";
import SourceBookDetails from "../components/SourceBookDetails";
import {
  useSourceBooks,
  useSourceBook,
  useSourceUsage,
  sourceActive,
} from "../hooks/useSourceBooks";
import { sourceStatusLabels } from "../lib/sourceStatus";
import { saveSourceDraft } from "../lib/sourceDraft";
import { sourceBooksApi } from "../api/sourceBooks";
import SourceRoutes from '../components/SourceRoutes';
import { sourceRoutesApi, type SourceRouteChoice } from '../api/sourceRoutes';
import { routeCreationInput } from '../lib/workshopCreation';
import type { SourceSelection } from '../api/sourceBooks';
import { clearLocal, newOperationId, operationFor, readLocal, writeLocal } from '../lib/sourceRouteDraft';
import SourceDesignStatus from '../components/SourceDesignStatus';
import { designFilters, matchesDesignFilter, sourceMode, type DesignFilter } from '../lib/sourceDesignStatus';
import "../styles/creative-types.css";
import "../styles/source-library.css";

export default function SourceBooks() {
  const [params, setParams] = useSearchParams(),
    navigate = useNavigate();
  const listing = useSourceBooks(),
    books = listing.data?.items ?? [];
  const active = books.filter(sourceActive);
  const id = params.get("source") ?? active[0]?.id ?? books[0]?.id ?? "";
  const version = params.get("version") ?? undefined;
  const detail = useSourceBook(id, version);
  const usage = useSourceUsage(id);
  const selectedBook = books.find(book => book.id === id) ?? detail.data;
  const routeMode: SourceSelection['mode'] = sourceMode(params.get('mode')) ?? sourceMode(selectedBook?.design?.mode) ?? 'source_development';
  const [filter, setFilter] = useState<DesignFilter>('all');
  const visibleBooks = books.filter(book => matchesDesignFilter(book, filter));
  const designCount = books.reduce((count, book) => count + (book.design?.active_count ?? 0), 0);
  const readingCount = books.filter(book => ['running', 'queued'].includes(book.study.status) || book.study.worker_active).length;
  // Pin the initial mode to the URL. Background progress must not switch an
  // author's current route editor when another mode becomes the latest run.
  useEffect(() => {
    if (!selectedBook || sourceMode(params.get('mode'))) return;
    setParams(previous => {
      if (sourceMode(previous.get('mode')) || (previous.get('source') && previous.get('source') !== id)) return previous;
      const next = new URLSearchParams(previous);
      next.set('source', id);
      next.set('mode', routeMode);
      return next;
    }, { replace: true });
  }, [selectedBook, id, routeMode, params, setParams]);
  function refreshStatus() {
    listing.refresh();
    detail.refresh();
  }
  async function continueRoute(choice: SourceRouteChoice) {
    const key = `novelos-library-route-handoff:${id}:${routeMode}`;
    const draftId = readLocal<string>(`${key}:draft`) ?? newOperationId();
    writeLocal(`${key}:draft`, draftId);
    const result = await sourceRoutesApi.handoff(operationFor(key, {
      draft_id: draftId, source_id: choice.source_id, route_set_sha256: choice.route_set_sha256, route_id: choice.route_id,
      author_note: choice.author_note, mode: choice.mode, use_cover: true,
      ...(choice.adoption_scope ? { adoption_scope: choice.adoption_scope } : {}),
      input: routeCreationInput(choice),
      ...(choice.latest_route_set_sha256 ? { latest_route_set_sha256: choice.latest_route_set_sha256 } : {}),
      ...(choice.pending_feedback_run_id ? { pending_feedback_run_id: choice.pending_feedback_run_id } : {}),
    }));
    clearLocal(key); clearLocal(`${key}:draft`);
    navigate(`/workshop?session=${encodeURIComponent(result.session_id)}`);
  }
  function create(mode: "source_development" | "original_rewrite") {
    saveSourceDraft({
      source_id: id,
      mode,
      use_cover: true,
      analysis_sha256: detail.data?.result?.analysis_sha256,
    });
    navigate("/workshop?new=1");
  }
  return (
    <Scene quiet>
      <div className="knowledge-page source-library-page">
        <section className="knowledge-shell source-library">
          <header className="knowledge-page-head">
            <div>
              <span className="knowledge-eyebrow">创作资料库</span>
              <h1>参考资料</h1>
              <p>整书设计进度、分析结果和历史讨论，都保留在这里。</p>
            </div>
            <Link className="btn-primary" to="/workshop?new=1">
              导入资料 / 新建小说
            </Link>
          </header>
          <div className="source-library-status">
            <strong>
              {books.length} 本参考小说
            </strong>
            <span aria-live="polite">
              {designCount > 0 && <b>{designCount} 项路线设计进行中 · </b>}
              {readingCount > 0 && <b>{readingCount} 项阅读分析进行中 · </b>}
              关闭页面不会暂停任务
            </span>
            <button
              type="button"
              className="btn-secondary"
              onClick={refreshStatus}
            >
              刷新列表
            </button>
          </div>
          {(listing.error || detail.error) && (
            <p role="status" className="source-sync-error">
              连接暂时中断，保留上次状态，正在重新同步。
            </p>
          )}
          {!listing.data && !listing.error && <p>正在读取参考资料…</p>}
          {listing.data && !books.length && (
            <div className="source-library-empty">
              还没有参考资料。新建小说时可导入 ZIP 或目录。
            </div>
          )}
          {!!books.length && (
            <div className="source-library-columns">
              <nav aria-label="参考小说列表">
                <div className="source-design-filters" role="group" aria-label="设计状态筛选">
                  {designFilters.map(item => (
                    <button key={item.value} type="button" aria-pressed={filter === item.value} onClick={() => setFilter(item.value)}>
                      {item.label} <span>{books.filter(book => matchesDesignFilter(book, item.value)).length}</span>
                    </button>
                  ))}
                </div>
                {!visibleBooks.length && <p className="source-filter-empty" role="status">没有符合此状态的资料。已打开的设计保持不变。</p>}
                {visibleBooks.map((book) => (
                  <button
                    key={book.id}
                    type="button"
                    className={`source-library-item ${id === book.id ? "is-selected" : ""}`}
                    aria-pressed={id === book.id}
                    onClick={() => { if (book.id !== id) setParams({ source: book.id, mode: sourceMode(book.design?.mode) ?? 'source_development' }); }}
                  >
                    {book.cover_url && (
                      <img src={sourceBooksApi.coverUrl(book)} alt="" />
                    )}
                    <span>
                      <strong>{book.title}</strong>
                      <SourceDesignStatus design={book.design} />
                      <small>
                        {sourceStatusLabels[book.study.status]}
                        {book.study.total
                          ? ` · 已保存 ${book.study.completed}/${book.study.total}`
                          : ""}
                      </small>
                    </span>
                    {sourceActive(book) && !book.design && (
                      <span className="source-study-dot is-active" />
                    )}
                  </button>
                ))}
              </nav>
              <div className="source-library-detail">
                {detail.data ? (
                  <>
                    {!!detail.data.versions?.length && (
                      <label className="source-version-picker">
                        分析版本
                        <select
                          aria-label="查看分析版本"
                          value={version ?? ""}
                          onChange={(e) => setParams({ source: id, mode: routeMode, ...(e.target.value ? { version: e.target.value } : {}) })}
                        >
                          <option value="">最新保存结果</option>
                          {detail.data.versions.map((v) => (
                            <option
                              key={v.analysis_sha256}
                              value={v.analysis_sha256}
                            >
                              {new Date(v.created_at).toLocaleString()} ·{" "}
                              {v.has_plan ? "含续写规划" : "阅读分析"} ·{" "}
                              {v.analysis_sha256.slice(0, 6)}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {usage.data && (
                      <section className="source-usage" aria-label="关联创作">
                        <div className="source-usage-counts">
                          <strong>
                            {usage.data.counts.discussions} 个讨论
                          </strong>
                          <strong>
                            {usage.data.counts.in_progress} 部未完成
                          </strong>
                          <strong>
                            {usage.data.counts.completed} 部已完成
                          </strong>
                        </div>
                        {(usage.data.sessions.length > 0 ||
                          usage.data.projects.length > 0) && (
                          <details>
                            <summary>查看关联讨论与作品</summary>
                            <ul>
                              {usage.data.sessions.map((s) => (
                                <li key={s.id}>
                                  <Link to={`/workshop?session=${s.id}`}>
                                    {s.title}
                                  </Link>
                                  <span>
                                    {stateLabel(s.status)} ·{" "}
                                    {s.source.route_set_sha256 ? '整书路线' : s.source.analysis_sha256?.slice(0, 6) ?? '来源资料'}
                                  </span>
                                </li>
                              ))}
                              {usage.data.projects.map((p) => (
                                <li key={p.id}>
                                  <Link to={`/projects/${p.id}`}>
                                    {p.title}
                                  </Link>
                                  <span>
                                    {stateLabel(p.status)} ·{" "}
                                    {p.source.route_set_sha256 ? '整书路线' : p.source.analysis_sha256?.slice(0, 6) ?? '来源资料'}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </section>
                    )}
                    {usage.error && (
                      <p role="status">
                        关联记录暂未同步。
                        <button type="button" onClick={usage.refresh}>
                          重试
                        </button>
                      </p>
                    )}
                    <SourceBookDetails
                      book={detail.data}
                      showLegacyControls={false}
                      onRefresh={() => {
                        detail.refresh();
                        listing.refresh();
                      }}
                    />
                    <div className="knowledge-choices" role="radiogroup" aria-label="路线创作方式">
                      {([['source_development', '原故事推演'], ['original_rewrite', '改编重写'], ['reference_creation', '借鉴创作']] as const).map(([value, label]) => <button type="button" role="radio" aria-checked={routeMode === value} className={routeMode === value ? 'selected' : ''} key={value} onClick={() => setParams(previous => { const next = new URLSearchParams(previous); next.set('source', id); next.set('mode', value); return next; })}>{label}</button>)}
                    </div>
                    <SourceRoutes key={`${id}:${routeMode}`} sourceId={id} mode={routeMode} onContinue={continueRoute} onStatusChange={refreshStatus} />
                    {detail.data.result && (
                      <details><summary>在新建页面继续配置</summary><div className="source-library-create">
                        <button
                          type="button"
                          className="btn-primary"
                          onClick={() => create("source_development")}
                        >
                          新建页 · 原故事推演
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => create("original_rewrite")}
                        >
                          改编重写
                        </button>
                      </div></details>
                    )}
                  </>
                ) : (
                  <p>正在读取分析状态…</p>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </Scene>
  );
}

function stateLabel(status: string) {
  return (
    (
      {
        completed: "已完成",
        in_progress: "创作中",
        running: "进行中",
        paused: "已暂停",
        failed: "失败",
        error: "需处理",
        cancelled: "已取消",
        draft: "草稿",
        ready: "待启动",
        launched: "已启动创作",
      } as Record<string, string>
    )[status] ?? status
  );
}
