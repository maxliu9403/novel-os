import { useEffect, useRef, useState } from "react";
import { sourceBooksApi, type SourceSelection } from "../api/sourceBooks";

import { useSourceBooks, useSourceBook } from "../hooks/useSourceBooks";
import { readSourceDraft, saveSourceDraft } from "../lib/sourceDraft";
import SourceBookDetails from "./SourceBookDetails";
import SourceRoutes from './SourceRoutes';
import type { SourceAuthorContext, SourceRouteChoice } from '../api/sourceRoutes';
import { sourcePickerStatus } from '../lib/sourceDesignStatus';

export default function SourceBookPicker({
  value,
  onChange,
  onReady,
  persistDraft = true,
  routeDesign,
}: {
  value?: SourceSelection | null;
  onChange: (v: SourceSelection | null) => void;
  onReady: (v: boolean) => void;
  persistDraft?: boolean;
  routeDesign?: { onPendingChange?: (pending: boolean) => void; authorContext: SourceAuthorContext; draftKey: string; choice?: SourceRouteChoice | null; onChoice: (choice: SourceRouteChoice | null) => void; onContinue?: (choice: SourceRouteChoice) => Promise<void> };
}) {
  const [draft] = useState(
    () => value ?? (persistDraft ? readSourceDraft() : undefined),
  );
  const [mode, setMode] = useState<SourceSelection["mode"] | "original">(
    draft?.mode ?? "original",
  );
  const [id, setId] = useState(draft?.source_id ?? "");
  const [version, setVersion] = useState(draft?.analysis_sha256 ?? "");
  const [useCover, setUseCover] = useState(draft?.use_cover ?? true);
  const [routeReady, setRouteReady] = useState(false);
  const routeChoiceRef = useRef<SourceRouteChoice | null>(null);
  const routeEnabled = !!routeDesign;
  const listing = useSourceBooks();
  const detail = useSourceBook(
    mode === "original" ? "" : id,
    version || undefined,
  );
  const book = detail.data;
  const books = listing.data?.items ?? [];
  function refreshStatus() {
    listing.refresh();
    detail.refresh();
  }
  useEffect(() => {
    if (persistDraft)
      saveSourceDraft({
        source_id: id,
        mode,
        use_cover: useCover,
        analysis_sha256: version || undefined,
      });
  }, [persistDraft, id, mode, useCover, version]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const zip = useRef<HTMLInputElement>(null),
    folder = useRef<HTMLInputElement>(null);
  const onChangeRef = useRef(onChange),
    readyRef = useRef(onReady);
  useEffect(() => {
    onChangeRef.current = onChange;
    readyRef.current = onReady;
  }, [onChange, onReady]);
  useEffect(() => {
    const ready = mode === "original" || (routeEnabled ? routeReady : book?.id === id && !!book.result);
    readyRef.current(!!ready);
    onChangeRef.current(
      !routeEnabled && mode !== "original" && ready && book?.result
        ? {
            source_id: book.id,
            analysis_sha256: book.result.analysis_sha256,
            mode,
            use_cover: useCover,
          }
        : null,
    );
  }, [mode, id, book, useCover, routeReady, routeEnabled]);
  function changeMode(next: typeof mode) {
    setMode(next);
    setError("");
    setRouteReady(false);
    routeDesign?.onChoice(null);
    if (next !== "original") readyRef.current(false);
  }
  async function upload(files: File[]) {
    if (!files.length) return;
    setBusy(true);
    setError("");
    try {
      const added = await sourceBooksApi.upload(files);
      setId(added.id);
      setVersion("");
      setRouteReady(false);
      routeDesign?.onChoice(null);
      listing.refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="source-picker">
      <div className="source-picker-heading">
        <a
          href={
            id
              ? `/source-books?source=${encodeURIComponent(id)}`
              : "/source-books"
          }
          target="_blank"
          rel="noreferrer"
        >
          参考资料与后台任务 ↗
        </a>
      </div>
      <div
        className="knowledge-choices"
        role="radiogroup"
        aria-label="创作方式"
      >
        {(
          [
            ["original", "原创构想"],
            ["source_development", "原故事推演"],
            ["original_rewrite", "改编重写"],
            ...(mode === "reference_creation" || routeDesign
              ? [["reference_creation", routeDesign ? "借鉴创作" : "独立参考创作（已保存）"] as const]
              : []),
          ] as const
        ).map(([v, label]) => (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={mode === v}
            className={mode === v ? "selected" : ""}
            onClick={() => changeMode(v)}
          >
            {label}
          </button>
        ))}
      </div>
      {mode === "source_development" && (
        <p className="knowledge-muted">
          保留已有故事与人物，从已知进度发展新剧情。
        </p>
      )}
      {mode !== "original" && (
        <div className="source-picker-body">
          <div className="source-picker-toolbar">
            <select
              aria-label="选择参考小说"
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setVersion("");
                setRouteReady(false);
                routeDesign?.onChoice(null);
              }}
            >
              <option value="">选择已导入的小说</option>
              {books.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.title} ·{" "}
                  {sourcePickerStatus(b)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => zip.current?.click()}
            >
              导入 ZIP
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => folder.current?.click()}
            >
              导入目录
            </button>
            <input
              hidden
              type="file"
              ref={zip}
              accept=".zip"
              onChange={(e) => {
                void upload(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
            <input
              hidden
              type="file"
              multiple
              ref={folder}
              {...{ webkitdirectory: "" }}
              onChange={(e) => {
                void upload(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
          </div>
          <p className="knowledge-muted">
            metadata.json、synopsis.md、chapters.json 和 chapters/；overview.md
            可省略。
          </p>
          {book?.id === id && (
            <div>
              {!!book.versions?.length && (
                <label className="source-version-picker">
                  分析版本
                  <select
                    aria-label="参考资料版本"
                    value={version}
                    onChange={(e) => setVersion(e.target.value)}
                  >
                    <option value="">最新保存结果</option>
                    {book.versions.map((v) => (
                      <option key={v.analysis_sha256} value={v.analysis_sha256}>
                        {new Date(v.created_at).toLocaleString()} ·{" "}
                        {v.has_plan ? "含续写规划" : "阅读分析"} ·{" "}
                        {v.analysis_sha256.slice(0, 6)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <SourceBookDetails
                book={book}
                showLegacyControls={!routeDesign}
                onRefresh={() => {
                  detail.refresh();
                  listing.refresh();
                }}
              />
              {routeDesign && <SourceRoutes key={`${book.id}:${mode}`} sourceId={book.id} mode={mode} authorContext={routeDesign.authorContext} onPendingChange={routeDesign.onPendingChange} draftKey={routeDesign.draftKey} choiceOverride={routeDesign.choice} onStatusChange={refreshStatus} onContinue={routeDesign.onContinue ? choice => routeDesign.onContinue!({ ...choice, use_cover: useCover }) : undefined} onChoice={choice => { routeChoiceRef.current = choice; setRouteReady(!!choice); routeDesign.onChoice(choice ? { ...choice, use_cover: useCover } : null); }} />}
              {book.cover_url && (
                <label>
                  <input
                    type="checkbox"
                    checked={useCover}
                    onChange={(e) => { setUseCover(e.target.checked); if (routeDesign && routeChoiceRef.current) routeDesign.onChoice({ ...routeChoiceRef.current, use_cover: e.target.checked }); }}
                  />{" "}
                  将来源封面带入封面工作室
                  {mode === "reference_creation" ? "作视觉参考" : "进行二创"}
                </label>
              )}
            </div>
          )}
          {mode === "original_rewrite" && (
            <p className="knowledge-muted">
              请使用有权改编的素材，并在构想中说明要保留与改变的内容。
            </p>
          )}
        </div>
      )}
      {(error || listing.error || detail.error) && (
        <div className="source-sync-error" role="status">
          {error || "状态暂未同步，任务不会因此停止；正在重新连接。"}
          <button
            type="button"
            onClick={() => {
              listing.refresh();
              detail.refresh();
            }}
          >
            刷新状态
          </button>
        </div>
      )}
    </section>
  );
}
