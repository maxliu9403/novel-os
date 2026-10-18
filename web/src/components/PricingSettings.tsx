import { useEffect, useState, type FormEvent } from "react";
import { api, type ModelPricing, type StudioPricing } from "../api/client";
import "./pricing-settings.css";

const fields = [
  ["input_usd_per_million", "输入 / 百万 Token"],
  ["cached_input_usd_per_million", "缓存输入 / 百万 Token"],
  ["output_usd_per_million", "输出 / 百万 Token"],
  ["image_usd_per_image", "每张图片"],
] as const;
const emptyPrice = (): ModelPricing => ({provider: "codex", model: "", input_usd_per_million: null,
  cached_input_usd_per_million: null, output_usd_per_million: null, image_usd_per_image: null});

export default function PricingSettings() {
  const [draft, setDraft] = useState<StudioPricing | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [presetBusy, setPresetBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    void api.studioPricing().then(value => { if (active) { setDraft(value); setError(""); } })
      .catch(() => { if (active) setError("无法读取计费设置，请重试。"); });
    return () => { active = false; };
  }, [reload]);
  const change = (index: number, patch: Partial<ModelPricing>) => {
    setDraft(current => current && ({...current, models: current.models.map((row, i) => i === index ? {...row, ...patch} : row)}));
    setMessage("");
  };
  const loadDeepSeekPreset = async () => {
    setPresetBusy(true); setError(""); setMessage("");
    try {
      const preset = await api.studioPricingPreset("deepseek");
      setDraft(current => {
        if (!current) return current;
        const identities = new Set(preset.models.map(row => `${row.provider}:${row.model}`));
        const kept = current.models.filter(row => !identities.has(`${row.provider}:${row.model}`));
        return {...current, models: [...kept, ...preset.models]};
      });
      setMessage("已载入 DeepSeek 两档参考价；保存后生效，可继续手动修改。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "载入 DeepSeek 参考价失败，请重试。");
    } finally {
      setPresetBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    setError(""); setMessage("");
    const keys = draft.models.map(row => `${row.provider.trim()}:${row.model.trim()}`);
    if (draft.models.some(row => !row.provider.trim() || !row.model.trim())) {
      setError("请填写每一项的服务商与完整模型 ID。"); return;
    }
    if (new Set(keys).size !== keys.length) { setError("同一服务商和模型只能设置一组单价。"); return; }
    setSaving(true);
    try {
      const updated = await api.updateStudioPricing({...draft,
        exchange_rate_status: draft.usd_to_cny === null ? "unset" : "configured",
        models: draft.models.map(row => ({...row, provider: row.provider.trim(), model: row.model.trim()})),
      });
      setDraft(updated); setMessage("计费设置已保存，作品费用将按新单价重新估算。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试。"); }
    finally { setSaving(false); }
  };
  if (!draft) return <div className="pricing-settings"><p>{error || "正在读取计费设置…"}</p>{error && <button type="button" className="btn-secondary" onClick={() => setReload(value => value + 1)}>重试计费设置</button>}</div>;
  return <form className="pricing-settings" onSubmit={event => void submit(event)}>
    <header><div><p className="pricing-settings__eyebrow">费用估算</p><h2>模型单价与换算</h2>
      <p>以真实调用用量计算人民币参考费用，帮助了解每本作品的制作投入。</p></div>
      <button type="submit" className="btn-primary" disabled={saving}>{saving ? "保存中…" : "保存计费设置"}</button></header>
    {error && <p role="alert" className="pricing-settings__error">{error}</p>}
    {message && <p role="status" className="pricing-settings__success">{message}</p>}
    <div className="pricing-settings__preset">
      <div className="pricing-settings__preset-left">
        <span className="pricing-settings__preset-mark">DS</span>
        <div>
          <h3>DeepSeek 参考价</h3>
          <p>一键填入 deepseek-flash 与 deepseek-v4-pro 的峰值输入、缓存输入和输出参考价（非高峰约减半）；不会覆盖其它服务商的自定义价格。</p>
        </div>
      </div>
      <button type="button" className="btn-secondary" disabled={saving || presetBusy} onClick={() => void loadDeepSeekPreset()}>{presetBusy ? "载入中…" : "载入 2 个模型参考价"}</button>
    </div>
    <fieldset className="pricing-settings__exchange" disabled={saving}>
      <legend>人民币换算</legend>
      <label htmlFor="cost-exchange-rate">1 美元兑换人民币</label>
      <div><span>1 USD =</span><input id="cost-exchange-rate" type="number" step="any" min="0.000001" placeholder="未设置" value={draft.usd_to_cny ?? ""}
        onChange={event => { setDraft({...draft, usd_to_cny: event.target.value === "" ? null : Number(event.target.value)}); setMessage(""); }} /><span>CNY</span></div>
      <p>{draft.exchange_rate_status === "assumed" ? "当前为初始参考换算值，并非实时汇率。" : "按你保存的换算值计算，不自动获取实时汇率。"}修改后会重新估算历史已记录用量，不改变原始 Token 记录。</p>
    </fieldset>
    <div className="pricing-settings__section-heading"><div><h3>模型单价</h3><p>以下金额均为美元。留空表示未配置，0 表示你明确设定该项免费。</p></div><button className="btn-secondary" type="button" disabled={saving} onClick={() => { setDraft({...draft, models: [...draft.models, emptyPrice()]}); setMessage(""); }}>添加模型单价</button></div>
    <div className="pricing-settings__models">
      {draft.models.map((row, index) => <fieldset className="pricing-settings__model" key={index} disabled={saving}>
        <legend>模型单价 {index + 1}</legend>
        <div className="pricing-settings__identity">
          <label>服务商标识<input aria-label={`服务商 ${index + 1}`} list="pricing-providers" value={row.provider} onChange={event => change(index, {provider: event.target.value})} required /></label>
          <label>模型 ID<input aria-label={`模型 ${index + 1}`} placeholder="完整模型名称" value={row.model} onChange={event => change(index, {model: event.target.value})} required /></label>
          <button type="button" aria-label={`移除模型单价 ${index + 1}`} onClick={() => { setDraft({...draft, models: draft.models.filter((_, i) => i !== index)}); setMessage(""); }}>移除</button>
        </div>
        <div className="pricing-settings__rates">{fields.map(([key, label]) => <label key={key}>{label}<div><span>$</span><input aria-label={`${label} ${index + 1}`} type="number" min="0" step="any" placeholder="未配置" value={row[key] ?? ""}
          onChange={event => change(index, {[key]: event.target.value === "" ? null : Number(event.target.value)})} /></div></label>)}</div>
      </fieldset>)}
      {!draft.models.length && <p className="pricing-settings__empty">还没有单价。添加模型后，已记录的用量即可参与费用估算。</p>}
    </div>
    <datalist id="pricing-providers"><option value="codex"/><option value="openai"/><option value="openai_compatible"/><option value="anthropic"/><option value="claude_cli"/></datalist>
    <aside className="pricing-settings__footnote">
      <p>Codex / Claude 订阅的估算金额不等同于实际扣款。预置文本单价以标准短上下文为参考；服务档位、长上下文或供应商折扣应按你的计费规则调整。</p>
      <p>图像按“每张图片”的配置估算；图片 Token 仅在服务返回可用记录时计入。缺失用量或未配置单价会保留为“待核算”，不补造历史费用。</p>
      <a href="https://developers.openai.com/api/docs/pricing" target="_blank" rel="noreferrer">查看 OpenAI 官方价格参考 ↗</a>
    </aside>
  </form>;
}
