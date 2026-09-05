import { useCallback, useEffect, useState } from "react";
import {
  api, type SnapshotMeta, type SnapshotText, type CommentItem, type ContinuityFinding,
} from "../api/client";
import { useToast } from "./toastContext";
import { useConfirm } from "./confirmContext";
import DiffView from "./DiffView";
import Icon, { type IconName } from "./Icon";
import { displayLabel } from "../lib/displayLabels";

function when(iso: string) {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export type Tab = "versions" | "comments" | "continuity";

export default function Inspector({
  id, num, currentText, flush, onRestored, pendingComment, onPendingCommentConsumed, onClose,
  onCommentsChange, onJumpToComment, requestedTab,
}: {
  id: string; num: number; currentText: string;
  flush: () => Promise<void>; onRestored: (finalText?: string) => void;
  pendingComment?: { from: number; to: number; quote: string } | null;
  onPendingCommentConsumed?: () => void;
  onClose?: () => void;
  onCommentsChange?: (comments: CommentItem[]) => void;
  onJumpToComment?: (c: CommentItem) => void;
  /** Studio mode asks for a tab; the writer can still switch away afterwards. */
  requestedTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(
    pendingComment ? "comments" : requestedTab ?? "versions",
  );

  // Honour a new request from the mode switch, but only when it changes - this
  // is a suggestion about where to start, not a lock on the tab.
  const [lastRequested, setLastRequested] = useState(requestedTab);
  if (requestedTab !== lastRequested) {
    setLastRequested(requestedTab);
    if (requestedTab) setTab(requestedTab);
  }
  // A new pending comment pulls the rail to the Comments tab. Adjusted during
  // render so the tab is already correct on the frame the selection lands.
  const [lastPending, setLastPending] = useState(pendingComment);
  if (pendingComment !== lastPending) {
    setLastPending(pendingComment);
    if (pendingComment) setTab("comments");
  }

  const tabs: { id: Tab; label: string; icon: IconName }[] = [
    { id: "versions", label: "版本快照", icon: "history" },
    { id: "comments", label: "批注", icon: "message-square" },
    { id: "continuity", label: "连续性", icon: "shield-alert" },
  ];

  return (
    <aside className="glass-rail flex min-h-full w-[340px] max-w-full flex-1 flex-col overflow-y-auto border-l-0">
      <div className="flex items-center gap-1.5 border-b border-[rgba(74,91,133,0.12)] px-2.5 py-2.5">
        <div
          role="tablist"
          aria-label="批注面板"
          className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto overflow-y-hidden rounded-full border border-[rgba(96,112,153,0.16)] bg-white/55 p-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
        >
          {tabs.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                title={t.label}
                onClick={() => setTab(t.id)}
                className={`inline-flex shrink-0 items-center justify-center gap-1 rounded-full px-2.5 py-1.5 text-[11px] font-medium transition-colors ${
                  active
                    ? "bg-[var(--color-violet)] text-white shadow-[0_6px_16px_rgba(104,103,234,0.28)]"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                <Icon name={t.icon} className="h-3 w-3 shrink-0" />
                <span className="whitespace-nowrap">{t.label}</span>
              </button>
            );
          })}
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-white/70 hover:text-ink"
            aria-label="关闭批注面板"
            title="关闭批注面板"
          >
            <Icon name="chevron-right" className="h-4 w-4" />
          </button>
        )}
      </div>
      {tab === "versions" && (
        <Snapshots id={id} num={num} currentText={currentText} flush={flush} onRestored={onRestored} />
      )}
      {tab === "comments" && (
        <Comments
          id={id}
          num={num}
          pendingComment={pendingComment}
          onPendingCommentConsumed={onPendingCommentConsumed}
          onCommentsChange={onCommentsChange}
          onJumpToComment={onJumpToComment}
        />
      )}
      {tab === "continuity" && <ContinuityPanel id={id} num={num} />}
    </aside>
  );
}

function Snapshots({ id, num, currentText, flush, onRestored }: {
  id: string; num: number; currentText: string;
  flush: () => Promise<void>; onRestored: (finalText?: string) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [list, setList] = useState<SnapshotMeta[]>([]);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [viewing, setViewing] = useState<SnapshotText | null>(null);

  const reload = useCallback(() => {
    api.snapshots(id, num).then(setList).catch(() => setList([]));
  }, [id, num]);
  useEffect(reload, [reload]);

  async function saveVersion() {
    setBusy(true);
    try {
      await flush(); // persist the current buffer so the snapshot reflects it
      await api.createSnapshot(id, num, label.trim() || "版本");
      setLabel("");
      toast("版本已保存", "success");
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  async function view(sid: string) {
    if (viewing?.id === sid) { setViewing(null); return; }
    try { setViewing(await api.getSnapshot(id, num, sid)); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  }

  async function restore(sid: string) {
    try {
      const r = await api.restoreSnapshot(id, num, sid);
      toast("已恢复该版本，恢复前的定稿已保存为快照", "success");
      setViewing(null);
      onRestored(r.final);
      reload();
    } catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  }

  async function remove(sid: string) {
    const ok = await confirm({
      title: "删除版本",
      message: "此操作将永久删除该版本快照，且无法撤销。",
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    try { await api.deleteSnapshot(id, num, sid); reload(); if (viewing?.id === sid) setViewing(null); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  }

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <div className="flex gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="版本名称（可选）…"
          className="min-w-0 flex-1 rounded-lg border border-paper-line bg-paper px-3 py-1.5 text-[13px] text-ink-text placeholder:text-paper-muted"
        />
        <button onClick={saveVersion} disabled={busy}
                className="btn-primary shrink-0 disabled:opacity-40">
          {busy ? "正在保存…" : "保存版本"}
        </button>
      </div>

      {list.length === 0 && <p className="py-6 text-center text-[13px] text-ink-muted">暂无版本快照。</p>}

      {list.map((s) => (
        <div key={s.id} className="rounded-lg border border-paper-line bg-paper-card p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[13.5px] font-semibold text-ink-text">{s.label}</p>
              <p className="nums text-[11.5px] text-ink-muted">{when(s.created_at)} · {s.word_count.toLocaleString("zh-CN")} 字</p>
            </div>
          </div>
          <div className="mt-2 flex gap-3 text-[12px] font-medium">
            <button onClick={() => view(s.id)} className="text-st-drafted hover:underline">
              {viewing?.id === s.id ? "隐藏差异" : "查看差异"}
            </button>
            <button onClick={() => restore(s.id)} className="text-ink-muted hover:underline">恢复</button>
            <button onClick={() => remove(s.id)} className="text-ink-muted hover:text-ink">删除</button>
          </div>
          {viewing?.id === s.id && (
            <div className="mt-3 max-h-72 overflow-y-auto rounded-md border border-paper-line bg-paper p-3">
              <p className="mb-2 text-[12px] font-medium tracking-[-0.01em] text-paper-muted">
                快照 → 当前内容
              </p>
              <DiffView oldText={viewing.text} newText={currentText} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Comments({ id, num, pendingComment, onPendingCommentConsumed, onCommentsChange, onJumpToComment }: {
  id: string; num: number;
  pendingComment?: { from: number; to: number; quote: string } | null;
  onPendingCommentConsumed?: () => void;
  onCommentsChange?: (comments: CommentItem[]) => void;
  onJumpToComment?: (c: CommentItem) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [list, setList] = useState<CommentItem[]>([]);
  const [body, setBody] = useState("");
  const [quote, setQuote] = useState("");
  const [fromPos, setFromPos] = useState<number | null>(null);
  const [toPos, setToPos] = useState<number | null>(null);
  const [persona, setPersona] = useState<"author" | "editor" | "beta">(() => {
    const raw = localStorage.getItem("novelos-comment-persona");
    return raw === "editor" || raw === "beta" ? raw : "author";
  });

  const reload = useCallback(() => {
    api.comments(id, num).then((items) => {
      setList(items);
      onCommentsChange?.(items);
    }).catch(() => setList([]));
  }, [id, num, onCommentsChange]);
  useEffect(reload, [reload]);

  // Seed the draft note from the editor selection during render; only the
  // parent notification stays in an effect, since telling another component to
  // update mid-render is illegal.
  const [lastPending, setLastPending] = useState(pendingComment);
  if (pendingComment !== lastPending) {
    setLastPending(pendingComment);
    if (pendingComment) {
      setQuote(pendingComment.quote);
      setFromPos(pendingComment.from);
      setToPos(pendingComment.to);
    }
  }

  useEffect(() => {
    if (pendingComment) onPendingCommentConsumed?.();
  }, [pendingComment, onPendingCommentConsumed]);

  async function removeComment(cid: string) {
    const ok = await confirm({
      title: "删除批注", message: "确定删除这条批注吗？", confirmLabel: "删除", danger: true,
    });
    if (ok) api.deleteComment(id, num, cid).then(reload).catch(() => {});
  }

  async function add() {
    if (!body.trim()) return;
    try {
      await api.addComment(id, num, body, quote, fromPos, toPos, persona);
      setBody(""); setQuote(""); setFromPos(null); setToPos(null);
      reload();
    } catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  }

  const personas: { id: "author" | "editor" | "beta"; label: string }[] = [
    { id: "author", label: "作者" },
    { id: "editor", label: "编辑" },
    { id: "beta", label: "试读者" },
  ];

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <div className="rounded-2xl border border-[rgba(74,91,133,0.12)] bg-white/70 p-3">
        <div className="mb-2 flex overflow-hidden rounded-full border border-[rgba(96,112,153,0.16)] bg-white/80 p-0.5">
          {personas.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                setPersona(p.id);
                localStorage.setItem("novelos-comment-persona", p.id);
              }}
              className={`flex-1 rounded-full px-2 py-1 text-[11px] font-medium transition-colors ${
                persona === p.id
                  ? "bg-[var(--color-violet)] text-white"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <input value={quote} onChange={(e) => setQuote(e.target.value)}
               placeholder="引用文字（可选）…"
               className="mb-2 w-full rounded-xl border border-[rgba(96,112,153,0.16)] bg-white/80 px-2.5 py-1.5 text-[12.5px] text-ink-text placeholder:text-paper-muted" />
        {fromPos != null && toPos != null && (
          <p className="mb-2 text-[11px] text-ink-muted">已锚定 · 字符 {fromPos}–{toPos}</p>
        )}
        <textarea value={body} onChange={(e) => setBody(e.target.value)}
                  placeholder={`添加${displayLabel(persona)}批注…`} rows={2}
                  className="w-full resize-y rounded-xl border border-[rgba(96,112,153,0.16)] bg-white/80 px-3 py-2.5 text-[13px] leading-relaxed text-ink-text placeholder:text-paper-muted" />
        <div className="mt-2 flex justify-end">
          <button type="button" onClick={add} disabled={!body.trim()}
                  className="btn-primary disabled:opacity-40">
            添加批注
          </button>
        </div>
      </div>

      {list.length === 0 && <p className="py-6 text-center text-[13px] text-ink-muted">暂无批注。</p>}

      {list.map((c) => (
        <div key={c.id} className={`rounded-2xl border border-[rgba(74,91,133,0.12)] p-3 ${c.resolved ? "bg-white/40 opacity-70" : "bg-white/70"}`}>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-violet)]">
            {displayLabel(c.persona || "author")}
          </p>
          {c.quote && (
            <blockquote className="mb-1.5 border-l-2 border-[var(--color-violet)] pl-2 text-[12px] italic text-ink-muted">
              “{c.quote}”
            </blockquote>
          )}
          {c.anchor_status === "unresolved" && (
            <p className="mb-1 text-[11px] font-medium text-[#c85177]">无法定位原文锚点</p>
          )}
          <p className={`text-[13.5px] text-ink-text ${c.resolved ? "line-through" : ""}`}>{c.body}</p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[11.5px]">
            <span className="nums text-paper-muted">{when(c.created_at)}</span>
            {c.from_pos != null && c.to_pos != null && c.anchor_status !== "unresolved" && (
              <button type="button" onClick={() => onJumpToComment?.(c)}
                      className="font-medium text-[var(--color-violet)] hover:underline">
                显示原文
              </button>
            )}
            <button type="button" onClick={() => api.updateComment(id, num, c.id, !c.resolved).then(reload)}
                    className="font-medium text-st-approved hover:underline">
              {c.resolved ? "重新打开" : "标为已解决"}
            </button>
            <button type="button" onClick={() => removeComment(c.id)}
                    className="font-medium text-ink-muted hover:text-ink">删除</button>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * "This is intentional" (PLAN.md P2.1).
 *
 * A checker cannot tell an unreliable narrator or a character who lies from a
 * real mistake, so the writer needs a third answer besides fixing and ignoring.
 * The reason is required in spirit but not enforced: asking for it is what makes
 * the exemption reviewable six months later, but blocking on it would just
 * teach people to type "x".
 */
function IntentionalButton({
  projectId, finding, onExempted,
}: {
  projectId: string;
  finding: ContinuityFinding;
  onExempted: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      await api.exemptFinding(projectId, finding.key, reason);
      toast("已标记为有意为之，此问题不会再次提示", "success");
      setOpen(false);
      setReason("");
      onExempted();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn-ghost mt-2 px-2 py-0.5 text-[11.5px]"
        onClick={() => setOpen(true)}
      >
        这是有意为之
      </button>
    );
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <input
        autoFocus
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="请说明原因，例如：她在这里说了谎"
        aria-label="为什么这是有意为之？"
        className="min-w-0 flex-1 rounded-lg border border-[rgba(96,112,153,0.2)] bg-white/80 px-2 py-1 text-[12px] text-ink-text"
      />
      <button type="button" className="btn-ghost px-2 py-0.5 text-[11.5px]"
              onClick={() => setOpen(false)}>
        取消
      </button>
      <button type="button" disabled={busy}
              className="btn-secondary px-2 py-0.5 text-[11.5px] disabled:opacity-40"
              onClick={() => void submit()}>
        {busy ? "正在保存…" : "忽略问题"}
      </button>
    </div>
  );
}

function ContinuityPanel({ id, num }: { id: string; num: number }) {
  const [findings, setFindings] = useState<ContinuityFinding[] | null>(null);
  const [counts, setCounts] = useState({ critical: 0, warning: 0, info: 0 });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.chapterContinuity(id, num)
      .then((r) => {
        setFindings(r.findings);
        setCounts({ critical: r.critical, warning: r.warning, info: r.info });
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, [id, num]);

  useEffect(() => { load(); }, [load]);

  const iconFor = (sev: string): IconName => {
    if (sev === "critical") return "circle-alert";
    if (sev === "warning") return "triangle-alert";
    return "circle-check";
  };

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] text-ink-muted">
          确定性检查免费且即时完成。
        </p>
        <button type="button" onClick={load} className="btn-ghost text-[12px]">刷新</button>
      </div>
      {findings && (
        <div className="flex gap-2 text-[11px]">
          <span className="rounded-full bg-[#ffeaf1] px-2 py-0.5 text-[#c85177]">{counts.critical} 个严重问题</span>
          <span className="rounded-full bg-[#fff2dc] px-2 py-0.5 text-[#c47a1b]">{counts.warning} 个警告</span>
          <span className="rounded-full bg-[#e8f1ff] px-2 py-0.5 text-[#3974db]">{counts.info} 条提示</span>
        </div>
      )}
      {error && <p className="text-[13px] text-[#c85177]">{error}</p>}
      {!error && !findings && <p className="py-6 text-center text-[13px] text-ink-muted">正在检查…</p>}
      {findings && findings.length === 0 && (
        <p className="py-8 text-center text-[13px] text-ink-muted">未发现连续性问题。</p>
      )}
      {findings?.map((f, i) => (
        <div key={`${f.category}-${i}`} className="rounded-2xl border border-[rgba(74,91,133,0.12)] bg-white/70 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium capitalize text-ink-muted">
            <Icon name={iconFor(f.severity)} className="h-3.5 w-3.5" />
            {displayLabel(f.severity)} · {displayLabel(f.category)}
            {f.chapter != null && <> · 第 {f.chapter} 章</>}
          </div>
          <p className="text-[13px] text-ink-text">{f.message}</p>
          {f.suggestion && (
            <p className="mt-1.5 text-[12px] text-ink-muted">{f.suggestion}</p>
          )}
          {f.key && (
            <IntentionalButton
              projectId={id}
              finding={f}
              onExempted={load}
            />
          )}
        </div>
      ))}
    </div>
  );
}
