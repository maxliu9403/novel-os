import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CostStage, type CoverCostActivity, type CostTotals, type GenerationCosts } from "../api/client";
import "./generation-costs.css";

const stages: Record<CostStage, {label: string; detail: string; number: string}> = {
  brainstorming: {label: "头脑风暴", detail: "构思、讨论与骨架", number: "01"},
  manuscript: {label: "正文创作", detail: "规划、写作与审校", number: "02"},
  cover: {label: "封面设计", detail: "方向、出图、精修与建议", number: "03"},
};
const coverActivities: Record<CoverCostActivity, string> = {
  plan: "设计方向", generate: "图片生成", refine: "原图精修", retry: "失败恢复", advisory: "画面建议", legacy: "历史未细分",
};
const integer = new Intl.NumberFormat("zh-CN");
const money = (value: number | null) => value === null ? "待核算" : value > 0 && value < 0.0001 ? "< ¥0.0001" : `¥${value.toLocaleString("zh-CN", {
  minimumFractionDigits: 2, maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
})}`;
function tokens(value: CostTotals) {
  if (!value.request_count) return "尚无记录";
  return (value.usage_coverage?.total_tokens ?? value.known_usage_requests) ? `${integer.format(value.total_tokens)} tokens` : "Token 未记录";
}

