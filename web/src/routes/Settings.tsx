import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { motion } from "motion/react";
import {
  api,
  type ImageModelProfile,
  type ImageModelCatalog,
  type ImageTestResult,
  type JobStatus,
  type ModelCapability,
  type ProviderConnection,
  type ProviderConnectionInput,
  type ProviderModelDetail,
  type ProviderTemplate,
  type StudioModelConfiguration,
  type TextModelRoute,
  type TextTestResult,
  type VisionRoute,
} from "../api/client";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useConfirm } from "../components/confirmContext";
import { useToast } from "../components/toastContext";
import PricingSettings from "../components/PricingSettings";
import "../styles/settings.css";

type SettingsTab = "connections" | "text" | "images" | "pricing";

const controlClass =
  "h-10 w-full rounded-lg border border-[rgba(74,91,133,0.18)] bg-white px-3 text-[14px] font-medium text-ink-text shadow-[inset_0_1px_2px_rgba(23,33,63,0.04)] placeholder:text-paper-muted focus:border-[rgba(104,103,234,0.55)] focus:outline-none focus:ring-4 focus:ring-[rgba(104,103,234,0.09)]";

const ROUTE_LABELS: Record<string, { label: string; detail: string }> = {
  default: { label: "默认写作模型", detail: "所有文本任务的默认选择" },
  workshop: { label: "头脑风暴", detail: "小说骨架讨论，可与正文模型不同" },
  architect: { label: "架构师", detail: "全局大纲、章节规划与核查" },
  writer: { label: "执笔者", detail: "章节初稿写作" },
  editor: { label: "编辑", detail: "审稿发现问题后，按需修订正文" },
  guardian: { label: "连续性守卫", detail: "故事、连续性、文风与读者体验联合审稿" },
  style: { label: "风格策展", detail: "出版简介与文案；旧任务的文风处理" },
  judge: { label: "试读评估", detail: "历史流程的英文试读评估；新流程由联合审稿覆盖" },
  cover_director: { label: "封面指导", detail: "封面/海报策划（文本）；视觉审阅在图像生成页" },
};

const REASONING_EFFORT_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "很高",
  max: "最大",
  ultra: "极限",
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForStudioJob(initial: JobStatus, timeoutMilliseconds: number) {
  const deadline = Date.now() + timeoutMilliseconds;
  let job = initial;
  while (job.status === "running") {
    if (Date.now() >= deadline) throw new Error("模型测试超时");
    await wait(750);
    job = await api.getJob(job.job_id);
  }
  if (job.status === "error") throw new Error(job.error || "模型测试失败");
  return job;
}

function jobResult<T>(job: JobStatus): T {
  if (!job.meta) throw new Error("模型测试未返回结果");
  return job.meta as T;
}

