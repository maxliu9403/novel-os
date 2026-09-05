import { useCallback, useEffect, useState } from "react";
import {
  api, type CompileFormat, type CompileStyle, type StyleSheet,
} from "../api/client";
import { useToast } from "./toastContext";
import Icon from "./Icon";
import Select from "./Select";

/**
 * Compile the book through its named styles (PLAN.md P5.2 / P6).
 *
 * Only the handful of styles a writer actually changes are exposed. The full
 * sheet exists in the API, but a panel with seven roles times eleven properties
 * is a settings screen, and settings screens are how Scrivener earned its
 * reputation for needing tutorials.
 *
 * Nothing here can alter a word: a style names an appearance, and appearance is
 * not story truth.
 */
const EDITABLE: { role: string; label: string; hint: string }[] = [
  { role: "chapter_title", label: "章节标题", hint: "每章开头的标题" },
  { role: "body", label: "正文", hint: "普通正文段落" },
  { role: "block_quote", label: "引用块", hint: "书信与题词" },
];

const FONTS = [
  { value: "serif", label: "衬线体" },
  { value: "sans", label: "无衬线体" },
  { value: "mono", label: "等宽体" },
];

const ALIGNMENTS = [
  { value: "left", label: "左对齐" },
  { value: "center", label: "居中" },
  { value: "justify", label: "两端对齐" },
];

export default function CompilePanel({ projectId }: { projectId: string }) {
  const toast = useToast();
  const [sheet, setSheet] = useState<StyleSheet | null>(null);
  const [busy, setBusy] = useState(false);
  const [format, setFormat] = useState<CompileFormat>("html");

  const load = useCallback(() => {
    api.styles(projectId).then(setSheet).catch(() => setSheet(null));
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  if (!sheet) return null;

  function patch(role: string, change: Partial<CompileStyle>) {
    setSheet((s) =>
      s ? { ...s, styles: { ...s.styles, [role]: { ...s.styles[role], ...change } } } : s,
    );
  }

  async function save() {
    if (!sheet) return;
    setBusy(true);
    try {
      // The API validates the whole sheet and rejects it entire, so the answer
      // it sends back is the truth about what is stored.
      setSheet(await api.saveStyles(projectId, sheet));
      toast("样式已保存", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="编译导出"
      className="mb-6 rounded-[24px] border border-[rgba(74,91,133,0.12)] bg-white/55 px-5 py-5 backdrop-blur-md"
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-[18px] font-semibold tracking-tight text-ink-text">
            编译导出
          </h2>
          <p className="mt-0.5 text-[12.5px] text-ink-muted">
            使用命名样式控制导出 · 修改一次，全书同步
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            label="格式"
            size="sm"
            value={format}
            onChange={(v) => setFormat(v as CompileFormat)}
            options={[
              { value: "docx", label: "Word (.docx)" },
              { value: "epub", label: "EPUB" },
              { value: "pdf", label: "PDF (.pdf)" },
              { value: "html", label: "HTML" },
              { value: "markdown", label: "Markdown" },
            ]}
          />
          <a
            href={api.compileUrl(projectId, format)}
            download
            className="btn-primary inline-flex items-center gap-1.5"
          >
            <Icon name="download" className="h-3.5 w-3.5" /> 编译导出
          </a>
        </div>
      </div>

      <div className="space-y-3">
        {EDITABLE.map(({ role, label, hint }) => {
          const style = sheet.styles[role];
          if (!style) return null;
          return (
            <div
              key={role}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-[rgba(74,91,133,0.12)] bg-white/70 px-3 py-2"
            >
              <div className="min-w-[9rem] flex-1">
                <p className="text-[13px] font-medium text-ink-text">{label}</p>
                <p className="text-[11.5px] text-ink-muted">{hint}</p>
              </div>

              <label className="flex items-center gap-1.5 text-[11.5px] text-ink-muted">
                字号
                <input
                  type="number"
                  min={4}
                  max={96}
                  step={0.5}
                  value={style.size_pt}
                  aria-label={`${label}字号（磅）`}
                  onChange={(e) => patch(role, { size_pt: Number(e.target.value) })}
                  className="w-16 rounded-lg border border-[rgba(96,112,153,0.2)] bg-white/80 px-2 py-1 text-[12px] text-ink-text"
                />
              </label>

              <Select
                label={`${label}字体`}
                size="sm"
                value={style.font}
                onChange={(v) => patch(role, { font: v })}
                options={FONTS}
              />
              <Select
                label={`${label}对齐方式`}
                size="sm"
                value={style.align}
                onChange={(v) => patch(role, { align: v })}
                options={ALIGNMENTS}
              />
            </div>
          );
        })}

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <label className="flex items-center gap-2 text-[12px] text-ink-muted">
            场景分隔符
            <input
              value={sheet.scene_break_marker}
              aria-label="场景分隔标记"
              onChange={(e) =>
                setSheet((s) => (s ? { ...s, scene_break_marker: e.target.value } : s))
              }
              className="w-28 rounded-lg border border-[rgba(96,112,153,0.2)] bg-white/80 px-2 py-1 text-[12px] text-ink-text"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void save()}
            className="btn-secondary disabled:opacity-40"
          >
            {busy ? "正在保存…" : "保存样式"}
          </button>
        </div>
      </div>
    </section>
  );
}
