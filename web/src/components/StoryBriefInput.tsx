import { useEffect, useId, useRef, useState } from "react";
import { importDocument, type ImportedDocument } from "../api/documentImport";
import Icon from "./Icon";
import { textareaClass } from "./Modal";

const ACCEPT = ".txt,.md,.markdown,.doc,.docx,.pdf";

export default function StoryBriefInput({
  value,
  onChange,
  onPendingChange,
  disabled = false,
}: {
  value: string;
  onChange: (text: string) => void;
  onPendingChange: (pending: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [filename, setFilename] = useState("");
  const [preview, setPreview] = useState<ImportedDocument | null>(null);
  const [mode, setMode] = useState<"append" | "replace">("append");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    onPendingChange(busy || preview !== null);
  }, [busy, preview, onPendingChange]);
  useEffect(
    () => () => {
      controller.current?.abort();
      onPendingChange(false);
    },
    [onPendingChange],
  );

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setPreview(null);
    setError("");
  }

  async function load(file?: File) {
    if (!file || disabled) return;
    cancel();
    setNotice("");
    if (!/\.(txt|md|markdown|doc|docx|pdf)$/i.test(file.name)) {
      setError("请选择 TXT、MD、Word 或 PDF 文件。");
      return;
    }
    if (file.size > 10 * 1024 * 1024 || !file.size) {
      setError(
        file.size
          ? "文件不能超过 10 MiB，请拆分后导入。"
          : "文件为空，请重新选择。",
      );
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setFilename(file.name);
    setMode("append");
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      current.abort();
    }, 35000);
    try {
      const result = await importDocument(file, current.signal);
      if (controller.current === current && !current.signal.aborted)
        setPreview(result);
    } catch (e) {
      if (
        controller.current === current &&
        (!current.signal.aborted || timedOut)
      ) {
        setError(
          timedOut
            ? "解析等待超时，请稍后重试或将文件另存为 TXT/MD。"
            : e instanceof Error
              ? e.message
              : String(e),
        );
      }
    } finally {
      clearTimeout(timeout);
      if (controller.current === current) setBusy(false);
    }
  }

  function apply() {
    if (!preview || !preview.text.trim() || disabled) return;
    const text = preview.text.trim();
    onChange(
      mode === "append" && value.trim()
        ? `${value.trimEnd()}\n\n${text}`
        : text,
    );
    setNotice(`已导入「${preview.filename}」，可以继续编辑。`);
    setPreview(null);
  }

  return (
    <section className="mb-5 min-w-0" aria-label="故事构想文件导入">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-[14px] font-medium text-ink-muted">
          故事构想或已有框架
        </label>
        <button
          type="button"
          className="btn-secondary gap-2"
          disabled={disabled || busy}
          onClick={() => fileInput.current?.click()}
        >
          <Icon name="upload" />
          导入文件
        </button>
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          className="hidden"
          aria-label="选择故事构想文件"
          disabled={disabled || busy}
          onChange={(e) => {
            void load(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
      <textarea
        id={id}
        className={textareaClass}
        rows={5}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        placeholder="输入故事背景、人物设定或已有框架，也可导入文件。"
        required
      />
      <p className="mt-1 text-[12px] text-ink-muted">
        TXT、MD、Word（DOC/DOCX）、PDF · 单个文件不超过 10 MiB
      </p>
      {busy && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-violet/20 bg-violet-soft/50 p-4">
          <div role="status">
            <p className="text-[14px] font-semibold">正在提取文字…</p>
            <p className="mt-1 break-all text-[12px] text-ink-muted">
              {filename} · 本地解析，不调用模型
            </p>
          </div>
          <button type="button" className="btn-ghost" onClick={cancel}>
            取消导入
          </button>
        </div>
      )}
      {preview && (
        <div className="mt-3 rounded-2xl border border-violet/25 bg-violet-soft/40 p-4 sm:p-5">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="text-[16px] font-semibold">确认导入内容</h3>
              <p className="mt-1 break-all text-[13px] text-ink-muted">
                {preview.filename} · {preview.characters.toLocaleString()} 字符
                {preview.pages ? ` · ${preview.pages} 页` : ""}
              </p>
            </div>
            <button type="button" className="btn-ghost" onClick={cancel}>
              取消导入
            </button>
          </div>
          {preview.warnings.map((warning, i) => (
            <p key={i} className="mb-2 text-[13px] leading-6 text-ink-muted">
              {warning}
            </p>
          ))}
          <label htmlFor={`${id}-preview`} className="sr-only">
            导入文字预览
          </label>
          <textarea
            id={`${id}-preview`}
            className={`${textareaClass} !bg-white/80`}
            rows={8}
            value={preview.text}
            onChange={(e) => setPreview({ ...preview, text: e.target.value })}
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            {value.trim() ? (
              <fieldset
                className="flex flex-wrap gap-4 text-[14px]"
                aria-label="导入方式"
              >
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    checked={mode === "append"}
                    onChange={() => setMode("append")}
                  />
                  追加到现有内容
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    checked={mode === "replace"}
                    onChange={() => setMode("replace")}
                  />
                  替换现有内容
                </label>
              </fieldset>
            ) : (
              <span className="text-[13px] text-ink-muted">
                确认后填入构想，仍可继续编辑。
              </span>
            )}
            <button
              type="button"
              className="btn-primary"
              disabled={disabled || !preview.text.trim()}
              onClick={apply}
            >
              确认导入
            </button>
          </div>
        </div>
      )}
      {error && (
        <p
          role="alert"
          className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-[14px] text-rose-700"
        >
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-2 text-[13px] text-violet">
          {notice}
        </p>
      )}
    </section>
  );
}
