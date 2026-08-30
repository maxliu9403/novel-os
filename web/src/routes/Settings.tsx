import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import {
  api,
  type StudioCoverStatus,
  type StudioLlmStatus,
  type StudioPreset,
} from "../api/client";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import { useToast } from "../components/toastContext";
import { Field, fieldClass } from "../components/Modal";

export default function Settings() {
  const toast = useToast();
  const [status, setStatus] = useState<StudioLlmStatus | null>(null);
  const [coverStatus, setCoverStatus] = useState<StudioCoverStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverApiKey, setCoverApiKey] = useState("");
  const [coverModel, setCoverModel] = useState("gpt-image-2");
  const [coverSize, setCoverSize] = useState("2048x3072");
  const [coverBaseUrl, setCoverBaseUrl] = useState("");
  const [coverCount, setCoverCount] = useState(4);
  const [coverTimeout, setCoverTimeout] = useState(180);
  const [coverQuality, setCoverQuality] = useState<StudioCoverStatus["quality"]>("high");
  const [coverFormat, setCoverFormat] = useState<StudioCoverStatus["output_format"]>("jpeg");

  function load() {
    api.studioLlm().then((s) => {
      setStatus(s);
      setModel(s.model || "");
    }).catch((e) => setError(String(e)));
    api.studioCover().then((s) => {
      setCoverStatus(s);
      setCoverModel(s.model);
      setCoverSize(s.size);
      setCoverBaseUrl(s.base_url);
      setCoverCount(s.count);
      setCoverTimeout(s.timeout_seconds);
      setCoverQuality(s.quality);
      setCoverFormat(s.output_format);
    }).catch((e) => setError(String(e)));
  }

  useEffect(() => { load(); }, []);

  async function applyPreset(p: StudioPreset) {
    setBusy(true);
    try {
      const next = await api.updateStudioLlm({
        preset: p.id,
        model: model.trim() || undefined,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      });
      setStatus(next);
      setModel(next.model || "");
      setApiKey("");
      toast(`Preset: ${p.label}`, "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveCustom() {
    setBusy(true);
    try {
      const next = await api.updateStudioLlm({
        provider: status?.preset === "local" ? "ollama" : undefined,
        model: model.trim() || undefined,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      });
      setStatus(next);
      setApiKey("");
      toast("Settings saved", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveCover() {
    setCoverBusy(true);
    try {
      const next = await api.updateStudioCover({
        model: coverModel.trim(),
        base_url: coverBaseUrl.trim(),
        api_key: coverApiKey.trim() || undefined,
        size: coverSize.trim(),
        quality: coverQuality,
        output_format: coverFormat,
        count: coverCount,
        timeout_seconds: coverTimeout,
      });
      setCoverStatus(next);
      setCoverApiKey("");
      toast("Cover settings saved", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setCoverBusy(false);
    }
  }

  return (
    <Scene>
      <div className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
        <motion.div
          initial={{ opacity: 0, y: 16, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
          className="glass-shell p-3 sm:p-4"
        >
          <div className="glass-panel px-6 py-8 sm:px-10 sm:py-10">
            <Link to="/" className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-ink-muted hover:text-[var(--color-violet)]">
              <Icon name="arrow-left" className="h-3.5 w-3.5" /> Library
            </Link>
            <p className="eyebrow">Studio</p>
            <h1 className="font-display text-[32px] font-semibold tracking-[-0.035em] text-ink-text">
              Settings
            </h1>
            <p className="mt-2 max-w-xl text-[14px] text-ink-muted">
              Choose how agents write. Novel OS does not host NSFW models use Local or Mature-capable (BYOK) for uncensored fiction.
            </p>

            {error && (
              <div className="mt-6 rounded-2xl border border-[rgba(200,80,100,0.3)] bg-[#fff5f7] px-4 py-3 text-[13px]">
                {error}
              </div>
            )}

            {status && (
              <>
                <div className={`mt-8 rounded-2xl border px-4 py-3 text-[13px] ${
                  status.configured
                    ? "border-[rgba(104,103,234,0.25)] bg-[rgba(238,237,255,0.6)] text-ink-text"
                    : "border-[rgba(200,122,27,0.3)] bg-[#fff8ee] text-ink-text"
                }`}>
                  <div className="flex items-center gap-2 font-medium">
                    <Icon name={status.configured ? "circle-check" : "triangle-alert"} className="h-4 w-4" />
                    {status.configured ? "LLM ready" : "LLM not configured"}
                  </div>
                  <p className="mt-1 text-ink-muted">
                    {status.configured
                      ? `${status.provider} · ${status.model}`
                      : (status.error || "Add a key or pick Local (Ollama).")}
                  </p>
                </div>

                <h2 className="mt-10 mb-3 font-display text-[18px] font-semibold text-ink-text">Presets</h2>
                <div className="grid gap-3 sm:grid-cols-2">
                  {status.presets.map((p) => {
                    const active = status.preset === p.id;
                    return (
                      <button
                        key={p.id}
                        type="button"
                        disabled={busy}
                        onClick={() => applyPreset(p)}
                        className={`rounded-2xl border p-4 text-left transition-all ${
                          active
                            ? "border-[rgba(104,103,234,0.45)] bg-[rgba(238,237,255,0.85)] shadow-[0_8px_24px_rgba(104,103,234,0.12)]"
                            : "border-[rgba(74,91,133,0.12)] bg-white/60 hover:border-[rgba(104,103,234,0.28)]"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[15px] font-semibold text-ink-text">{p.label}</span>
                          {p.mature_capable && (
                            <span className="rounded-full bg-[#ffeaf1] px-2 py-0.5 text-[10px] font-medium text-[#c85177]">
                              Mature-capable
                            </span>
                          )}
                        </div>
                        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-muted">{p.hint}</p>
                        <p className="mt-2 text-[11px] text-paper-muted">{p.provider} · {p.model}</p>
                      </button>
                    );
                  })}
                </div>

                <h2 className="mt-10 mb-3 font-display text-[18px] font-semibold text-ink-text">Credentials</h2>
                <Field label="Model id">
                  <input className={fieldClass} value={model} onChange={(e) => setModel(e.target.value)}
                         placeholder="e.g. claude-sonnet-4-6 or llama3.2" />
                </Field>
                <Field label="Api key (optional leave blank to keep existing)">
                  <input className={fieldClass} type="password" value={apiKey}
                         onChange={(e) => setApiKey(e.target.value)}
                         placeholder="sk-… / OpenRouter key" autoComplete="off" />
                </Field>
                <Field label="Base url (local / custom)">
                  <input className={fieldClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                         placeholder="http://localhost:11434/v1" />
                </Field>
                <button type="button" disabled={busy} onClick={saveCustom} className="btn-primary mt-2 disabled:opacity-40">
                  {busy ? "Saving…" : "Save credentials"}
                </button>
              </>
            )}

            {coverStatus && (
              <section className="mt-12 border-t border-[rgba(74,91,133,0.12)] pt-10">
                <p className="eyebrow">Images</p>
                <h2 className="font-display text-[24px] font-semibold text-ink-text">
                  Cover generation
                </h2>

                <div className={`mt-5 border-l-2 px-4 py-2.5 text-[13px] ${
                  coverStatus.configured
                    ? "border-[var(--color-violet)] text-ink-text"
                    : "border-[#c87a1b] text-ink-text"
                }`}>
                  <div className="flex items-center gap-2 font-medium">
                    <Icon name={coverStatus.configured ? "circle-check" : "triangle-alert"} className="h-4 w-4" />
                    {coverStatus.configured ? "Cover model ready" : "Cover model not configured"}
                  </div>
                  <p className="mt-1 text-ink-muted">
                    {coverStatus.model} · {coverStatus.size} · {coverStatus.quality}
                  </p>
                  {coverStatus.error && !coverStatus.configured ? (
                    <p className="mt-1 text-ink-muted">{coverStatus.error}</p>
                  ) : null}
                </div>

                <div className="mt-7 grid gap-x-4 sm:grid-cols-2">
                  <Field label="Cover model">
                    <input
                      className={fieldClass}
                      value={coverModel}
                      readOnly
                    />
                  </Field>
                  <Field label="Output size">
                    <input
                      className={fieldClass}
                      value={coverSize}
                      onChange={(e) => setCoverSize(e.target.value)}
                      placeholder="1024x1536"
                      aria-describedby="cover-size-hint"
                    />
                  </Field>
                  <Field label="Quality">
                    <select
                      className={fieldClass}
                      value={coverQuality}
                      onChange={(e) => setCoverQuality(e.target.value as StudioCoverStatus["quality"])}
                    >
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                      <option value="low">Low</option>
                      <option value="auto">Auto</option>
                    </select>
                  </Field>
                  <Field label="Format">
                    <select
                      className={fieldClass}
                      value={coverFormat}
                      onChange={(e) => setCoverFormat(e.target.value as StudioCoverStatus["output_format"])}
                    >
                      <option value="jpeg">JPEG</option>
                      <option value="png">PNG</option>
                    </select>
                  </Field>
                  <Field label="Candidates">
                    <input
                      className={fieldClass}
                      type="number"
                      min={3}
                      max={5}
                      value={coverCount}
                      onChange={(e) => setCoverCount(Number(e.target.value))}
                    />
                  </Field>
                  <Field label="Timeout seconds">
                    <input
                      className={fieldClass}
                      type="number"
                      min={1}
                      value={coverTimeout}
                      onChange={(e) => setCoverTimeout(Number(e.target.value))}
                    />
                  </Field>
                </div>
                <p id="cover-size-hint" className="mt-2 text-[12px] text-ink-muted">
                  Use a portrait 2:3 ratio; the provider may return a different resolution.
                </p>
                <Field label="Cover API base URL">
                  <input
                    className={fieldClass}
                    value={coverBaseUrl}
                    onChange={(e) => setCoverBaseUrl(e.target.value)}
                    placeholder="https://sub2api.example/v1"
                  />
                </Field>
                <Field label="Cover API key">
                  <input
                    className={fieldClass}
                    type="password"
                    value={coverApiKey}
                    onChange={(e) => setCoverApiKey(e.target.value)}
                    placeholder={coverStatus.has_api_key ? "Configured" : "Sub2API key"}
                    autoComplete="off"
                  />
                </Field>
                <button
                  type="button"
                  disabled={coverBusy}
                  onClick={saveCover}
                  className="btn-primary mt-2 disabled:opacity-40"
                >
                  {coverBusy ? "Saving…" : "Save cover settings"}
                </button>
              </section>
            )}
          </div>
        </motion.div>
      </div>
    </Scene>
  );
}