export default function Settings() {
  const toast = useToast();
  const confirm = useConfirm();
  const [tab, setTab] = useState<SettingsTab>(() => {
    const initial = new URLSearchParams(window.location.search).get("tab");
    return initial === "text" || initial === "images" || initial === "pricing" ? initial : "connections";
  });
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState(window.history.state, "", url);
  }, [tab]);
  const [configuration, setConfiguration] = useState<StudioModelConfiguration | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ProviderConnection | "new" | null>(null);
  const [newProvider, setNewProvider] = useState<string | undefined>();
  const [testingId, setTestingId] = useState("");

  const load = useCallback(async () => {
    try {
      const next = await api.studioModels();
      setConfiguration(next);
      setError("");
    } catch (nextError) {
      setError(messageOf(nextError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void api.studioModels().then((next) => {
      if (!active) return;
      setConfiguration(next);
      setError("");
    }).catch((nextError) => {
      if (active) setError(messageOf(nextError));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const textReady = configuration?.text_routes.find((route) => route.id === "default")?.configured ?? false;
  const imageReady = configuration?.image_profiles.cover.configured ?? false;
  const visionReady = configuration?.vision_routes?.find((route) => route.id === "cover_review")?.configured ?? false;

  async function testConnection(connection: ProviderConnection) {
    setTestingId(connection.id);
    try {
      const result = await api.testProvider(connection.id);
      toast(result.message, result.ok ? "success" : "error");
      await load();
    } catch (nextError) {
      toast(messageOf(nextError), "error");
    } finally {
      setTestingId("");
    }
  }

  async function removeConnection(connection: ProviderConnection) {
    const accepted = await confirm({
      title: "删除连接",
      message: `确定删除“${connection.name}”吗？已分配给模型路由的连接无法删除。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!accepted) return;
    try {
      await api.deleteProvider(connection.id);
      toast("连接已删除", "success");
      await load();
    } catch (nextError) {
      toast(messageOf(nextError), "error");
    }
  }

  return (
    <Scene quiet>
      <div className="workspace-page settings-page">
        <div className="glass-panel settings-workbench">
          <motion.header
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.32 }}
            className="settings-masthead"
          >
            <div>
              <p className="settings-kicker">创作工作台</p>
              <h1 className="settings-title">设置</h1>
              <p className="settings-lede">管理模型连接、创作配置与费用估算。</p>
            </div>
            <div className="settings-readiness" aria-label="配置状态">
              <span data-ready={textReady}>文本 {textReady ? "已配置" : "待配置"}</span>
              <span data-ready={imageReady}>出图 {imageReady ? "已配置" : "待配置"}</span>
              <span data-ready={visionReady}>视觉 {visionReady ? "已配置" : "待配置"}</span>
            </div>
          </motion.header>

          <nav aria-label="模型设置" role="tablist" className="settings-tabs">
            <TabButton active={tab === "connections"} onClick={() => setTab("connections")} icon="waypoints" label="服务连接" />
            <TabButton active={tab === "text"} onClick={() => setTab("text")} icon="bot" label="文本路由" />
            <TabButton active={tab === "images"} onClick={() => setTab("images")} icon="image" label="图像生成" />
            <TabButton active={tab === "pricing"} onClick={() => setTab("pricing")} icon="waypoints" label="费用估算" />
          </nav>

          {error && <div role="alert" className="settings-error">{error}</div>}

          {loading && <div className="settings-loading">正在读取模型配置…</div>}

          {configuration && !loading && (
            <div className="settings-stage">
              <motion.div
                key={tab}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                className="settings-main"
              >
                {tab === "connections" && (
                  <div className="settings-surface settings-surface--flush">
                    <ConnectionsPanel
                      connections={configuration.connections}
                      templates={configuration.templates}
                      testingId={testingId}
                      onAdd={(provider) => { setNewProvider(provider); setEditing("new"); }}
                      onEdit={setEditing}
                      onTest={(connection) => void testConnection(connection)}
                      onDelete={(connection) => void removeConnection(connection)}
                    />
                  </div>
                )}
                {tab === "text" && (
                  <div className="settings-surface">
                    <TextRoutesPanel
                      key={configuration.text_routes.map((route) => `${route.id}:${route.connection_id}:${route.model}:${route.reasoning_effort}:${route.inherits_default}:${route.effective_timeout_seconds}`).join("|")}
                      routes={configuration.text_routes}
                      connections={configuration.connections}
                      onSaved={load}
                    />
                  </div>
                )}
                {tab === "images" && (
                  <div className="settings-surface settings-surface--flush">
                    <VisionReviewPanel
                      key={(configuration.vision_routes ?? []).map((route) => `${route.id}:${route.connection_id}:${route.model}`).join("|")}
                      route={(configuration.vision_routes ?? []).find((item) => item.id === "cover_review")}
                      connections={configuration.connections}
                      onSaved={load}
                    />
                    <ImageProfilePanel
                      key={`${configuration.image_profiles.cover.connection_id}:${configuration.image_profiles.cover.model}:${configuration.image_profiles.cover.size}:${configuration.image_profiles.cover.quality}:${configuration.image_profiles.cover.output_format}:${configuration.image_profiles.cover.count}`}
                      profile={configuration.image_profiles.cover}
                      connections={configuration.connections}
                      onSaved={load}
                    />
                  </div>
                )}
                {tab === "pricing" && (
                  <div className="settings-surface">
                    <PricingSettings />
                  </div>
                )}
              </motion.div>
            </div>
          )}
        </div>
      </div>

      {configuration && (
        <ProviderEditor
          key={editing === "new" ? `new:${newProvider ?? ""}` : editing?.id ?? "closed"}
          open={editing !== null}
          connection={editing === "new" ? null : editing}
          initialProvider={newProvider}
          templates={configuration.templates}
          onClose={() => { setEditing(null); setNewProvider(undefined); }}
          onSaved={async () => {
            setEditing(null);
            setNewProvider(undefined);
            await load();
          }}
        />
      )}
    </Scene>
  );

}

function TabButton({ active, onClick, icon, label }: {
  active: boolean;
  onClick: () => void;
  icon: "waypoints" | "bot" | "image";
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-label={label}
      aria-selected={active}
      onClick={onClick}
      className="settings-tab"
    >
      <span className="settings-tab__icon" aria-hidden><Icon name={icon} className="h-3.5 w-3.5" /></span>
      <span className="settings-tab__label" aria-hidden>{label}</span>
    </button>
  );
}

const PROVIDER_MARKS: Record<string, string> = {
  codex: "CX", deepseek: "DS", openai: "OA", openai_compatible: "OC",
  gemini: "G", gemini_openai: "G", anthropic: "AN", openrouter: "OR", ollama: "OL",
};

function capabilityLabel(capability: ModelCapability): string {
  if (capability === "text_generation") return "文本";
  if (capability === "image_generation") return "图像";
  return "视觉";
}

function ConnectionsPanel({ connections, templates, testingId, onAdd, onEdit, onTest, onDelete }: {
  connections: ProviderConnection[];
  templates: ProviderTemplate[];
  testingId: string;
  onAdd: (provider?: string) => void;
  onEdit: (connection: ProviderConnection) => void;
  onTest: (connection: ProviderConnection) => void;
  onDelete: (connection: ProviderConnection) => void;
}) {
  return (
    <section aria-labelledby="connections-title" className="settings-connections">
      <SectionHeading
        id="connections-title"
        title="服务商连接"
        detail="添加服务商后，在文本与图像页选择要使用的模型。"
        action={<button type="button" onClick={() => onAdd()} className="btn-primary !rounded-full"><Icon name="plus" className="h-3.5 w-3.5" /> 添加连接</button>}
      />

      {connections.length === 0 ? (
        <div className="settings-empty-connections">
          <Icon name="waypoints" className="h-6 w-6 text-ink-muted" />
          <p>暂无服务商连接</p>
          <button type="button" className="btn-secondary mt-4 !rounded-full" onClick={() => onAdd()}>添加第一个连接</button>
        </div>
      ) : (
        <div className="settings-connection-grid">
          {connections.map((connection) => {
            const failed = connection.last_test_ok === false;
            const ready = connection.status === "ready" && connection.last_test_ok === true;
            const statusClass = failed ? "error" : ready ? "ready" : "warn";
            const statusText = failed ? "测试失败" : connection.last_test_ok ? "已验证" : "未验证";
            const statusDetail = connection.last_tested_at
              ? (connection.last_test_ok ? "连接验证成功" : connection.last_test_error)
              : (connection.error || "尚未测试");
            const visibleModels = connection.discovered_models.slice(0, 3);
            const moreModels = connection.discovered_models.length - visibleModels.length;
            return (
              <article className="settings-connection" key={connection.id}>
                <div className="settings-connection__top">
                  <div className="settings-connection__id">
                    <span className="settings-connection__mark" aria-hidden>{PROVIDER_MARKS[connection.provider] ?? connection.provider.slice(0, 2).toUpperCase()}</span>
                    <div className="min-w-0">
                      <h3 className="settings-connection__name">{connection.name}</h3>
                      <p className="settings-connection__provider">{connection.provider.replaceAll("_", " ")} · {authLabel(connection.auth_type)}</p>
                    </div>
                  </div>
                  <span className={`settings-connection__status settings-connection__status--${statusClass}`}>
                    <span className={`settings-status-dot settings-status-dot--${ready ? "ready" : "warn"}`} aria-hidden />
                    {statusText}
                  </span>
                </div>

                <div className="settings-connection__caps">
                  {connection.capabilities.map((capability) => (
                    <span key={capability} className={`settings-cap ${capability === "image_input" ? "settings-cap--accent" : ""}`}>{capabilityLabel(capability)}</span>
                  ))}
                  <span className="settings-cap">{connection.discovered_models.length} 个模型</span>
                </div>

                <div className="settings-connection__models" aria-label={`${connection.name} 模型`}>
                  {visibleModels.length ? visibleModels.map((model) => (
                    <span className="settings-model-chip" key={model} title={model}>
                      {connection.model_details?.[model]?.name && connection.model_details[model].name !== model
                        ? `${connection.model_details[model].name} · ${model}`
                        : model}
                    </span>
                  )) : <span className="settings-model-chip">尚未读取模型目录</span>}
                  {moreModels > 0 && <span className="settings-model-chip settings-model-chip--more">+{moreModels}</span>}
                </div>

                <p className={`settings-connection__meta ${failed ? "settings-connection__meta--error" : ""}`}>{statusDetail}</p>

                <div className="settings-connection__actions">
                  <button type="button" className="btn-secondary" disabled={testingId === connection.id} onClick={() => onTest(connection)}>{testingId === connection.id ? "测试中…" : "测试"}</button>
                  <button type="button" className="btn-ghost" onClick={() => onEdit(connection)}>编辑</button>
                  <button type="button" aria-label={`删除${connection.name}`} title="删除连接" className="settings-connection__delete" onClick={() => onDelete(connection)}>
                    <span aria-hidden className="text-[16px] leading-none">×</span>
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="settings-subsection">
        <div className="settings-subhead">
          <div>
            <h3>可添加的服务商</h3>
            <p>支持原生 API 与自定义兼容连接。</p>
          </div>
          <span className="settings-subhead__count">{templates.length} 种服务商</span>
        </div>
        <div className="settings-template-grid">
          {templates.map((template) => (
            <button key={template.id} type="button" className="settings-template" aria-label={`添加 ${template.label} 连接`} onClick={() => onAdd(template.id)}>
              <span className="settings-template__mark" aria-hidden>{PROVIDER_MARKS[template.id] ?? template.label.slice(0, 2).toUpperCase()}</span>
              <span className="settings-template__copy">
                <strong>{template.label}</strong>
                <span>{template.capabilities.map(capabilityLabel).join(" / ")}</span>
              </span>
              <span className="settings-template__plus" aria-hidden><Icon name="plus" className="h-3 w-3" /></span>
            </button>
          ))}
        </div>
      </div>

    </section>
  );
}

function ProviderEditor({ open, connection, initialProvider, templates, onClose, onSaved }: {
  open: boolean;
  connection: ProviderConnection | null;
  initialProvider?: string;
  templates: ProviderTemplate[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const initialTemplate = templates.find((item) => item.id === (connection?.provider ?? initialProvider)) ?? templates[0];
  const [provider, setProvider] = useState(initialTemplate?.id ?? "openai_compatible");
  const [name, setName] = useState(connection?.name ?? initialTemplate?.label ?? "");
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? initialTemplate?.default_base_url ?? "");
  const [imageBaseUrl, setImageBaseUrl] = useState(connection?.image_base_url ?? "");
  const [capabilities, setCapabilities] = useState<ModelCapability[]>(connection?.capabilities ?? initialTemplate?.capabilities ?? []);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);

  const template = templates.find((item) => item.id === provider) ?? templates[0];

  function changeProvider(nextProvider: string) {
    const next = templates.find((item) => item.id === nextProvider);
    setProvider(nextProvider);
    setName(next?.label ?? "");
    setBaseUrl(next?.default_base_url ?? "");
    setImageBaseUrl("");
    setCapabilities(next?.capabilities ?? []);
    setClearKey(false);
  }

  function toggleCapability(capability: ModelCapability) {
    setCapabilities((current) => current.includes(capability) ? current.filter((item) => item !== capability) : [...current, capability]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!template) return;
    setBusy(true);
    const body: ProviderConnectionInput = {
      name: name.trim(),
      provider,
      auth_type: template.auth_type,
      base_url: baseUrl.trim(),
      image_base_url: imageBaseUrl.trim(),
      capabilities,
      secret_action: clearKey ? "clear" : apiKey.trim() ? "replace" : "keep",
      api_key: apiKey.trim() || undefined,
    };
    try {
      if (connection) await api.updateProvider(connection.id, body);
      else await api.createProvider(body);
      setApiKey("");
      toast(connection ? "连接已更新" : "连接已创建", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={connection ? "编辑连接" : "添加连接"}>
      <form onSubmit={(event) => void submit(event)}>
        <FormField label="服务商" htmlFor="provider-type">
          <select id="provider-type" className={controlClass} value={provider} disabled={!!connection} onChange={(event) => changeProvider(event.target.value)}>
            {templates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </FormField>
        <FormField label="连接名称" htmlFor="provider-name">
          <input id="provider-name" className={controlClass} value={name} onChange={(event) => setName(event.target.value)} required />
        </FormField>
        {provider !== "codex" && (
          <FormField label="API 基础地址" htmlFor="provider-base-url">
            <input id="provider-base-url" className={controlClass} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" required />
          </FormField>
        )}
        <fieldset className="mb-5">
          <legend className="mb-2 text-[14px] font-medium text-ink-muted">功能</legend>
          <div className="grid grid-cols-3 gap-2">
            <CapabilityToggle active={capabilities.includes("text_generation")} disabled={!template?.capabilities.includes("text_generation")} label="文本生成" icon="bot" onClick={() => toggleCapability("text_generation")} />
            <CapabilityToggle active={capabilities.includes("image_generation")} disabled={!template?.capabilities.includes("image_generation")} label="图像生成" icon="image" onClick={() => toggleCapability("image_generation")} />
            <CapabilityToggle active={capabilities.includes("image_input")} disabled={!template?.capabilities.includes("image_input")} label="视觉理解" icon="eye" onClick={() => toggleCapability("image_input")} />
          </div>
        </fieldset>
        {provider === "gemini_openai" && <p className="mb-5 text-[14px] leading-relaxed text-ink-muted">填写 OpenAI 格式网关地址（以 /v1 结尾）和 Token。Docker 访问本机网关可用 http://host.docker.internal:8083/v1。思考档位由完整模型 ID 决定；此连接适配 gemini-web2api，不是 Google 原生 API。</p>}
        {provider === "gemini" && <p className="mb-5 text-[14px] leading-relaxed text-ink-muted">使用 Google AI Studio API 密钥及 Gemini 原生接口。支持文本创作、参考图片理解；模型列表按账户权限读取。Vertex AI / ADC 需另行接入。</p>}
        {provider === "deepseek" && (
          <div className="mb-5 rounded-lg border border-[rgba(74,91,133,0.13)] bg-[#f7f9fd] px-3 py-3 text-[14px] leading-relaxed text-ink-muted">
            DeepSeek 在当前连接中只声明文本生成。图像生成与视觉理解请使用 Codex、OpenAI 或其他支持对应能力的连接。
          </div>
        )}
        {capabilities.includes("image_generation") && provider !== "codex" && (
          <FormField label="图像 API 基础地址（可选覆盖）" htmlFor="provider-image-url">
            <input id="provider-image-url" className={controlClass} value={imageBaseUrl} onChange={(event) => setImageBaseUrl(event.target.value)} placeholder={baseUrl || "使用 API 基础地址"} />
          </FormField>
        )}
        {template?.auth_type === "api_key" && (
          <FormField label={connection?.has_api_key ? "API 密钥（已配置）" : "API 密钥"} htmlFor="provider-api-key">
            <input id="provider-api-key" className={controlClass} type="password" value={apiKey} disabled={clearKey} onChange={(event) => setApiKey(event.target.value)} placeholder={connection?.has_api_key ? "留空则保留现有密钥" : "必填"} autoComplete="off" />
          </FormField>
        )}
        {connection?.has_api_key && template?.auth_type === "api_key" && (
          <label className="mb-5 flex items-center gap-2 text-[14px] text-ink-muted">
            <input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />
            删除已保存的 API 密钥
          </label>
        )}
        {template?.auth_type === "codex_session" && (
          <div className="mb-5 rounded-lg border border-[rgba(74,91,133,0.13)] bg-[#f7f9fd] px-3 py-3 text-[14px] text-ink-muted">
            Codex 凭据将继续保存在 Codex 凭据存储中。
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-[rgba(74,91,133,0.12)] pt-4">
          <button type="button" className="btn-ghost !rounded-full" onClick={onClose}>取消</button>
          <button type="submit" className="btn-primary !rounded-full" disabled={busy || capabilities.length === 0}>{busy ? "保存中…" : "保存连接"}</button>
        </div>
      </form>
    </Modal>
  );
}

function CapabilityToggle({ active, disabled, label, icon, onClick }: {
  active: boolean;
  disabled: boolean;
  label: string;
  icon: "bot" | "image" | "eye";
  onClick: () => void;
}) {
  return (
    <button type="button" aria-pressed={active} disabled={disabled} onClick={onClick} className={`flex h-11 items-center gap-2 rounded-lg border px-3 text-left text-[14px] font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${active ? "border-[rgba(104,103,234,0.45)] bg-[#f0efff] text-ink-text" : "border-[rgba(74,91,133,0.14)] bg-white text-ink-muted"}`}>
      <Icon name={icon} className="h-3.5 w-3.5" /> {label}
    </button>
  );
}

type ModelCatalog = {
  models: string[];
  details: Record<string, ProviderModelDetail>;
  status: "cached" | "loading" | "ready" | "error";
  error?: string;
};

const ALL_REASONING_LEVELS = ["low", "medium", "high", "xhigh", "max", "ultra"] as const;

function reasoningLevelsFor(provider: string, detail?: ProviderModelDetail, model = ""): string[] {
  const supported = detail?.reasoning?.supported_levels ?? [];
  if (provider === "gemini_openai") return [];
  if (provider === "gemini") {
    if (detail?.reasoning) return supported;
    const name = model.replace(/^models\//, "");
    if (name.startsWith("gemini-3-pro")) return ["low", "high"];
    return /^gemini-(2\.5-|3|4)/.test(name) ? ["low", "medium", "high"] : [];
  }
  if (supported.length) return supported;
  if (provider === "deepseek") return ["low", "high", "max"];
  return [...ALL_REASONING_LEVELS];
}

function modelOptionLabel(model: string, details: Record<string, ProviderModelDetail>): string {
  const name = details[model]?.name;
  return name && name !== model ? name : model;
}

function effectiveConnectionId(
  route: { id: string; inherits_default: boolean; connection_id: string; effective_connection_id?: string },
  defaultRoute?: TextModelRoute,
) {
  return (route.id !== "default" && route.inherits_default
    ? defaultRoute?.connection_id ?? route.effective_connection_id ?? ""
    : route.connection_id ?? "");
}

function TextRoutesPanel({ routes, connections, onSaved }: {
  routes: TextModelRoute[];
  connections: ProviderConnection[];
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [draft, setDraft] = useState(routes);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [workshopTimeoutDirty, setWorkshopTimeoutDirty] = useState(false);
  const [workshopTimeoutMinutes, setWorkshopTimeoutMinutes] = useState(String((routes.find((route) => route.id === "workshop")?.effective_timeout_seconds ?? 900) / 60));
  const [busy, setBusy] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testRouteId, setTestRouteId] = useState("default");
  const [testPrompt, setTestPrompt] = useState("请用一句简短的话确认你可以正常回复。");
  const [contextBytes, setContextBytes] = useState(64000);
  const [testResult, setTestResult] = useState<TextTestResult | null>(null);
  const textConnections = connections.filter((item) => item.capabilities.includes("text_generation"));
  const [manualModels, setManualModels] = useState<Record<string, boolean>>({});
  const [catalogs, setCatalogs] = useState<Record<string, ModelCatalog>>(() => Object.fromEntries(
    connections.map((connection) => [connection.id, {
      models: connection.discovered_models,
      details: connection.model_details ?? {},
      status: "cached",
    }]),
  ));
  const requested = useRef(new Set<string>());
  const inFlight = useRef(new Set<string>());
  const live = useRef(true);
  const defaultRoute = draft.find((route) => route.id === "default");
  const connectionIds = [...new Set(
    draft.map((route) => effectiveConnectionId(route, defaultRoute)).filter((id): id is string => Boolean(id)),
  )].sort();
  const connectionKey = JSON.stringify(connectionIds);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  const loadModels = useCallback(async (connectionId: string) => {
    if (inFlight.current.has(connectionId)) return;
    inFlight.current.add(connectionId);
    setCatalogs((current) => ({
      ...current,
      [connectionId]: {
        ...current[connectionId],
        models: current[connectionId]?.models ?? [],
        details: current[connectionId]?.details ?? {},
        status: "loading",
      },
    }));
    try {
      const catalog = await api.providerModelCatalog(connectionId, true);
      if (live.current) setCatalogs((current) => ({
        ...current,
        [connectionId]: {
          models: [...new Set(catalog.models)],
          details: catalog.details ?? {},
          status: "ready",
        },
      }));
    } catch (error) {
      if (live.current) setCatalogs((current) => ({
        ...current,
        [connectionId]: {
          ...current[connectionId],
          models: current[connectionId]?.models ?? [],
          details: current[connectionId]?.details ?? {},
          status: "error",
          error: messageOf(error),
        },
      }));
    } finally {
      inFlight.current.delete(connectionId);
    }
  }, []);

  useEffect(() => {
    const ids: string[] = JSON.parse(connectionKey);
    for (const id of ids) {
      if (requested.current.has(id)) continue;
      requested.current.add(id);
      void loadModels(id);
    }
  }, [connectionKey, loadModels]);

  function update(routeId: string, patch: Partial<TextModelRoute>) {
    setDraft((current) => current.map((route) => route.id === routeId ? { ...route, ...patch } : route));
  }

  async function save() {
    const timeoutSeconds = Number(workshopTimeoutMinutes) * 60;
    if (workshopTimeoutDirty && (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 60 || timeoutSeconds > 1800)) {
      toast("头脑风暴超时请填写 1–30 分钟", "error");
      return;
    }
    setBusy(true);
    try {
      const updated = await api.updateTextRoutes(draft.map(({ id, connection_id, model, max_tokens, reasoning_effort, inherits_default }) => {
        const effectiveId = effectiveConnectionId({ id, inherits_default, connection_id }, defaultRoute);
        const provider = connections.find((connection) => connection.id === effectiveId)?.provider;
        const effectiveModel = inherits_default ? defaultRoute?.model ?? "" : model;
        const levels = reasoningLevelsFor(provider ?? "", catalogs[effectiveId]?.details?.[effectiveModel], effectiveModel);
        return {
          id, connection_id, model, max_tokens,
          reasoning_effort: levels.includes(reasoning_effort) ? reasoning_effort : "",
          inherits_default,
          ...(id === "workshop" && workshopTimeoutDirty ? { timeout_seconds: timeoutSeconds } : {}),
        };
      }));
      setDraft(updated);
      setWorkshopTimeoutDirty(false);
      setWorkshopTimeoutMinutes(String((updated.find((route) => route.id === "workshop")?.effective_timeout_seconds ?? 900) / 60));
      toast("文本路由已保存", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy(false);
    }
  }

  async function testResponse(context = false) {
    if (context && !await confirm({
      title: "测试长上下文",
      message: `将发送约 ${(contextBytes / 1000).toFixed(0)} KB 合成材料，验证首、中、尾等五处信息读取。会调用一次当前已保存的模型并产生用量，不发送小说。通过不代表写作质量或更大上下文已经通过验证。`,
      confirmLabel: "开始测试",
    })) return;
    setTestBusy(true);
    setTestResult(null);
    try {
      const initial = context
        ? await api.testTextRoute(testRouteId, testPrompt.trim(), { mode: "context", context_bytes: contextBytes })
        : await api.testTextRoute(testRouteId, testPrompt.trim());
      const completed = await waitForStudioJob(initial, context ? 630_000 : 150_000);
      const result = jobResult<TextTestResult>(completed);
      if (!result.reply) throw new Error("模型返回了空响应");
      setTestResult(result);
      toast(result.context_check && !result.context_check.passed ? "长上下文信息读取未全部通过" : "已收到文本响应", result.context_check && !result.context_check.passed ? "error" : "success");
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <section aria-labelledby="text-routes-title">
      <SectionHeading id="text-routes-title" title="文本模型路由" detail="通常只需设置默认写作模型与头脑风暴。其他任务自动沿用默认模型。" action={<button type="button" className="btn-primary !rounded-full" disabled={busy || !textConnections.length} onClick={() => void save()}>{busy ? "保存中…" : "保存路由"}</button>} />
      {!textConnections.length ? (
        <EmptyState icon="bot" title="请先添加支持文本生成的连接" />
      ) : (
        <>
          <div className="mt-4 space-y-2">{connectionIds.map((id) => {
            const catalog = catalogs[id];
            const name = connections.find((connection) => connection.id === id)?.name || id;
            return <div key={id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white/60 px-3 py-2 text-[14px] text-ink-muted">
              <div className="min-w-0 flex-1"><span className="font-medium">{name}</span><span className="ml-2">{catalog?.status === "loading" ? "正在从接口获取模型…" : catalog?.status === "ready" ? catalog.models.length ? `接口返回 ${catalog.models.length} 个模型` : "接口未返回模型，可手动输入" : catalog?.status === "error" ? "模型列表获取失败，仍可手动输入或使用已有选项" : "准备读取模型列表…"}</span>
                {catalog?.error && <p role="alert" className="mt-1 break-words text-[#9b651e]">{catalog.error}</p>}
              </div>
              <button type="button" aria-label={`刷新 ${name} 的模型列表`} className="btn-ghost !px-2 !py-1 !text-[14px] !text-violet" disabled={catalog?.status === "loading"} onClick={() => void loadModels(id)}>刷新模型</button>
            </div>;
          })}</div>
          {[false, true].map((advanced) => (
          <div key={String(advanced)} className={advanced ? "settings-advanced" : "settings-routes"}>
            {advanced && <button type="button" className="settings-advanced__toggle" aria-expanded={showAdvanced} onClick={() => setShowAdvanced(value => !value)}>
              <span>高级任务覆盖 <small>{draft.filter(route => !["default", "workshop"].includes(route.id) && (!route.inherits_default || route.reasoning_effort)).length} 项独立设置</small></span>
              <span>{showAdvanced ? "收起 −" : "展开 +"}</span>
            </button>}
            {(!advanced || showAdvanced) && <div className="settings-route-list">
            {draft.filter(route => advanced !== ["default", "workshop"].includes(route.id)).map((route) => {
            const meta = ROUTE_LABELS[route.id] ?? { label: route.id, detail: "" };
            const inherited = route.id !== "default" && route.inherits_default;
            const connectionId = effectiveConnectionId(route, defaultRoute);
            const selected = connections.find((item) => item.id === connectionId);
            const models = catalogs[connectionId]?.models ?? [];
            const details = catalogs[connectionId]?.details ?? {};
            const currentModel = inherited ? defaultRoute?.model ?? route.effective_model : route.model;
            const manual = !inherited && manualModels[route.id];
            const reasoningDetail = details[currentModel] ?? details[route.model];
            const reasoningLevels = reasoningLevelsFor(selected?.provider ?? "", reasoningDetail, currentModel);
            const reasoningValue = reasoningLevels.includes(route.reasoning_effort) ? route.reasoning_effort : "";
            const defaultReasoning = reasoningDetail?.reasoning?.default_level
              ?? (selected?.provider === "deepseek" ? "high" : route.effective_reasoning_effort);
            const defaultReasoningLabel = defaultReasoning
              ? (REASONING_EFFORT_LABELS[defaultReasoning] ?? defaultReasoning)
              : "";
            return (
              <div key={route.id} className="settings-route-row">
                <div>
                  <h3 className="text-[14px] font-semibold text-ink-text">{meta.label}</h3>
                  <p className="mt-0.5 text-[14px] text-ink-muted">{meta.detail}</p>
                </div>
                <label className="settings-route-field"><span>服务连接</span>
                <select aria-label={`${meta.label}连接`} className={controlClass} value={inherited ? "" : route.connection_id} disabled={inherited} onChange={(event) => {
                  setManualModels((current) => ({ ...current, [route.id]: false }));
                  update(route.id, { connection_id: event.target.value, model: "", reasoning_effort: "" });
                }}>
                  <option value="">{inherited ? `继承 · ${selected?.name ?? "默认连接"}` : "选择连接"}</option>
                  {textConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
                </select>
                </label>
                <div className="settings-route-field">
                  <span>模型</span>
                  <select aria-label={`${meta.label}模型`} className={controlClass} value={manual ? "manual" : `model:${currentModel}`} disabled={inherited} onChange={(event) => {
                    const isManual = event.target.value === "manual";
                    setManualModels((current) => ({ ...current, [route.id]: isManual }));
                    if (!isManual) {
                      const nextModel = event.target.value.slice(6);
                      const nextLevels = reasoningLevelsFor(selected?.provider ?? "", details[nextModel], nextModel);
                      update(route.id, {
                        model: nextModel,
                        reasoning_effort: nextLevels.includes(route.reasoning_effort) ? route.reasoning_effort : "",
                      });
                    }
                  }}>
                    <option value="model:">{selected?.provider === "codex" ? "Codex 默认模型" : "选择模型"}</option>
                    {currentModel && !models.includes(currentModel) && <option value={`model:${currentModel}`}>{modelOptionLabel(currentModel, details)}（当前 / 自定义）</option>}
                    {models.map((model) => <option key={model} value={`model:${model}`}>{modelOptionLabel(model, details)}</option>)}
                    <option value="manual">手动输入…</option>
                  </select>
                  {!manual && currentModel && details[currentModel]?.name && details[currentModel].name !== currentModel && (
                    <p className="settings-model-meta">
                      {currentModel}
                      {details[currentModel].context_window ? ` · ${details[currentModel].context_window >= 1_000_000 ? `${(details[currentModel].context_window / 1_000_000).toFixed(1)}M` : `${Math.round(details[currentModel].context_window / 1000)}K`} ctx` : ""}
                    </p>
                  )}
                  {manual && <input aria-label={`${meta.label}自定义模型`} className={`${controlClass} mt-2`} value={route.model} onChange={(event) => update(route.id, { model: event.target.value })} placeholder="输入完整模型 ID" />}
                </div>
                <label className="settings-route-field"><span>推理强度</span>
                <select
                  aria-label={`${meta.label}推理强度`}
                  className={controlClass}
                  value={reasoningValue}
                  disabled={selected?.provider === "gemini_openai"}
                  onChange={(event) => update(route.id, { reasoning_effort: event.target.value as TextModelRoute["reasoning_effort"] })}
                >
                  <option value="">{selected?.provider === "gemini_openai" ? "由模型 ID 决定" : selected?.provider === "deepseek"
                    ? `模型默认${defaultReasoningLabel ? `（${defaultReasoningLabel}）` : "（高）"}`
                    : `继承 / 默认${defaultReasoningLabel ? `（${defaultReasoningLabel}）` : ""}`}</option>
                  {reasoningLevels.map((level) => <option key={level} value={level}>{REASONING_EFFORT_LABELS[level] ?? level}</option>)}
                </select>
                </label>
                {route.id === "default" ? (
                  <span className={`text-[14px] font-medium ${route.configured ? "text-[#267553]" : "text-[#9b651e]"}`}>{route.configured ? "已就绪" : "未完成"}</span>
                ) : (
                  <label className="flex items-center gap-2 text-[14px] font-medium text-ink-muted"><input type="checkbox" aria-label={`${meta.label}使用默认模型`} checked={inherited} onChange={(event) => update(route.id, { inherits_default: event.target.checked })} /> 使用默认模型</label>
                )}
                <div className="settings-route-budget">
                  <label>{selected?.provider === "gemini_openai" ? "输出额度（网关忽略）" : "输出 Token 上限"} <input type="number" aria-label={`${meta.label}输出 Token 上限`} className={controlClass} min={1} step={1024} disabled={inherited || selected?.provider === "gemini_openai"} value={inherited ? defaultRoute?.max_tokens ?? 8192 : route.max_tokens} onChange={event => update(route.id, { max_tokens: Number(event.target.value) })} /></label>
                  {selected?.provider === "gemini_openai" && <p>网关忽略输出 Token 上限。普通型号为默认档，-thinking 为高档，-thinking-medium 为中档。普通输入上限为 128,000 字节；支持附件协议且有可用登录账号的网关，纯文本输入可协商至 156,000 字节（均含封装预留）。超限停止，不裁剪故事。模型列表与响应型号不证明实际未降级，需核对网关 strict_model 与日志。</p>}
                  {selected?.provider === "gemini" && <p>Gemini 的上限包含思考与正文；模型默认会自动调节思考。2.5 的低／中／高分别使用 1,024／8,192／24,576 思考预算。</p>}
                </div>
                {route.id === "workshop" && <div className="border-t border-paper-line pt-3 lg:col-span-4 lg:col-start-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <label htmlFor="workshop-timeout" className="text-[14px] font-medium text-ink-muted">头脑风暴请求超时（分钟）</label>
                    <input id="workshop-timeout" aria-describedby="workshop-timeout-help" type="number" min={1} max={30} step={1}
                      className={`${controlClass} !w-24`} value={workshopTimeoutMinutes} disabled={busy} onChange={(event) => { setWorkshopTimeoutMinutes(event.target.value); setWorkshopTimeoutDirty(true); }} />
                  </div>
                  <p id="workshop-timeout-help" className="mt-2 text-[14px] leading-relaxed text-ink-muted">默认 15 分钟，可设为 1–30 分钟。独立于默认模型，保存后从下一轮讨论生效，不影响正文创作或正在进行的请求。</p>
                </div>}
              </div>
            );
            })}
            </div>}
          </div>
          ))}
          <div className="mt-6 border-t border-[rgba(74,91,133,0.12)] pt-5">
            <h3 className="text-[14px] font-semibold text-ink-text">文本响应测试</h3>
            <div className="mt-3 grid gap-3 lg:grid-cols-[180px_minmax(0,1fr)_auto] lg:items-end">
              <FormField label="已保存的路由" htmlFor="text-test-route" compact>
                <select id="text-test-route" className={controlClass} value={testRouteId} onChange={(event) => setTestRouteId(event.target.value)}>
                  {routes.map((route) => <option key={route.id} value={route.id}>{ROUTE_LABELS[route.id]?.label ?? route.id}</option>)}
                </select>
              </FormField>
              <FormField label="测试提示词" htmlFor="text-test-prompt" compact>
                <input id="text-test-prompt" className={controlClass} value={testPrompt} maxLength={4000} onChange={(event) => setTestPrompt(event.target.value)} />
              </FormField>
              <button type="button" className="btn-secondary !h-10 !rounded-full" disabled={testBusy || !testPrompt.trim()} onClick={() => void testResponse()}>
                <Icon name="bot" className="mr-2 h-3.5 w-3.5" />{testBusy ? "等待中…" : "测试响应"}
              </button>
            </div>
            <div className="mt-5 flex flex-wrap items-end gap-3">
              <FormField label="长上下文测试材料" htmlFor="context-probe-size" compact>
                <select id="context-probe-size" className={controlClass} value={contextBytes} disabled={testBusy} onChange={event => setContextBytes(Number(event.target.value))}>
                  {[16000, 64000, 100000, 112000].map(size => <option key={size} value={size}>{size / 1000} KB · UTF-8 字节</option>)}
                </select>
              </FormField>
              <button type="button" className="btn-secondary !rounded-full" disabled={testBusy} onClick={() => void testResponse(true)}>{testBusy ? "等待模型…" : "测试长上下文"}</button>
              <p className="max-w-xl text-[14px] leading-6 text-ink-muted">仅使用合成材料，每次一条请求。通过代表该长度下的抽样读取成功，不能证明整本小说都能读全或写得好。</p>
            </div>
            {testResult && (
              <div className="mt-4 border-l-2 border-[rgba(104,103,234,0.55)] bg-[#f7f8fc] px-4 py-3">
                <p className="whitespace-pre-wrap text-[14px] leading-6 text-ink-text">{testResult.reply}</p>
                <p className="mt-2 text-[14px] text-ink-muted">{testResult.provider} · {testResult.model || "默认模型"} · {testResult.duration_ms} 毫秒</p>
                {testResult.request_text_bytes != null && <p className="mt-2 text-[14px] text-ink-muted">输入 {testResult.request_text_bytes.toLocaleString()} 字节{testResult.first_content_ms != null ? ` · 首段正文 ${(testResult.first_content_ms / 1000).toFixed(1)} 秒` : ""}</p>}
                {testResult.transport?.usage_source === "gateway_estimate" && <p className="mt-2 text-[14px] text-ink-muted">网关返回型号：{testResult.transport.reported_model || "未报告"}。该字段可能回显请求，实际模型尚未核验；Token 为网关估算，输出上限不由网关执行。</p>}
                {testResult.context_check && <div className="mt-3 text-[14px]">
                  <p className="font-semibold">{testResult.context_check.passed ? "本次长度抽样通过" : "本次长度抽样未通过"} · {testResult.context_check.matched}/{testResult.context_check.total} 处信息一致</p>
                  <p className="mt-1 text-ink-muted">{testResult.context_check.scope}</p>
                </div>}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function VisionReviewPanel({ route, connections, onSaved }: {
  route?: VisionRoute;
  connections: ProviderConnection[];
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const visionConnections = connections.filter((item) =>
    item.capabilities.includes("image_input") && item.capabilities.includes("text_generation"));
  const [connectionId, setConnectionId] = useState(
    route?.connection_id && visionConnections.some((item) => item.id === route.connection_id)
      ? route.connection_id
      : visionConnections[0]?.id ?? "",
  );
  const [model, setModel] = useState(route?.model ?? "");
  const [manual, setManual] = useState(false);
  const [catalog, setCatalog] = useState<{ models: string[]; status: "idle" | "loading" | "ready" | "error"; error?: string }>({
    models: [], status: "idle",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = visionConnections.find((item) => item.id === connectionId);
  const codex = selected?.provider === "codex";
  const configured = route?.configured ?? false;

  const catalogRequest = useRef(0);
  const loadVisionModels = useCallback(async (id: string) => {
    const request = ++catalogRequest.current;
    setCatalog({ models: [], status: "loading" });
    try {
      const models = await api.providerModels(id, true);
      if (request === catalogRequest.current) setCatalog({ models: [...new Set(models)], status: "ready" });
    } catch (nextError) {
      if (request === catalogRequest.current) setCatalog({ models: [], status: "error", error: messageOf(nextError) });
    }
  }, []);

  useEffect(() => {
    if (!connectionId) {
      catalogRequest.current += 1;
      return;
    }
    const timer = window.setTimeout(() => { void loadVisionModels(connectionId); }, 0);
    return () => {
      window.clearTimeout(timer);
      catalogRequest.current += 1;
    };
  }, [connectionId, loadVisionModels]);

  function changeConnection(nextId: string) {
    setConnectionId(nextId);
    setModel("");
    setManual(false);
    setError("");
  }

  async function save() {
    setError("");
    if (!connectionId) {
      setError("请选择支持视觉理解的连接。");
      return;
    }
    if (!model && !codex) {
      setError("请选择或输入视觉模型。");
      return;
    }
    setBusy(true);
    try {
      await api.updateVisionRoute("cover_review", {
        connection_id: connectionId, model, max_tokens: route?.max_tokens || 8192,
      });
      toast("视觉审阅设置已保存", "success");
      await onSaved();
    } catch (nextError) {
      setError(messageOf(nextError));
    } finally {
      setBusy(false);
    }
  }

  if (!visionConnections.length) {
    return (
      <section aria-labelledby="vision-review-title" className="mb-8">
        <SectionHeading
          id="vision-review-title"
          title="视觉审阅"
          detail="封面语义评审、独立盲读与海报建议；连接必须支持图像输入。"
        />
        <div className="mt-4 flex min-h-[180px] flex-col items-center justify-center rounded-lg border border-dashed border-[rgba(74,91,133,0.2)] bg-white/50 px-6 text-center">
          <Icon name="eye" className="h-6 w-6 text-ink-muted" />
          <p className="mt-3 text-[14px] font-semibold text-ink-text">请先添加支持视觉理解的连接</p>
          <p className="mt-1 max-w-[520px] text-[11.5px] leading-relaxed text-ink-muted">
            Codex 默认提供视觉理解；OpenAI 或 OpenAI Compatible 连接可在「服务连接」中开启这项能力。DeepSeek 文本模型不会作为视觉审阅连接。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="vision-review-title" className="mb-8">
      <SectionHeading
        id="vision-review-title"
        title="视觉审阅"
        detail="封面语义评审、独立盲读与海报建议；与文本路由、图像生成分开。"
        action={<button type="button" className="btn-primary !rounded-full disabled:opacity-40" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存视觉审阅"}</button>}
      />
      <div className="mt-4 rounded-lg border border-[rgba(74,91,133,0.14)] bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="rounded bg-[#f0f3fa] px-2 py-1 text-[14px] font-medium text-ink-muted">视觉理解</span>
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[14px] font-semibold ${configured ? "bg-[#eef8f2] text-[#267553]" : "bg-[#fff8ee] text-[#9b651e]"}`}>
              <Icon name={configured ? "circle-check" : "circle-alert"} className="h-3 w-3" />{configured ? "已就绪" : "未配置"}
            </span>
          </div>
          <span className="text-[14px] text-ink-muted">
            {configured ? `${route?.connection_name || selected?.name || "已保存连接"}${route?.source === "fallback" ? " · 当前沿用封面指导连接" : ""}` : (route?.error || "选择连接并保存后启用")}
          </span>
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(180px,.8fr)]">
          <FormField label="视觉审阅连接" htmlFor="vision-connection">
            <select id="vision-connection" className={controlClass} value={connectionId} onChange={(event) => changeConnection(event.target.value)}>
              <option value="">选择连接</option>
              {visionConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
            </select>
          </FormField>
          <FormField label="视觉审阅模型" htmlFor="vision-model">
            <select id="vision-model" className={controlClass} value={manual ? "manual" : model} onChange={(event) => {
              if (event.target.value === "manual") { setManual(true); return; }
              setManual(false);
              setModel(event.target.value);
            }}>
              <option value="">{codex ? "Codex 默认模型" : "选择模型"}</option>
              {model && !catalog.models.includes(model) && <option value={model}>{model}（当前 / 自定义）</option>}
              {catalog.models.map((item) => <option key={item} value={item}>{item}</option>)}
              <option value="manual">手动输入…</option>
            </select>
            {manual && <input aria-label="视觉审阅自定义模型" className={`${controlClass} mt-2`} value={model} onChange={(event) => setModel(event.target.value)} placeholder="输入完整模型 ID" />}
          </FormField>
          <FormField label="状态" htmlFor="vision-status">
            <div id="vision-status" className={`flex h-10 items-center gap-2 rounded-lg border px-3 text-[11.5px] font-medium ${configured ? "border-[rgba(38,117,83,0.16)] bg-[#eef8f2] text-[#267553]" : "border-[rgba(155,101,30,0.18)] bg-[#fff8ee] text-[#9b651e]"}`}>
              <Icon name={configured ? "circle-check" : "circle-alert"} className="h-3.5 w-3.5" />
              {configured ? "可用于封面与海报审阅" : "保存后启用"}
            </div>
          </FormField>
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-muted">
          DeepSeek 的默认文本模型不会出现在此列表；如启用其视觉模型，可作为独立视觉连接。视觉审阅未配置或调用失败时，封面保留候选并转人工审核，不会伪造通过；海报建议会明确标记为不可用。
        </p>
        {catalog.status === "error" && <p role="alert" className="mt-2 text-[14px] text-[#9b651e]">模型列表获取失败：{catalog.error}。可手动输入模型 ID。</p>}
        {error && <p role="alert" className="mt-2 text-[14px] text-[#9d334c]">{error}</p>}
      </div>
    </section>
  );
}

function ImageProfilePanel({ profile, connections, onSaved }: {
  profile: ImageModelProfile;
  connections: ProviderConnection[];
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [draft, setDraft] = useState(profile);
  const [busy, setBusy] = useState<"save" | "test" | "">("");
  const [preview, setPreview] = useState<ImageTestResult | null>(null);
  const [catalogs, setCatalogs] = useState<Record<string, {value?: ImageModelCatalog; loading: boolean; error?: string}>>({});
  const live = useRef(true);
  const requested = useRef(new Set<string>());
  const modelDrafts = useRef<Record<string, string>>({[profile.connection_id]: profile.model});
  const imageConnections = connections.filter((item) => item.capabilities.includes("image_generation"));
  const selected = connections.find((item) => item.id === draft.connection_id);
  const codexImageConnection = selected?.provider === "codex";
  const catalog = catalogs[draft.connection_id];
  const imageModelChoices = [...new Set(catalog?.value?.models || [])];
  const savedCodexConnection = connections.find(item => item.id === profile.connection_id)?.provider === "codex";
  const normalizeSavedModel = savedCodexConnection && ["gpt-image-2", "codex-image-auto"].includes(profile.model)
    ? "codex-image-auto" : profile.model.trim();
  const draftModel = codexImageConnection ? "codex-image-auto" : draft.model.trim();
  const dirty = draft.connection_id !== profile.connection_id || draftModel !== normalizeSavedModel
    || (["size", "quality", "output_format", "count", "timeout_seconds"] as const).some(key => draft[key] !== profile[key]);

  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const loadImageModels = useCallback(async (connectionId: string) => {
    setCatalogs(current => ({...current, [connectionId]: {...current[connectionId], loading: true, error: ""}}));
    try {
      const value = await api.providerImageModels(connectionId, true);
      if (live.current) setCatalogs(current => ({...current, [connectionId]: {value, loading: false}}));
    } catch (error) {
      if (live.current) setCatalogs(current => ({...current, [connectionId]: {...current[connectionId], loading: false, error: messageOf(error)}}));
    }
  }, []);
  useEffect(() => {
    if (!draft.connection_id || requested.current.has(draft.connection_id)) return;
    requested.current.add(draft.connection_id);
    void loadImageModels(draft.connection_id);
  }, [draft.connection_id, loadImageModels]);

  function changeConnection(connectionId: string) {
    modelDrafts.current[draft.connection_id] = draft.model;
    const nextCodex = connections.find(item => item.id === connectionId)?.provider === "codex";
    const previous = modelDrafts.current[connectionId];
    setDraft({...draft, connection_id: connectionId, model: nextCodex ? "codex-image-auto"
      : previous && previous !== "codex-image-auto" ? previous : "gpt-image-2.5-sunburst"});
    setPreview(null);
  }

  async function save() {
    setBusy("save");
    try {
      const updated = await api.updateCoverProfile({
        connection_id: draft.connection_id,
        model: draftModel,
        size: draft.size,
        quality: draft.quality,
        output_format: draft.output_format,
        count: draft.count,
        timeout_seconds: draft.timeout_seconds,
      });
      setDraft(updated);
      toast("图像设置已保存", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy("");
    }
  }

  async function testImage() {
    if (dirty || !profile.configured) return;
    const accepted = await confirm({
      title: "生成测试图片",
      message: `确定使用已保存的封面设置，通过 ${savedCodexConnection ? "Codex 内置图片工具（自动选择模型）" : profile.model} 生成一张可能产生费用的测试图片吗？`,
      confirmLabel: "生成测试图片",
    });
    if (!accepted) return;
    setBusy("test");
    try {
      const initial = await api.testCoverProfile();
      const completed = await waitForStudioJob(
        initial,
        Math.max(profile.timeout_seconds * 1000 + 30_000, 210_000),
      );
      const result = jobResult<ImageTestResult>(completed);
      if (!result.data_url) throw new Error("图像测试未返回预览");
      setPreview(result);
      toast("测试图片已生成", "success");
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy("");
    }
  }

  return (
    <section aria-labelledby="image-profile-title">
      <SectionHeading
        id="image-profile-title"
        title="封面图像模型"
        detail={profile.configured ? `${profile.connection_name} · ${savedCodexConnection ? "Codex 内置图片工具（自动选择）" : profile.model}` : profile.error}
        action={<div className="flex gap-2"><button type="button" className="btn-secondary !rounded-full" disabled={!profile.configured || dirty || !!busy} title={dirty ? "请先保存图像设置，再测试已保存的配置" : undefined} onClick={() => void testImage()}>{busy === "test" ? "生成中…" : "测试图片"}</button><button type="button" className="btn-primary !rounded-full" disabled={!selected || !draftModel || !!busy} onClick={() => void save()}>{busy === "save" ? "保存中…" : "保存设置"}</button></div>}
      />
      <p className="mt-3 text-[14px] leading-relaxed text-ink-muted">仅显示支持图像生成的连接。视觉理解模型不能代替出图模型。</p>
      {dirty && <p className="mt-3 text-[14px] text-ink-muted">设置尚未保存。请先保存，再测试当前图像配置。</p>}
      {!imageConnections.length ? (
        <EmptyState icon="image" title="请先添加支持图像生成的连接" />
      ) : (
        <div className="mt-5 grid gap-6">
          <div className="rounded-lg border border-[rgba(74,91,133,0.14)] bg-white p-5 sm:p-6">
            <div className="grid gap-x-5 sm:grid-cols-2">
              <FormField label="服务商连接" htmlFor="image-connection">
                <select id="image-connection" className={controlClass} value={draft.connection_id} onChange={(event) => changeConnection(event.target.value)}>
                  <option value="">选择连接</option>
                  {imageConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
                </select>
              </FormField>
              <FormField label="图像模型" htmlFor="image-model">
                <input id="image-model" className={controlClass} readOnly={codexImageConnection} value={codexImageConnection ? "Codex 自动选择" : draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} list={codexImageConnection ? undefined : "image-models"} placeholder="输入服务商支持的完整模型 ID" aria-describedby="image-model-guidance" />
                <datalist id="image-models">{imageModelChoices.map(model => <option key={model} value={model} />)}</datalist>
              </FormField>
            </div>
            {selected && <div className="mb-4 text-[14px] leading-6 text-ink-muted">
              <div className="flex flex-wrap items-center justify-between gap-2"><span>{catalog?.loading ? "正在读取图片模型目录…"
                : catalog?.value?.source === "unsupported" ? "当前连接不提供图片型号列表"
                : catalog?.value ? `服务商目录返回 ${catalog.value.models.length} 个候选型号` : "尚未读取图片模型目录"}</span>
                <button type="button" className="btn-secondary !h-8 !text-[14px]" disabled={catalog?.loading} onClick={() => void loadImageModels(selected.id)}>拉取图片模型列表</button></div>
              {catalog?.error && <p role="alert" className="mt-2 text-[#9d334c]">图片模型列表获取失败：{catalog.error}。当前填写的型号保持不变。</p>}
              {catalog?.value?.message && <p className="mt-2">{catalog.value.message}</p>}
            </div>}
            <div id="image-model-guidance" className="mb-5 rounded-lg bg-[#f3f4fa] p-3 text-[14px] leading-6 text-ink-muted">
              {codexImageConnection ? <p>当前 Codex 连接使用内置图片工具，由 Codex 自动选择实际图片模型。无法指定 Sunburst 或 Flare；需要精确型号时，请选择支持图像生成的 API 连接。</p> : <>
                <p>GPT Image 2.5：Sunburst 注重成图质量，Flare 注重速度。请使用完整模型 ID。</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary !h-8 !text-[14px]" onClick={() => setDraft({...draft, model: "gpt-image-2.5-sunburst"})}>使用 Sunburst · 推荐</button>
                  <button type="button" className="btn-secondary !h-8 !text-[14px]" onClick={() => setDraft({...draft, model: "gpt-image-2.5-flare"})}>使用 Flare</button>
                </div>
              </>}
              {!codexImageConnection && draft.model.trim() === "gpt-image-2.5" && <p className="mt-2 text-[#9d334c]">gpt-image-2.5 不是官方列出的完整 ID，请选择 gpt-image-2.5-sunburst 或 gpt-image-2.5-flare。</p>}
            </div>
            <div className="border-t border-[rgba(74,91,133,0.11)] pt-5">
              <p className="mb-2 text-[14px] font-medium text-ink-muted">竖版尺寸</p>
              <div className="grid grid-cols-2 gap-2">
                {["1024x1536", "2048x3072"].map((size) => (
                  <button key={size} type="button" aria-label={size} aria-pressed={draft.size === size} onClick={() => setDraft({ ...draft, size })} className={`rounded-lg border px-3 py-3 text-left ${draft.size === size ? "border-[rgba(104,103,234,0.5)] bg-[#f0efff]" : "border-[rgba(74,91,133,0.14)] bg-white"}`}>
                    <span className="block text-[14px] font-semibold text-ink-text">{size}</span><span className="mt-0.5 block text-[14px] text-ink-muted">2:3 封面</span>
                  </button>
                ))}
              </div>
              <FormField label="自定义 2:3 尺寸" htmlFor="image-size" compact><input id="image-size" className={controlClass} value={draft.size} onChange={(event) => setDraft({ ...draft, size: event.target.value })} placeholder="2048x3072" /></FormField>
            </div>
            <fieldset className="mt-1 border-t border-[rgba(74,91,133,0.11)] pt-5">
              <legend className="mb-2 pt-5 text-[14px] font-medium text-ink-muted">质量</legend>
              <div className="grid grid-cols-4 rounded-lg bg-[#f0f3f8] p-1">
                {(["low", "medium", "high", "auto"] as const).map((quality) => <button key={quality} type="button" aria-pressed={draft.quality === quality} onClick={() => setDraft({ ...draft, quality })} className={`rounded-md px-2 py-2 text-[14px] font-semibold capitalize ${draft.quality === quality ? "bg-white text-ink-text shadow-sm" : "text-ink-muted"}`}>{({ low: "低", medium: "中", high: "高", auto: "自动" })[quality]}</button>)}
              </div>
            </fieldset>
            <div className="mt-5 grid gap-x-5 sm:grid-cols-2">
              <FormField label="输出格式" htmlFor="image-format"><select id="image-format" className={controlClass} value={draft.output_format} onChange={(event) => setDraft({ ...draft, output_format: event.target.value as ImageModelProfile["output_format"] })}><option value="jpeg">JPEG</option><option value="png">PNG</option></select></FormField>
              <FormField label="超时时间（秒）" htmlFor="image-timeout"><input id="image-timeout" className={controlClass} type="number" min={1} value={draft.timeout_seconds} onChange={(event) => setDraft({ ...draft, timeout_seconds: Number(event.target.value) })} /></FormField>
            </div>
            <div className="flex items-center justify-between border-t border-[rgba(74,91,133,0.11)] pt-5">
              <div><p className="text-[14px] font-medium text-ink-text">封面候选图</p><p className="mt-0.5 text-[14px] text-ink-muted">每个概念单独发起一次请求</p></div>
              <div className="flex h-10 items-center rounded-lg border border-[rgba(74,91,133,0.16)] bg-white">
                <button type="button" aria-label="减少候选图数量" disabled={draft.count <= 3} onClick={() => setDraft({ ...draft, count: draft.count - 1 })} className="h-full w-10 text-[18px] text-ink-muted disabled:opacity-30">-</button>
                <output className="nums w-10 text-center text-[14px] font-semibold text-ink-text">{draft.count}</output>
                <button type="button" aria-label="增加候选图数量" disabled={draft.count >= 5} onClick={() => setDraft({ ...draft, count: draft.count + 1 })} className="h-full w-10 text-[18px] text-ink-muted disabled:opacity-30">+</button>
              </div>
            </div>
          </div>
          {preview && (
              <figure className="flex flex-wrap items-start gap-5 rounded-2xl border border-[rgba(74,91,133,0.14)] bg-white p-5"><img src={preview.data_url} alt="服务商生成测试图" className="aspect-[2/3] w-60 max-w-full rounded-lg bg-white object-contain shadow-sm" /><figcaption className="space-y-2 py-2 text-[14px] text-ink-muted">
                <p className="font-semibold text-ink-text">测试结果</p>
                <p>{preview.width}×{preview.height}</p>
                <p>请求配置：{(preview.requested_model || preview.model) === "codex-image-auto" ? "Codex 自动选择" : preview.requested_model || preview.model}</p>
                <p>{preview.observed_model ? `服务商报告型号：${preview.observed_model}${preview.model_verified ? "（已核验匹配）" : ""}` : "服务商未返回实际图片型号"}</p>
              </figcaption></figure>
          )}
        </div>
      )}
    </section>
  );
}

function SectionHeading({ id, title, detail, action }: { id: string; title: string; detail?: string; action?: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-4"><div><h2 id={id} className="font-display text-[20px] font-semibold text-ink-text">{title}</h2>{detail && <p className="mt-1 text-[14px] text-ink-muted">{detail}</p>}</div>{action}</div>;
}

function EmptyState({ icon, title, action, onAction }: { icon: "waypoints" | "bot" | "image"; title: string; action?: string; onAction?: () => void }) {
  return <div className="mt-5 flex min-h-[250px] flex-col items-center justify-center rounded-lg border border-dashed border-[rgba(74,91,133,0.2)] bg-white/50 text-center"><Icon name={icon} className="h-6 w-6 text-ink-muted" /><p className="mt-3 text-[14px] font-semibold text-ink-text">{title}</p>{action && onAction && <button type="button" className="btn-secondary mt-4 !rounded-full" onClick={onAction}>{action}</button>}</div>;
}

function FormField({ label, htmlFor, compact = false, children }: { label: string; htmlFor: string; compact?: boolean; children: ReactNode }) {
  return <div className={compact ? "mt-3" : "mb-5"}><label htmlFor={htmlFor} className="mb-1.5 block text-[14px] font-medium text-ink-muted">{label}</label>{children}</div>;
}

function authLabel(authType: string): string {
  if (authType === "codex_session") return "Codex 登录";
  if (authType === "none") return "无需密钥";
  return "API 密钥";
}