export default function GenerationCostPanel({owner, refreshKey = 0}: {owner: string; refreshKey?: string | number}) {
  const [result, setResult] = useState<{owner: string; data: GenerationCosts} | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    let latestRequest = 0;
    const load = async () => {
      const request = ++latestRequest;
      try {
        const data = await api.generationCosts(owner);
        if (active && request === latestRequest) { setResult({owner, data}); setError(""); }
      } catch {
        if (active && request === latestRequest) setError("用量暂时无法读取");
      }
    };
    void load();
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    const timer = window.setInterval(visible, 30_000);
    window.addEventListener("focus", visible);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", visible); };
  }, [owner, refreshKey, refresh]);
  const data = result?.owner === owner ? result.data : null;
  if (!data) return <section className="generation-costs generation-costs--loading" aria-label="创作用量与费用">
    <span>{error || "正在读取创作用量…"}</span>
    {error && <button type="button" onClick={() => setRefresh(value => value + 1)}>重试用量统计</button>}
  </section>;
  const total = data.totals;
  const recorded = (key: "input_tokens" | "output_tokens" | "cached_input_tokens" | "reasoning_tokens") => {
    const covered = total.usage_coverage?.[key] ?? total.known_usage_requests;
    return covered ? `${integer.format(total[key])} · 已记录` : "未记录";
  };
  return <section className="generation-costs" aria-label="创作用量与费用">
    <div className="generation-costs__heading">
      <div><span className="generation-costs__eyebrow">制作账本</span><h2>创作用量与费用</h2></div>
      <Link to="/settings?tab=pricing">单价与汇率 <span aria-hidden="true">↗</span></Link>
    </div>
    <p className="generation-costs__note">Token 按模型调用结束后返回的用量更新，页面可见时每 30 秒刷新；当前调用尚未返回的用量不代表 0，也不代表生成速度。</p>
    {error && <p role="status" className="generation-costs__note">{error}，下方保留上次读取结果。<button type="button" onClick={() => setRefresh(value => value + 1)}>重试</button></p>}
    <div className="generation-costs__grid">
      <div className="generation-costs__total">
        <span>{total.cost_complete ? "已记录总费用 · 估算" : "已计费用 · 估算小计"}</span>
        <strong>{money(total.estimated_cost_cny)}</strong>
        <p>{tokens(total)}{total.unknown_usage_requests > 0 && <span className="generation-costs__partial"> · 用量不完整</span>}</p>
      </div>
      {data.stages.map(stage => <div className="generation-costs__stage" key={stage.stage}>
        <span className="generation-costs__stage-label"><i>{stages[stage.stage].number}</i>{stages[stage.stage].label}</span>
        <strong>{!stage.request_count ? "—" : money(stage.estimated_cost_cny)}{!stage.cost_complete && stage.estimated_cost_cny !== null && <small>小计</small>}</strong>
        <p>{tokens(stage)}</p><span className="generation-costs__description">{stages[stage.stage].detail}</span>
      </div>)}
    </div>
    {!!data.cover_activities?.length && <div className="generation-costs__activities">
      <div className="generation-costs__table-wrap"><table><caption>封面制作明细</caption><thead><tr><th>步骤</th><th>调用记录</th><th>Token</th><th>估算人民币</th></tr></thead><tbody>{data.cover_activities.map(row => <tr key={row.activity}>
        <th scope="row">{coverActivities[row.activity] || row.activity}</th><td>{integer.format(row.request_count)} 次</td><td>{tokens(row)}{row.unknown_usage_requests > 0 && <small>用量不完整</small>}</td><td>{row.request_count ? money(row.estimated_cost_cny) : "—"}{!row.cost_complete && row.estimated_cost_cny !== null && <small>部分费用</small>}</td>
      </tr>)}</tbody></table></div>
      <p>仅列出已记录的模型调用；纯本地标题排版不调用模型。历史未细分的记录不推测所属步骤。</p>
    </div>}
    <details className="generation-costs__details">
      <summary>用量明细与计费说明 <span>{integer.format(total.request_count)} 次调用记录</span></summary>
      <div className="generation-costs__breakdown">
        <dl><div><dt>输入 Token</dt><dd>{recorded("input_tokens")}</dd></div>
          <div><dt>其中缓存命中</dt><dd>{recorded("cached_input_tokens")}</dd></div>
          <div><dt>输出 Token</dt><dd>{recorded("output_tokens")}</dd></div>
          <div><dt>其中推理 Token</dt><dd>{recorded("reasoning_tokens")}</dd></div></dl>
        <p>总 Token 为已记录的输入与输出之和；缓存与推理属于其中的子项，不重复相加。未返回用量的调用和历史缺失记录不按 0 Token 计。</p>
        {total.unknown_usage_requests > 0 && <p className="generation-costs__partial">{total.unknown_usage_requests} 次调用缺少完整用量；显示的金额可能仅覆盖部分调用。</p>}
        <p>{data.pricing.usd_to_cny === null ? "尚未设置美元兑人民币换算值。" : `当前参考换算值：1 USD = ${data.pricing.usd_to_cny} CNY${data.pricing.exchange_rate_status === "assumed" ? "（初始参考值，可在设置中调整）" : "（按当前设置）"}。`}{data.pricing.request_snapshots_present ? "已有计价快照的调用按当次保存的单价与汇率估算；没有快照的历史记录按当前配置估算。" : "当前记录按配置单价与汇率估算；后续保存计价快照的调用将使用当次配置。"}不代表订阅实际扣款或供应商账单。</p>
        {total.mixed_exchange_rates && <p>已记录调用采用了不同汇率，总人民币费用为各次调用估算金额之和。</p>}
        {data.models.length > 0 && <div className="generation-costs__table-wrap"><table><caption>模型与阶段明细</caption><thead><tr><th>阶段 / 模型</th><th>Token</th><th>估算人民币</th></tr></thead><tbody>{data.models.map((row, index) => <tr key={`${row.stage}:${row.provider}:${row.model}:${index}`}>
          <th scope="row"><span>{stages[row.stage].label}</span><b>{row.model || "模型未记录"}</b><small>{row.provider}</small></th><td>{(row.usage_coverage?.total_tokens ?? row.known_usage_requests) ? integer.format(row.total_tokens) : "未记录"}</td><td>{money(row.estimated_cost_cny)}{!row.cost_complete && row.estimated_cost_cny !== null && <small>部分费用</small>}</td>
        </tr>)}</tbody></table></div>}
        {data.notes.map((note, index) => <p key={index}>{note}</p>)}
      </div>
    </details>
  </section>;
}
