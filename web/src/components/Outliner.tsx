import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type BinderNode, type ChapterSummary } from "../api/client";
import StatusPill from "./StatusPill";
import { useToast } from "./toastContext";

type MetricKey = "tension" | "emotional_intensity" | "pacing";
type Key = "number" | "title" | "status" | "word_count" | "pov" | MetricKey;

type RowMetrics = Partial<Record<MetricKey, number>>;

const COLUMNS: { key: Key; label: string; align?: "right"; title?: string }[] = [
  { key: "number", label: "#" },
  { key: "title", label: "标题" },
  { key: "status", label: "状态" },
  { key: "pov", label: "视角人物" },
  { key: "word_count", label: "字数", align: "right" },
  { key: "tension", label: "张力", align: "right", title: "风格策展 1–10 启发式评分" },
  { key: "emotional_intensity", label: "情绪", align: "right", title: "情绪强度 1–10" },
  { key: "pacing", label: "节奏", align: "right", title: "分数越高，节奏越快" },
];

function flattenChapters(nodes: BinderNode[]): BinderNode[] {
  const out: BinderNode[] = [];
  const walk = (list: BinderNode[]) => {
    for (const n of list) {
      if (n.type === "chapter" && n.chapter_number != null) out.push(n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

function ScoreCell({ value }: { value?: number }) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-paper-muted">—</span>;
  }
  const v = Math.max(1, Math.min(10, value));
  const hot = v >= 7;
  const mid = v >= 4;
  return (
    <span
      className={`nums inline-flex min-w-[1.75rem] justify-end rounded-md px-1.5 py-0.5 text-[12.5px] font-semibold ${
        hot
          ? "bg-[rgba(104,103,234,0.16)] text-[var(--color-violet)]"
          : mid
            ? "bg-white/70 text-ink-text"
            : "bg-white/40 text-ink-muted"
      }`}
    >
      {v}
    </span>
  );
}

export default function Outliner({ id, chapters }: { id: string; chapters: ChapterSummary[] }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "number", dir: 1 });
  const [synopses, setSynopses] = useState<Record<number, { nodeId: string; text: string }>>({});
  const [metrics, setMetrics] = useState<Record<number, RowMetrics>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const reloadBinder = useCallback(() => {
    api.binder(id).then((tree) => {
      const map: Record<number, { nodeId: string; text: string }> = {};
      const m: Record<number, RowMetrics> = {};
      for (const n of flattenChapters(tree)) {
        map[n.chapter_number!] = { nodeId: n.id, text: n.synopsis || "" };
        const d = n.derived || {};
        if (
          d.tension != null
          || d.emotional_intensity != null
          || d.pacing != null
        ) {
          m[n.chapter_number!] = {
            tension: typeof d.tension === "number" ? d.tension : undefined,
            emotional_intensity:
              typeof d.emotional_intensity === "number" ? d.emotional_intensity : undefined,
            pacing: typeof d.pacing === "number" ? d.pacing : undefined,
          };
        }
      }
      setSynopses(map);
      setMetrics(m);
    }).catch(() => {
      setSynopses({});
      setMetrics({});
    });
  }, [id]);

  useEffect(reloadBinder, [reloadBinder]);

  const rows = useMemo(() => {
    return [...chapters].sort((a, b) => {
      const metricKeys: MetricKey[] = ["tension", "emotional_intensity", "pacing"];
      if (metricKeys.includes(sort.key as MetricKey)) {
        const av = metrics[a.number]?.[sort.key as MetricKey] ?? -1;
        const bv = metrics[b.number]?.[sort.key as MetricKey] ?? -1;
        if (av < bv) return -1 * sort.dir;
        if (av > bv) return 1 * sort.dir;
        return 0;
      }
      const av = a[sort.key as keyof ChapterSummary];
      const bv = b[sort.key as keyof ChapterSummary];
      if ((av as string | number) < (bv as string | number)) return -1 * sort.dir;
      if ((av as string | number) > (bv as string | number)) return 1 * sort.dir;
      return 0;
    });
  }, [chapters, sort, metrics]);

  const toggle = (key: Key) =>
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: 1 }));

  async function saveSynopsis(chapterNumber: number) {
    const slot = synopses[chapterNumber];
    if (!slot) return;
    setBusy(chapterNumber);
    try {
      await api.patchBinderNode(id, slot.nodeId, { synopsis: slot.text });
      toast("章节梗概已保存", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  async function refreshMetrics() {
    setRefreshing(true);
    try {
      const result = await api.refreshOutlinerMetrics(id);
      const next: Record<number, RowMetrics> = {};
      for (const row of result.chapters) {
        next[row.chapter] = {
          tension: row.tension,
          emotional_intensity: row.emotional_intensity,
          pacing: row.pacing,
        };
      }
      setMetrics(next);
      toast(
        result.chapters.length
          ? `已完成 ${result.chapters.length} 章评分`
          : "没有可评分的章节",
        "success",
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12.5px] text-ink-muted">
          大纲表 · 点击表头排序 · 张力、情绪与节奏由风格策展启发式评估
        </p>
        <button
          type="button"
          disabled={refreshing || chapters.length === 0}
          onClick={() => void refreshMetrics()}
          className="rounded-full border border-[rgba(96,112,153,0.16)] bg-white/70 px-3 py-1.5 text-[12px] font-medium text-ink-muted transition-colors hover:text-[var(--color-violet)] disabled:opacity-40"
        >
          {refreshing ? "正在评分…" : "刷新指标"}
        </button>
      </div>
      <div className="overflow-x-auto overflow-hidden rounded-2xl border border-paper-line bg-paper-card shadow-[var(--shadow-paper)]">
        <table className="w-full min-w-[820px] border-collapse text-left">
          <thead>
            <tr className="border-b border-paper-line">
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  onClick={() => toggle(c.key)}
                  className={`cursor-pointer select-none px-3 py-3 text-[12px] font-medium tracking-[-0.01em] text-ink-muted transition-colors hover:text-ink-text sm:px-4 ${
                    c.align === "right" ? "text-right" : ""
                  }`}
                >
                  {c.label}
                  {sort.key === c.key && <span className="ml-1 text-ink">{sort.dir === 1 ? "↑" : "↓"}</span>}
                </th>
              ))}
              <th className="px-4 py-3 text-[12px] font-medium tracking-[-0.01em] text-ink-muted">
                章节梗概
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const m = metrics[c.number] || {};
              return (
                <tr
                  key={c.number}
                  className="border-b border-paper-line/60 transition-colors last:border-0 hover:bg-ink/[0.03]"
                >
                  <td
                    className="nums cursor-pointer px-3 py-3 font-mono text-[12.5px] text-paper-muted sm:px-4"
                    onClick={() => navigate(`/projects/${id}/chapters/${c.number}`)}
                  >
                    {c.number}
                  </td>
                  <td
                    className="cursor-pointer px-3 py-3 font-display text-[15px] text-ink-text sm:px-4"
                    onClick={() => navigate(`/projects/${id}/chapters/${c.number}`)}
                  >
                    {c.title || "未命名"}
                  </td>
                  <td className="px-3 py-3 sm:px-4"><StatusPill status={c.status} /></td>
                  <td className="px-3 py-3 text-[13px] text-ink-muted sm:px-4">{c.pov || "—"}</td>
                  <td className="nums px-3 py-3 text-right text-[13px] text-ink-muted sm:px-4">
                    {c.word_count.toLocaleString("zh-CN")}
                  </td>
                  <td className="px-3 py-3 text-right sm:px-4"><ScoreCell value={m.tension} /></td>
                  <td className="px-3 py-3 text-right sm:px-4">
                    <ScoreCell value={m.emotional_intensity} />
                  </td>
                  <td className="px-3 py-3 text-right sm:px-4"><ScoreCell value={m.pacing} /></td>
                  <td className="min-w-[200px] px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <textarea
                      rows={2}
                      disabled={busy === c.number || !synopses[c.number]}
                      value={synopses[c.number]?.text ?? ""}
                      onChange={(e) =>
                        setSynopses((s) => ({
                          ...s,
                          [c.number]: {
                            nodeId: s[c.number]?.nodeId || "",
                            text: e.target.value,
                          },
                        }))
                      }
                      onBlur={() => void saveSynopsis(c.number)}
                      placeholder="章节梗概…"
                      className="w-full resize-y rounded-lg border border-[rgba(96,112,153,0.14)] bg-white/70 px-2.5 py-1.5 text-[12.5px] leading-relaxed text-ink-text placeholder:text-paper-muted focus:outline-none"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
