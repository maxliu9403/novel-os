import { useEffect, useRef, useState } from "react";
import { systemPromptsApi, type SystemPromptRecord } from "../api/systemPrompts";
import Modal from "./Modal";
import KnowledgeMarkdownPreview from "./KnowledgeMarkdownPreview";
import { useConfirm } from "./confirmContext";

export default function TitlePromptEditor({ onClose }: { onClose: () => void }) {
  const confirm = useConfirm();
  const [record, setRecord] = useState<SystemPromptRecord | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [tab, setTab] = useState<"preview" | "edit" | "versions">("preview");
  const [version, setVersion] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [saved, setSaved] = useState(false);
  const operation = useRef<{ markdown: string; revision: number; id: string } | null>(null);
  const dirty = Boolean(record && markdown !== record.current.markdown);
  const bytes = new TextEncoder().encode(markdown).length;
  const selected = record?.versions.find((item) => item.version === version);

  useEffect(() => {
    let active = true;
    systemPromptsApi.get().then((data) => {
      if (active) { setRecord(data); setMarkdown(data.current.markdown); }
    }).catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "读取失败"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);

  async function discard() {
    return !dirty || confirm({ title: "放弃未保存的修改？", message: "当前修改还未生效。", confirmLabel: "放弃修改" });
  }

  async function close() {
    if (!busy && await discard()) onClose();
  }

  async function reload() {
    if (!await discard()) return;
    setBusy(true); setError("");
    try { const data = await systemPromptsApi.get(); setRecord(data); setMarkdown(data.current.markdown); operation.current = null; }
    catch (err) { setError(err instanceof Error ? err.message : "读取失败"); }
    finally { setBusy(false); }
  }

  async function loadDraft(text: string) {
    if (!await discard()) return;
    setMarkdown(text); setTab("edit"); setError("");
  }

  async function save() {
    if (!record || !dirty || busy) return;
    setBusy(true); setError("");
    if (operation.current?.markdown !== markdown || operation.current.revision !== record.revision)
      operation.current = { markdown, revision: record.revision, id: crypto.randomUUID() };
    try {
      const data = await systemPromptsApi.save(markdown, record.revision, operation.current.id);
      setRecord(data); setMarkdown(data.current.markdown); setSaved(true); operation.current = null;
    } catch (err) { setError(err instanceof Error ? err.message : "保存失败"); }
    finally { setBusy(false); }
  }

  return <>
    <Modal open={!saved} wide title="书名设计提示词" onClose={() => void close()}>
      <div className="title-prompt-editor">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-paper-line pb-5">
          <p className="text-[14px] text-ink-muted">适用于所有作品 · 修改对下一次书名推荐生效{record && ` · v${record.current.version}`}</p>
          <button className="btn-ghost" disabled={busy} onClick={() => void close()}>关闭</button>
        </div>
        {error && <div role="alert" className="my-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-[14px] text-rose-700">{error}<button className="btn-secondary ml-3" disabled={busy} onClick={() => void reload()}>重新加载</button></div>}
        {!record ? <p role="status" className="py-12 text-center text-ink-muted">{busy ? "正在读取提示词…" : "提示词暂未加载"}</p> : <>
          <div role="tablist" aria-label="书名提示词视图" className="my-5 flex flex-wrap gap-2">
            {([["preview", "阅读预览"], ["edit", "编写 Markdown"], ["versions", "版本记录"]] as const).map(([id, label]) => <button type="button" key={id} role="tab" aria-selected={tab === id} className={`rounded-xl px-4 py-2.5 text-[15px] font-medium ${tab === id ? "bg-violet-soft text-violet" : "text-ink-muted"}`} onClick={() => setTab(id)}>{label}</button>)}
          </div>
          <div role="tabpanel" className="min-h-[320px]">
            {tab === "preview" && <KnowledgeMarkdownPreview markdown={markdown} />}
            {tab === "edit" && <label className="block"><span className="sr-only">书名设计 Markdown</span><textarea aria-label="书名设计 Markdown" value={markdown} disabled={busy} onChange={(e) => setMarkdown(e.target.value)} spellCheck={false} className="min-h-[390px] w-full resize-y rounded-2xl border border-paper-line bg-white/70 p-5 font-mono text-[15px] leading-[1.8] text-ink-text outline-none focus:border-violet" /><span className={`mt-2 block text-[13px] ${bytes > record.max_bytes ? "text-rose-700" : "text-ink-muted"}`}>{bytes.toLocaleString()} / {record.max_bytes.toLocaleString()} 字节 · 直接编写 Markdown，无需填写元数据</span></label>}
            {tab === "versions" && <div className="space-y-4">
              <div className="flex flex-wrap gap-2">{[...record.versions].reverse().map((item) => <button className={`rounded-xl border px-4 py-3 text-left text-[14px] ${version === item.version ? "border-violet bg-violet-soft" : "border-paper-line bg-white/70"}`} onClick={() => setVersion(item.version)} key={item.version} aria-pressed={version === item.version}>v{item.version}{item.version === record.current.version ? " · 当前使用" : ""}<span className="mt-1 block text-[12px] text-ink-muted">{item.created_at ? new Date(item.created_at).toLocaleString() : "内置版本"}</span></button>)}</div>
              {selected ? <div className="rounded-2xl border border-paper-line bg-white/65 p-5"><div className="mb-4 flex items-center justify-between"><strong>v{selected.version}</strong><button className="btn-secondary" disabled={busy || selected.markdown === markdown} onClick={() => void loadDraft(selected.markdown)}>载入此版本</button></div><KnowledgeMarkdownPreview markdown={selected.markdown} /></div> : <p className="py-12 text-center text-ink-muted">选择版本查看内容；载入后保存才会生效。</p>}
            </div>}
          </div>
          <footer className="sticky bottom-0 mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-paper-line bg-white/95 py-4">
            <button className="btn-ghost" disabled={busy || markdown === record.default_markdown} onClick={() => void loadDraft(record.default_markdown)}>载入内置提示词</button>
            {dirty ? <button className="btn-primary" disabled={busy || !markdown.trim() || bytes > record.max_bytes} onClick={() => void save()}>{busy ? "正在保存…" : "保存并生效"}</button> : <span className="text-[13px] text-ink-muted">当前版本已保存</span>}
          </footer>
        </>}
      </div>
    </Modal>
    <Modal open={saved} title="已保存" onClose={() => setSaved(false)}><p className="mb-6 text-[15px] leading-relaxed text-ink-muted">书名设计提示词 v{record?.current.version} 已生效。下一次推荐书名将使用此版本。</p><div className="flex justify-end gap-3"><button className="btn-secondary" onClick={() => setSaved(false)}>继续查看</button><button className="btn-primary" onClick={onClose}>完成</button></div></Modal>
  </>;
}
