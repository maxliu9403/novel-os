import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  creativeTypesApi,
  type CreativeRecord,
  type CreativeSummary,
  type KnowledgeDocument,
  type KnowledgeKind,
  type AudienceChannel,
} from "../api/creativeTypes";
import Scene from "../components/Scene";
import Modal from "../components/Modal";
import TitlePromptEditor from "../components/TitlePromptEditor";
import KnowledgeMarkdownPreview from "../components/KnowledgeMarkdownPreview";
import AudienceGuidanceEditor from "../components/AudienceGuidanceEditor";
import { AUDIENCE_CHANNELS, AUDIENCE_LABELS, audienceLabel } from "../lib/audience";
import { useConfirm } from "../components/confirmContext";
import { knowledgeExcerpt } from "../lib/knowledgeExcerpt";
import { splitKnowledgeMarkdown } from "../lib/knowledgeMarkdown";
import "../styles/creative-types.css";

const statuses = { enabled: "已启用", draft: "草稿", disabled: "已停用" };
const bodyOf = (markdown: string, document?: KnowledgeDocument | null) =>
  document?.body ?? markdown.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "");
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function CreativeTypes() {
  const [titlePromptOpen, setTitlePromptOpen] = useState(false);
  const [audienceOpen, setAudienceOpen] = useState(false);
  const [audienceFilter, setAudienceFilter] = useState<"all" | "missing" | AudienceChannel>("all");
  const confirm = useConfirm();
  const [items, setItems] = useState<CreativeSummary[]>([]),
    [record, setRecord] = useState<CreativeRecord | null>(null);
  const [markdown, setMarkdown] = useState(""),
    [query, setQuery] = useState(""),
    [kind, setKind] = useState<"all" | KnowledgeKind>("all");
  const [tab, setTab] = useState<"edit" | "preview" | "versions">("preview"),
    [parsed, setParsed] = useState<KnowledgeDocument | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  const [createOpen, setCreateOpen] = useState(false),
    [newKind, setNewKind] = useState<KnowledgeKind>("tag"),
    [newName, setNewName] = useState("");
  const [body, setBody] = useState("");
  const [initialBody, setInitialBody] = useState("");
  const [pasteNotice, setPasteNotice] = useState("");
  const [importBody, setImportBody] = useState<string | null>(null);
  const [importTarget, setImportTarget] = useState("");
  const requestSerial = useRef(0);
  const liveRef = useRef(true);
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const dirty = !!record && (markdown !== record.draft || body !== initialBody);
  const load = useCallback(async (id: string) => {
    const serial = ++requestSerial.current;
    setBusy(true);
    setError("");
    try {
      const next = await creativeTypesApi.get(id);
      if (liveRef.current && serial === requestSerial.current) {
        setRecord(next);
        setMarkdown(next.draft);
        setBody(bodyOf(next.draft, next.document));
        setInitialBody(bodyOf(next.draft, next.document));
        setParsed(next.document ?? null);
        setPasteNotice("");
        setTab("preview");
        return next;
      }
    } catch (e) {
      if (liveRef.current && serial === requestSerial.current)
        setError(errorText(e));
    } finally {
      if (liveRef.current && serial === requestSerial.current) setBusy(false);
    }
  }, []);
  const refresh = useCallback(async () => {
    const library = await creativeTypesApi.list();
    setItems(library.items);
    return library.items;
  }, []);
  useEffect(() => {
    let live = true;
    liveRef.current = true;
    creativeTypesApi
      .list()
      .then((library) => {
        if (live) {
          setItems(library.items);
          if (library.items[0]) void load(library.items[0].id);
        }
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
      liveRef.current = false;
    };
  }, [load]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function discard() {
    return (
      !dirty ||
      (await confirm({
        title: "当前修改尚未保存",
        message: "离开会放弃当前编辑内容。",
        confirmLabel: "放弃修改",
      }))
    );
  }
  async function choose(id: string) {
    if (!busy && (await discard())) await load(id);
  }
  async function pasteDocument(text: string) {
    setBusy(true);
    setError("");
    setPasteNotice("");
    try {
      const prepared = await creativeTypesApi.prepare(text, { base_markdown: markdown });
      if (prepared.needs_identity) throw new Error("请通过「导入 MD」补充资料信息。");
      setBody(bodyOf(prepared.markdown, prepared.document));
      setPasteNotice("已识别文件信息，仅粘贴正文。");
    } catch (e) {
      setBody(text);
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(
    action: "draft" | "activate" | "disable" | "delete" | "restore",
    version?: number,
  ) {
    if (!record || busy) return;
    if (
      action === "restore" &&
      !(await confirm({
        title: `使用 v${version}？`,
        message:
          "新讨论将使用此版本；已有讨论保持原快照。当前未保存的编辑会被替换。",
        confirmLabel: "使用此版本",
      }))
    )
      return;
    if (
      action === "delete" &&
      !(await confirm({
        title: "删除这份写作资料？",
        message: "新作品不再使用它；已冻结的故事资料仍然保留。",
        confirmLabel: "删除",
        danger: true,
      }))
    )
      return;
    if (action === "disable" && dirty && !(await discard())) return;
    const signature = JSON.stringify([
      record.id,
      record.revision,
      action,
      markdown,
      body,
      version,
    ]);
    if (operation.current?.signature !== signature)
      operation.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      let content = markdown;
      if (["draft", "activate"].includes(action) && body !== initialBody) {
        const prepared = await creativeTypesApi.prepare(body, { base_markdown: markdown });
        if (prepared.needs_identity) throw new Error("资料信息缺失，请重新打开资料。");
        content = prepared.markdown;
      }
      const next = await creativeTypesApi.mutate(
        record.id,
        action,
        record.revision,
        operation.current.id,
        ["draft", "activate"].includes(action) ? content : undefined,
        version,
      );
      operation.current = null;
      if (action === "delete") {
        setRecord(null);
        setMarkdown("");
        const remaining = await refresh();
        if (remaining[0]) await load(remaining[0].id);
      } else {
        setRecord(next);
        setMarkdown(next.draft);
        setBody(bodyOf(next.draft, next.document));
        setInitialBody(bodyOf(next.draft, next.document));
        setParsed(next.document ?? null);
        if (action === "restore") setTab("preview");
        await refresh();
        setSaved(true);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function stageImport(text: string, document: KnowledgeDocument, existingRecord?: CreativeRecord) {
    const existing = items.find((i) => i.id === document.id);
    if (existing) {
      const current = existingRecord ?? await creativeTypesApi.get(existing.id);
      setRecord(current);
      setMarkdown(text);
      setParsed(document);
      setBody(bodyOf(text, document));
      setInitialBody(bodyOf(text, document));
      setTab("preview");
    } else {
      const created = await creativeTypesApi.create(text, crypto.randomUUID());
      await refresh();
      await load(created.id);
    }
  }
  async function create() {
    if (busy) return;
    if (!importTarget && !newName.trim()) return;
    setBusy(true);
    setError("");
    try {
      const target = importTarget && importBody !== null
        ? await creativeTypesApi.get(importTarget) : undefined;
      const prepared = target && importBody !== null
        ? await creativeTypesApi.prepare(importBody, {
            base_markdown: target.draft,
          })
        : await creativeTypesApi.prepare(importBody ?? "", { identity: {
            id: `${newKind}.custom_${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`,
            kind: newKind, name: newName.trim(),
            genre_ids: [],
          } });
      if (prepared.needs_identity) throw new Error("请补充资料名称和分类。");
      await stageImport(prepared.markdown, prepared.document, target);
      setCreateOpen(false);
      setNewName("");
      setImportBody(null);
      setImportTarget("");
      if (importBody === null) setTab("edit");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function importMarkdown(file: File | undefined) {
    if (!file) return;
    if (file.size > 24576) {
      setError("Markdown 上限 24 KiB；内容不会截断。");
      return;
    }
    if (!(await discard())) return;
    setBusy(true);
    setError("");
    try {
      const text = await file.text();
      const prepared = await creativeTypesApi.prepare(text);
      if (prepared.needs_identity) {
        setImportBody(prepared.body);
        setImportTarget("");
        setNewName(file.name.replace(/\.md$/i, ""));
        setCreateOpen(true);
      } else {
        await stageImport(prepared.markdown, prepared.document);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const filtered = items.filter(
    (i) =>
      (kind === "all" || i.kind === kind) &&
      (audienceFilter === "all" || (audienceFilter === "missing" ? !i.audience_ready : i.audience_channels?.includes(audienceFilter))) &&
      [i.name, i.id, ...i.aliases].some((s) =>
        s.toLowerCase().includes(query.toLowerCase()),
      ),
  );
  return (
    <Scene quiet>
      <div className="knowledge-page">
        <section className="knowledge-shell">
          <header className="knowledge-page-head">
            <div>
              <span className="knowledge-eyebrow">创作资料库</span>
              <h1>Genre 与 Tags</h1>
              <p>每一种选择，都成为故事的动力。</p>
            </div>
            <div className="knowledge-actions">
              <button type="button" className="btn-secondary" onClick={() => setTitlePromptOpen(true)}>书名设计提示词</button>
              <Link className="btn-secondary" to="/workshop">
                进入头脑风暴 ↗
              </Link>
              <label className="btn-secondary">
                导入 Markdown
                <input
                  hidden
                  type="file"
                  accept=".md,text/markdown,text/plain"
                  disabled={busy}
                  onChange={(e) => {
                    void importMarkdown(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() =>
                  void discard().then((ok) => {
                    if (ok) {
                      setImportBody(null);
                      setImportTarget("");
                      setNewName("");
                      setCreateOpen(true);
                    }
                  })
                }
              >
                ＋ 新建资料
              </button>
            </div>
          </header>
          {error && (
            <div role="alert" className="knowledge-error">
              {error}
            </div>
          )}
          <div className="knowledge-workspace">
            <aside className="knowledge-library" aria-label="写作资料列表">
              <input
                className="knowledge-search"
                aria-label="搜索资料"
                placeholder="搜索名称、别名"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="knowledge-tabs" aria-label="筛选资料">
                {(["all", "genre", "tag"] as const).map((k) => (
                  <button
                    type="button"
                    aria-pressed={kind === k}
                    className={kind === k ? "active" : ""}
                    key={k}
                    onClick={() => setKind(k)}
                  >
                    {k === "all" ? "全部" : k === "genre" ? "Genres" : "Tags"}
                  </button>
                ))}
              </div>
              <select className="knowledge-search knowledge-channel-filter" aria-label="筛选适用频道" value={audienceFilter} onChange={e => setAudienceFilter(e.target.value as typeof audienceFilter)}>
                <option value="all">全部频道</option>
                {AUDIENCE_CHANNELS.map(channel => <option key={channel} value={channel}>{AUDIENCE_LABELS[channel]}</option>)}
                <option value="missing">待补频道指导</option>
              </select>
              <div className="knowledge-list">
                {filtered.map((i) => (
                  <button
                    type="button"
                    key={i.id}
                    disabled={busy}
                    aria-current={record?.id === i.id ? "true" : undefined}
                    onClick={() => void choose(i.id)}
                    className={record?.id === i.id ? "active" : ""}
                  >
                    <div>
                      <strong>{i.name}</strong>
                      <span className={`knowledge-status ${i.status}`}>
                        {statuses[i.status]}
                      </span>
                    </div>
                    <p>{knowledgeExcerpt(i.promise) || "等待填写创作说明"}</p>
                    <small>
                      {i.kind === "genre" ? "主类型" : "标签"} ·{" "}
                      {i.active_version ? `v${i.active_version}` : "未启用"}
                      {i.has_draft_changes ? " · 有草稿修改" : ""}
                    </small>
                    <span className="knowledge-audience-badge">{audienceLabel(i.audience_channels)}</span>
                  </button>
                ))}
              </div>
            </aside>
            {record ? (
              <section
                className="knowledge-editor"
                aria-label="Markdown 编辑器"
              >
                <header>
                  <div>
                    <h2>
                      {parsed?.name ?? record.document?.name ??
                        items.find((i) => i.id === record.id)?.name ??
                        "新资料"}
                    </h2>
                    <p>
                      {record.id} · {statuses[record.status]}
                      {record.active_version
                        ? ` · v${record.active_version}`
                        : ""}
                    </p>
                  </div>
                  <div className="knowledge-actions">
                    <a
                      className="btn-ghost"
                      href={creativeTypesApi.downloadUrl(record.id)}
                    >
                      下载 MD
                    </a>
                    {record.status === "enabled" && (
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busy}
                        onClick={() => void save("disable")}
                      >
                        停用
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn-ghost knowledge-danger"
                      disabled={busy}
                      onClick={() => void save("delete")}
                    >
                      删除
                    </button>
                  </div>
                </header>
                <div className="knowledge-audience-bar">
                  <span>适用频道 <strong>{audienceLabel(parsed?.audience_guidance?.audience_channels)}</strong></span>
                  <button type="button" className="btn-secondary" disabled={busy} onClick={async () => {
                    if (await discard()) { if (await load(record.id)) setAudienceOpen(true); }
                  }}>维护频道指导</button>
                </div>
                <div
                  className="knowledge-tabs"
                  role="tablist"
                  aria-label="资料视图"
                >
                  {(["preview", "edit", "versions"] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      role="tab"
                      aria-selected={tab === t}
                      className={tab === t ? "active" : ""}
                      disabled={busy}
                      onClick={() => setTab(t)}
                    >
                      {t === "edit"
                        ? "编写 Markdown"
                        : t === "preview"
                          ? "阅读预览"
                          : "版本记录"}
                    </button>
                  ))}
                </div>
                <p className="knowledge-muted knowledge-guidance-note">
                  {parsed?.schema === "novel-knowledge.v3" || body !== initialBody
                    ? "正文完整用于创作，标题与分节不限。"
                    : "此版本按原分节规则读取；编辑保存后将使用完整正文。"}
                </p>
                {pasteNotice && <p className="knowledge-paste-notice" role="status">{pasteNotice}</p>}
                <div className="knowledge-editor-body">
                  {tab === "edit" && (
                    <textarea
                      aria-label="资料 Markdown"
                      spellCheck={false}
                      disabled={busy}
                      value={body}
                      placeholder="写下阅读期待、故事机制、人物关系或表达要求。标题与段落自由安排。"
                      onChange={(e) => { setBody(e.target.value); setPasteNotice(""); }}
                      onPaste={(e) => {
                        const text = e.clipboardData.getData("text/plain");
                        if (e.currentTarget.selectionStart === 0 &&
                          e.currentTarget.selectionEnd === body.length &&
                          splitKnowledgeMarkdown(text).metadata.length > 0) {
                          e.preventDefault();
                          void pasteDocument(text);
                        }
                      }}
                    />
                  )}
                  {tab === "preview" && <>
                    <KnowledgeMarkdownPreview markdown={body} />
                    {parsed?.audience_guidance && <div className="knowledge-audience-preview">
                      {AUDIENCE_CHANNELS.filter(channel => parsed.audience_guidance?.guides[channel]?.trim()).map(channel => <details key={channel}>
                        <summary>{AUDIENCE_LABELS[channel]}写作指导 <span>{parsed.audience_guidance!.audience_channels.includes(channel) ? "已启用" : "暂未启用"}</span></summary>
                        <KnowledgeMarkdownPreview markdown={parsed.audience_guidance!.guides[channel]!} />
                      </details>)}
                    </div>}
                  </>}
                  {tab === "versions" && (
                    <div className="knowledge-versions">
                      <p>可使用历史版本，已有作品的资料保持不变。</p>
                      {record.versions.toReversed().map((v) => (
                        <details key={v.version}>
                          <summary>
                            v{v.version}
                            {record.active_version === v.version
                              ? " · 当前启用"
                              : ""}{" "}
                            <span>
                              {new Date(v.created_at).toLocaleString()}
                            </span>
                          </summary>
                          <small>SHA-256 · {v.sha256}</small>
                          <KnowledgeMarkdownPreview markdown={bodyOf(v.markdown)} />
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={
                              busy ||
                              (record.active_version === v.version &&
                                record.status === "enabled")
                            }
                            onClick={() => void save("restore", v.version)}
                          >
                            使用 v{v.version}
                          </button>
                        </details>
                      ))}
                    </div>
                  )}
                </div>
                <footer className="knowledge-savebar">
                  <span>
                    {busy ? "处理中…" : dirty ? "有未保存的修改" : "资料已保存"}{" "}
                    <small>
                      {new TextEncoder()
                        .encode(body)
                        .length.toLocaleString()}{" "}
                      B 正文 · 文件上限 24 KiB
                    </small>
                  </span>
                  <div className="knowledge-actions">
                    {dirty && (
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => void save("draft")}
                      >
                        保存草稿
                      </button>
                    )}
                    {(dirty ||
                      record.status !== "enabled" ||
                      record.versions.find(
                        (v) => v.version === record.active_version,
                      )?.markdown !== markdown) && (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void save("activate")}
                      >
                        保存并启用
                      </button>
                    )}
                  </div>
                </footer>
              </section>
            ) : (
              <div className="knowledge-empty">
                {busy ? "正在读取…" : "选择或新建一份写作资料"}
              </div>
            )}
          </div>
        </section>
      </div>
      {titlePromptOpen && <TitlePromptEditor onClose={() => setTitlePromptOpen(false)} />}
      {audienceOpen && record && <AudienceGuidanceEditor key={`${record.id}:${record.revision}`} record={record} onClose={() => setAudienceOpen(false)} onSaved={next => {
        setAudienceOpen(false);
        setRecord(next);
        setMarkdown(next.draft);
        setBody(bodyOf(next.draft, next.document));
        setInitialBody(bodyOf(next.draft, next.document));
        setParsed(next.document ?? null);
        setSaved(true);
        void refresh().catch(e => setError(errorText(e)));
      }} />}
      <Modal
        open={createOpen}
        onClose={() => {
          if (!busy) setCreateOpen(false);
        }}
        title={importBody === null ? "新建写作资料" : "导入写作资料"}
      >
        <div className="knowledge-form">
          {importBody !== null && (
            <label>保存到
              <select value={importTarget} onChange={(e) => setImportTarget(e.target.value)}>
                <option value="">新建资料</option>
                {items.map((item) => <option value={item.id} key={item.id}>替换：{item.name}</option>)}
              </select>
            </label>
          )}
          {!importTarget && <>
          <label>
            资料种类
            <select
              value={newKind}
              onChange={(e) => setNewKind(e.target.value as KnowledgeKind)}
            >
              <option value="genre">Genre · 主类型</option>
              <option value="tag">Tag · 创作标签</option>
            </select>
          </label>
          <label>
            名称
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="例如：第二次机会"
            />
          </label>
          </>}
          {importBody !== null && <p className="knowledge-muted">正文原样保留，确认后可预览，再保存并启用。</p>}
          <button
            type="button"
            className="btn-primary"
            disabled={
              busy || (!importTarget && !newName.trim())
            }
            onClick={() => void create()}
          >
            {importBody === null ? "创建资料" : "导入到编辑器"}
          </button>
          {error && <p role="alert">{error}</p>}
        </div>
      </Modal>
      <Modal open={saved} onClose={() => setSaved(false)} title="已保存">
        <p>
          资料已保存
          {record?.status === "enabled"
            ? "并启用。新故事可使用这个版本。"
            : "。草稿不会影响正在创作的故事。"}
        </p>
        <button
          type="button"
          className="btn-primary mt-5"
          onClick={() => setSaved(false)}
        >
          知道了
        </button>
      </Modal>
    </Scene>
  );
}
