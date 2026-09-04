import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { motion } from "motion/react";
import {
  api,
  type ImageModelProfile,
  type ImageTestResult,
  type JobStatus,
  type ModelCapability,
  type ProviderConnection,
  type ProviderConnectionInput,
  type ProviderTemplate,
  type StudioModelConfiguration,
  type TextModelRoute,
  type TextTestResult,
} from "../api/client";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useConfirm } from "../components/confirmContext";
import { useToast } from "../components/toastContext";

type SettingsTab = "connections" | "text" | "images";

const controlClass =
  "h-10 w-full rounded-lg border border-[rgba(74,91,133,0.18)] bg-white px-3 text-[13px] font-medium text-ink-text shadow-[inset_0_1px_2px_rgba(23,33,63,0.04)] placeholder:text-paper-muted focus:border-[rgba(104,103,234,0.55)] focus:outline-none focus:ring-4 focus:ring-[rgba(104,103,234,0.09)]";

const ROUTE_LABELS: Record<string, { label: string; detail: string }> = {
  default: { label: "Default writing", detail: "Fallback for every text task" },
  architect: { label: "Architect", detail: "Outlines and narrative structure" },
  writer: { label: "Writer", detail: "Chapter drafting" },
  editor: { label: "Editor", detail: "Developmental and line editing" },
  guardian: { label: "Continuity", detail: "Facts, timeline, and consistency" },
  style: { label: "Style", detail: "Voice and prose calibration" },
  judge: { label: "Judge", detail: "Commercial quality evaluation" },
  cover_director: { label: "Cover director", detail: "Visual concept planning" },
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
    if (Date.now() >= deadline) throw new Error("Model test timed out");
    await wait(750);
    job = await api.getJob(job.job_id);
  }
  if (job.status === "error") throw new Error(job.error || "Model test failed");
  return job;
}

function jobResult<T>(job: JobStatus): T {
  if (!job.meta) throw new Error("Model test returned no result");
  return job.meta as T;
}

export default function Settings() {
  const toast = useToast();
  const confirm = useConfirm();
  const [tab, setTab] = useState<SettingsTab>("connections");
  const [configuration, setConfiguration] = useState<StudioModelConfiguration | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ProviderConnection | "new" | null>(null);
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
      title: "Delete connection",
      message: `Delete ${connection.name}? Connections assigned to a route cannot be deleted.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!accepted) return;
    try {
      await api.deleteProvider(connection.id);
      toast("Connection deleted", "success");
      await load();
    } catch (nextError) {
      toast(messageOf(nextError), "error");
    }
  }

  return (
    <Scene quiet>
      <div className="mx-auto min-h-full max-w-[1180px] px-5 py-8 sm:px-8 lg:px-10 lg:py-10">
        <motion.header
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="border-b border-[rgba(74,91,133,0.14)] pb-7"
        >
          <p className="eyebrow">Studio settings</p>
          <div className="flex flex-wrap items-end justify-between gap-5">
            <div>
              <h1 className="font-display text-[30px] font-semibold text-ink-text sm:text-[34px]">
                Models & providers
              </h1>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-[12px] font-medium text-ink-muted">
                <HealthLabel ready={textReady} label="Text model" />
                <HealthLabel ready={imageReady} label="Image model" />
                <span>{configuration?.connections.length ?? 0} connections</span>
              </div>
            </div>
            {configuration?.source === "legacy" && (
              <span className="rounded-full border border-[rgba(200,122,27,0.28)] bg-[#fff8ee] px-3 py-1.5 text-[11px] font-medium text-[#8d5a19]">
                Legacy settings loaded
              </span>
            )}
          </div>
        </motion.header>

        <nav aria-label="Model settings" className="mt-6 flex w-full max-w-[560px] rounded-lg bg-[rgba(74,91,133,0.07)] p-1">
          <TabButton active={tab === "connections"} onClick={() => setTab("connections")} icon="waypoints">
            Connections
          </TabButton>
          <TabButton active={tab === "text"} onClick={() => setTab("text")} icon="bot">
            Text routing
          </TabButton>
          <TabButton active={tab === "images"} onClick={() => setTab("images")} icon="image">
            Image generation
          </TabButton>
        </nav>

        {error && (
          <div role="alert" className="mt-6 rounded-lg border border-[rgba(200,80,100,0.28)] bg-[#fff5f7] px-4 py-3 text-[13px] text-[#9d334c]">
            {error}
          </div>
        )}

        {loading && <div className="py-16 text-[13px] text-ink-muted">Loading model configuration...</div>}

        {configuration && !loading && (
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className="pt-7"
          >
            {tab === "connections" && (
              <ConnectionsPanel
                connections={configuration.connections}
                testingId={testingId}
                onAdd={() => setEditing("new")}
                onEdit={setEditing}
                onTest={(connection) => void testConnection(connection)}
                onDelete={(connection) => void removeConnection(connection)}
              />
            )}
            {tab === "text" && (
              <TextRoutesPanel
                key={configuration.text_routes.map((route) => `${route.id}:${route.connection_id}:${route.model}:${route.inherits_default}`).join("|")}
                routes={configuration.text_routes}
                connections={configuration.connections}
                onSaved={load}
              />
            )}
            {tab === "images" && (
              <ImageProfilePanel
                key={`${configuration.image_profiles.cover.connection_id}:${configuration.image_profiles.cover.model}:${configuration.image_profiles.cover.size}:${configuration.image_profiles.cover.quality}:${configuration.image_profiles.cover.output_format}:${configuration.image_profiles.cover.count}`}
                profile={configuration.image_profiles.cover}
                connections={configuration.connections}
                onSaved={load}
              />
            )}
          </motion.div>
        )}
      </div>

      {configuration && (
        <ProviderEditor
          key={editing === "new" ? "new" : editing?.id ?? "closed"}
          open={editing !== null}
          connection={editing === "new" ? null : editing}
          templates={configuration.templates}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}
    </Scene>
  );
}

function HealthLabel({ ready, label }: { ready: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${ready ? "text-[#267553]" : "text-[#9b651e]"}`}>
      <Icon name={ready ? "circle-check" : "circle-alert"} className="h-3.5 w-3.5" />
      {label} {ready ? "ready" : "needs setup"}
    </span>
  );
}

function TabButton({ active, onClick, icon, children }: {
  active: boolean;
  onClick: () => void;
  icon: "waypoints" | "bot" | "image";
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex min-w-0 flex-1 items-center justify-center gap-2 rounded-md px-2 py-2 text-[12px] font-semibold transition-colors sm:px-4 sm:text-[13px] ${active ? "bg-white text-ink-text shadow-sm" : "text-ink-muted hover:text-ink-text"}`}
    >
      <Icon name={icon} className="hidden h-3.5 w-3.5 sm:block" />
      <span className="truncate">{children}</span>
    </button>
  );
}

function ConnectionsPanel({ connections, testingId, onAdd, onEdit, onTest, onDelete }: {
  connections: ProviderConnection[];
  testingId: string;
  onAdd: () => void;
  onEdit: (connection: ProviderConnection) => void;
  onTest: (connection: ProviderConnection) => void;
  onDelete: (connection: ProviderConnection) => void;
}) {
  return (
    <section aria-labelledby="connections-title">
      <SectionHeading
        id="connections-title"
        title="Provider connections"
        action={<button type="button" onClick={onAdd} className="btn-primary !rounded-lg"><Icon name="plus" className="h-3.5 w-3.5" /> Add connection</button>}
      />
      {connections.length === 0 ? (
        <EmptyState icon="waypoints" title="No provider connections" action="Add connection" onAction={onAdd} />
      ) : (
        <div className="mt-5 overflow-hidden rounded-lg border border-[rgba(74,91,133,0.14)] bg-white">
          {connections.map((connection, index) => (
            <div key={connection.id} className={`grid gap-4 px-4 py-4 sm:grid-cols-[minmax(180px,1fr)_minmax(180px,1fr)_auto] sm:items-center sm:px-5 ${index ? "border-t border-[rgba(74,91,133,0.11)]" : ""}`}>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${connection.status === "ready" ? "bg-[#39a875]" : "bg-[#d18b32]"}`} />
                  <h3 className="truncate text-[14px] font-semibold text-ink-text">{connection.name}</h3>
                </div>
                <p className="mt-1 truncate pl-4 text-[11px] uppercase text-ink-muted">
                  {connection.provider.replaceAll("_", " ")} · {authLabel(connection.auth_type)}
                </p>
              </div>
              <div>
                <div className="flex flex-wrap gap-1.5">
                  {connection.capabilities.map((capability) => (
                    <span key={capability} className="rounded bg-[#f0f3fa] px-2 py-1 text-[10px] font-medium text-ink-muted">
                      {capability === "text_generation" ? "Text" : "Images"}
                    </span>
                  ))}
                </div>
                <p className={`mt-1.5 text-[11px] ${connection.last_test_ok === false ? "text-[#a33b54]" : "text-ink-muted"}`}>
                  {connection.last_tested_at ? connection.last_test_ok ? "Connection verified" : connection.last_test_error : connection.error || "Not tested"}
                </p>
              </div>
              <div className="flex items-center justify-end gap-2">
                <button type="button" className="btn-secondary !rounded-md !px-3 !py-2" disabled={testingId === connection.id} onClick={() => onTest(connection)}>{testingId === connection.id ? "Testing..." : "Test"}</button>
                <button type="button" className="btn-ghost !rounded-md !px-3 !py-2" onClick={() => onEdit(connection)}>Edit</button>
                <button type="button" aria-label={`Delete ${connection.name}`} title="Delete connection" className="flex h-9 w-9 items-center justify-center rounded-md text-ink-muted hover:bg-[#fff1f4] hover:text-[#a33b54]" onClick={() => onDelete(connection)}>
                  <span aria-hidden className="text-[16px] leading-none">×</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ProviderEditor({ open, connection, templates, onClose, onSaved }: {
  open: boolean;
  connection: ProviderConnection | null;
  templates: ProviderTemplate[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const initialTemplate = templates.find((item) => item.id === connection?.provider) ?? templates[0];
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
      toast(connection ? "Connection updated" : "Connection created", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={connection ? "Edit connection" : "Add connection"}>
      <form onSubmit={(event) => void submit(event)}>
        <FormField label="Provider" htmlFor="provider-type">
          <select id="provider-type" className={controlClass} value={provider} disabled={!!connection} onChange={(event) => changeProvider(event.target.value)}>
            {templates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </FormField>
        <FormField label="Connection name" htmlFor="provider-name">
          <input id="provider-name" className={controlClass} value={name} onChange={(event) => setName(event.target.value)} required />
        </FormField>
        {provider !== "codex" && (
          <FormField label="API base URL" htmlFor="provider-base-url">
            <input id="provider-base-url" className={controlClass} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" required />
          </FormField>
        )}
        <fieldset className="mb-5">
          <legend className="mb-2 text-[12px] font-medium text-ink-muted">Capabilities</legend>
          <div className="grid grid-cols-2 gap-2">
            <CapabilityToggle active={capabilities.includes("text_generation")} disabled={!template?.capabilities.includes("text_generation")} label="Text generation" icon="bot" onClick={() => toggleCapability("text_generation")} />
            <CapabilityToggle active={capabilities.includes("image_generation")} disabled={!template?.capabilities.includes("image_generation")} label="Image generation" icon="image" onClick={() => toggleCapability("image_generation")} />
          </div>
        </fieldset>
        {capabilities.includes("image_generation") && provider !== "codex" && (
          <FormField label="Image API base URL (optional override)" htmlFor="provider-image-url">
            <input id="provider-image-url" className={controlClass} value={imageBaseUrl} onChange={(event) => setImageBaseUrl(event.target.value)} placeholder={baseUrl || "Uses API base URL"} />
          </FormField>
        )}
        {template?.auth_type === "api_key" && (
          <FormField label={connection?.has_api_key ? "API key (configured)" : "API key"} htmlFor="provider-api-key">
            <input id="provider-api-key" className={controlClass} type="password" value={apiKey} disabled={clearKey} onChange={(event) => setApiKey(event.target.value)} placeholder={connection?.has_api_key ? "Leave blank to keep existing" : "Required"} autoComplete="off" />
          </FormField>
        )}
        {connection?.has_api_key && template?.auth_type === "api_key" && (
          <label className="mb-5 flex items-center gap-2 text-[12px] text-ink-muted">
            <input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />
            Remove saved API key
          </label>
        )}
        {template?.auth_type === "codex_session" && (
          <div className="mb-5 rounded-lg border border-[rgba(74,91,133,0.13)] bg-[#f7f9fd] px-3 py-3 text-[12px] text-ink-muted">
            Codex credentials remain in the Codex credential store.
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-[rgba(74,91,133,0.12)] pt-4">
          <button type="button" className="btn-ghost !rounded-md" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary !rounded-md" disabled={busy || capabilities.length === 0}>{busy ? "Saving..." : "Save connection"}</button>
        </div>
      </form>
    </Modal>
  );
}

function CapabilityToggle({ active, disabled, label, icon, onClick }: {
  active: boolean;
  disabled: boolean;
  label: string;
  icon: "bot" | "image";
  onClick: () => void;
}) {
  return (
    <button type="button" aria-pressed={active} disabled={disabled} onClick={onClick} className={`flex h-11 items-center gap-2 rounded-lg border px-3 text-left text-[12px] font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${active ? "border-[rgba(104,103,234,0.45)] bg-[#f0efff] text-ink-text" : "border-[rgba(74,91,133,0.14)] bg-white text-ink-muted"}`}>
      <Icon name={icon} className="h-3.5 w-3.5" /> {label}
    </button>
  );
}

function TextRoutesPanel({ routes, connections, onSaved }: {
  routes: TextModelRoute[];
  connections: ProviderConnection[];
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState(routes);
  const [busy, setBusy] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testRouteId, setTestRouteId] = useState("default");
  const [testPrompt, setTestPrompt] = useState("Reply with one short sentence confirming that you can respond.");
  const [testResult, setTestResult] = useState<TextTestResult | null>(null);
  const textConnections = connections.filter((item) => item.capabilities.includes("text_generation"));

  function update(routeId: string, patch: Partial<TextModelRoute>) {
    setDraft((current) => current.map((route) => route.id === routeId ? { ...route, ...patch } : route));
  }

  async function save() {
    setBusy(true);
    try {
      const updated = await api.updateTextRoutes(draft.map(({ id, connection_id, model, max_tokens, inherits_default }) => ({ id, connection_id, model, max_tokens, inherits_default })));
      setDraft(updated);
      toast("Text routes saved", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy(false);
    }
  }

  async function testResponse() {
    setTestBusy(true);
    setTestResult(null);
    try {
      const initial = await api.testTextRoute(testRouteId, testPrompt.trim());
      const completed = await waitForStudioJob(initial, 150_000);
      const result = jobResult<TextTestResult>(completed);
      if (!result.reply) throw new Error("The model returned an empty response");
      setTestResult(result);
      toast("Text response received", "success");
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <section aria-labelledby="text-routes-title">
      <SectionHeading id="text-routes-title" title="Text model routing" action={<button type="button" className="btn-primary !rounded-lg" disabled={busy || !textConnections.length} onClick={() => void save()}>{busy ? "Saving..." : "Save routes"}</button>} />
      {!textConnections.length ? (
        <EmptyState icon="bot" title="Add a text-capable connection first" />
      ) : (
        <>
          <div className="mt-5 overflow-hidden rounded-lg border border-[rgba(74,91,133,0.14)] bg-white">
            {draft.map((route, index) => {
            const meta = ROUTE_LABELS[route.id] ?? { label: route.id, detail: "" };
            const inherited = route.id !== "default" && route.inherits_default;
            const selected = connections.find((item) => item.id === route.connection_id);
            const models = selected?.discovered_models ?? [];
            return (
              <div key={route.id} className={`grid gap-4 px-4 py-4 lg:grid-cols-[220px_minmax(180px,1fr)_minmax(180px,1fr)_120px] lg:items-center lg:px-5 ${index ? "border-t border-[rgba(74,91,133,0.11)]" : ""}`}>
                <div>
                  <h3 className="text-[13px] font-semibold text-ink-text">{meta.label}</h3>
                  <p className="mt-0.5 text-[11px] text-ink-muted">{meta.detail}</p>
                </div>
                <select aria-label={`${meta.label} connection`} className={controlClass} value={inherited ? "" : route.connection_id} disabled={inherited} onChange={(event) => update(route.id, { connection_id: event.target.value })}>
                  <option value="">Select connection</option>
                  {textConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
                </select>
                <div>
                  <input aria-label={`${meta.label} model`} className={controlClass} value={inherited ? route.effective_model : route.model} disabled={inherited} onChange={(event) => update(route.id, { model: event.target.value })} placeholder={selected?.provider === "codex" ? "Codex default" : "Model id"} list={`models-${route.id}`} />
                  <datalist id={`models-${route.id}`}>{models.map((model) => <option key={model} value={model} />)}</datalist>
                </div>
                {route.id === "default" ? (
                  <span className={`text-[11px] font-medium ${route.configured ? "text-[#267553]" : "text-[#9b651e]"}`}>{route.configured ? "Ready" : "Incomplete"}</span>
                ) : (
                  <label className="flex items-center gap-2 text-[12px] font-medium text-ink-muted"><input type="checkbox" checked={inherited} onChange={(event) => update(route.id, { inherits_default: event.target.checked })} /> Use default</label>
                )}
              </div>
            );
            })}
          </div>
          <div className="mt-6 border-t border-[rgba(74,91,133,0.12)] pt-5">
            <h3 className="text-[13px] font-semibold text-ink-text">Text response test</h3>
            <div className="mt-3 grid gap-3 lg:grid-cols-[180px_minmax(0,1fr)_auto] lg:items-end">
              <FormField label="Saved route" htmlFor="text-test-route" compact>
                <select id="text-test-route" className={controlClass} value={testRouteId} onChange={(event) => setTestRouteId(event.target.value)}>
                  {routes.map((route) => <option key={route.id} value={route.id}>{ROUTE_LABELS[route.id]?.label ?? route.id}</option>)}
                </select>
              </FormField>
              <FormField label="Test prompt" htmlFor="text-test-prompt" compact>
                <input id="text-test-prompt" className={controlClass} value={testPrompt} maxLength={4000} onChange={(event) => setTestPrompt(event.target.value)} />
              </FormField>
              <button type="button" className="btn-secondary !h-10 !rounded-lg" disabled={testBusy || !testPrompt.trim()} onClick={() => void testResponse()}>
                <Icon name="bot" className="mr-2 h-3.5 w-3.5" />{testBusy ? "Waiting..." : "Test response"}
              </button>
            </div>
            {testResult && (
              <div className="mt-4 border-l-2 border-[rgba(104,103,234,0.55)] bg-[#f7f8fc] px-4 py-3">
                <p className="whitespace-pre-wrap text-[13px] leading-6 text-ink-text">{testResult.reply}</p>
                <p className="mt-2 text-[10px] text-ink-muted">{testResult.provider} · {testResult.model || "default model"} · {testResult.duration_ms} ms</p>
              </div>
            )}
          </div>
        </>
      )}
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
  const imageConnections = connections.filter((item) => item.capabilities.includes("image_generation"));
  const selected = connections.find((item) => item.id === draft.connection_id);

  async function save() {
    setBusy("save");
    try {
      const updated = await api.updateCoverProfile({
        connection_id: draft.connection_id,
        model: draft.model.trim(),
        size: draft.size,
        quality: draft.quality,
        output_format: draft.output_format,
        count: draft.count,
        timeout_seconds: draft.timeout_seconds,
      });
      setDraft(updated);
      toast("Image settings saved", "success");
      await onSaved();
    } catch (error) {
      toast(messageOf(error), "error");
    } finally {
      setBusy("");
    }
  }

  async function testImage() {
    const accepted = await confirm({
      title: "Generate test image",
      message: `Generate one billable test image with ${draft.model} using the saved cover settings?`,
      confirmLabel: "Generate test",
    });
    if (!accepted) return;
    setBusy("test");
    try {
      const initial = await api.testCoverProfile();
      const completed = await waitForStudioJob(
        initial,
        Math.max(draft.timeout_seconds * 1000 + 30_000, 210_000),
      );
      const result = jobResult<ImageTestResult>(completed);
      if (!result.data_url) throw new Error("Image test returned no preview");
      setPreview(result);
      toast("Test image generated", "success");
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
        title="Cover image model"
        detail={profile.configured ? `${profile.connection_name} · ${profile.model}` : profile.error}
        action={<div className="flex gap-2"><button type="button" className="btn-secondary !rounded-lg" disabled={!profile.configured || !!busy} onClick={() => void testImage()}>{busy === "test" ? "Generating..." : "Test image"}</button><button type="button" className="btn-primary !rounded-lg" disabled={!imageConnections.length || !!busy} onClick={() => void save()}>{busy === "save" ? "Saving..." : "Save settings"}</button></div>}
      />
      {!imageConnections.length ? (
        <EmptyState icon="image" title="Add an image-capable connection first" />
      ) : (
        <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="rounded-lg border border-[rgba(74,91,133,0.14)] bg-white p-5 sm:p-6">
            <div className="grid gap-x-5 sm:grid-cols-2">
              <FormField label="Provider connection" htmlFor="image-connection">
                <select id="image-connection" className={controlClass} value={draft.connection_id} onChange={(event) => setDraft({ ...draft, connection_id: event.target.value })}>
                  <option value="">Select connection</option>
                  {imageConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
                </select>
              </FormField>
              <FormField label="Image model" htmlFor="image-model">
                <input id="image-model" className={controlClass} value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} list="image-models" placeholder="gpt-image-2" />
                <datalist id="image-models">{selected?.discovered_models.map((model) => <option key={model} value={model} />)}</datalist>
              </FormField>
            </div>
            <div className="border-t border-[rgba(74,91,133,0.11)] pt-5">
              <p className="mb-2 text-[12px] font-medium text-ink-muted">Portrait size</p>
              <div className="grid grid-cols-2 gap-2">
                {["1024x1536", "2048x3072"].map((size) => (
                  <button key={size} type="button" aria-label={size} aria-pressed={draft.size === size} onClick={() => setDraft({ ...draft, size })} className={`rounded-lg border px-3 py-3 text-left ${draft.size === size ? "border-[rgba(104,103,234,0.5)] bg-[#f0efff]" : "border-[rgba(74,91,133,0.14)] bg-white"}`}>
                    <span className="block text-[13px] font-semibold text-ink-text">{size}</span><span className="mt-0.5 block text-[10px] text-ink-muted">2:3 cover</span>
                  </button>
                ))}
              </div>
              <FormField label="Custom 2:3 size" htmlFor="image-size" compact><input id="image-size" className={controlClass} value={draft.size} onChange={(event) => setDraft({ ...draft, size: event.target.value })} placeholder="2048x3072" /></FormField>
            </div>
            <fieldset className="mt-1 border-t border-[rgba(74,91,133,0.11)] pt-5">
              <legend className="mb-2 pt-5 text-[12px] font-medium text-ink-muted">Quality</legend>
              <div className="grid grid-cols-4 rounded-lg bg-[#f0f3f8] p-1">
                {(["low", "medium", "high", "auto"] as const).map((quality) => <button key={quality} type="button" aria-pressed={draft.quality === quality} onClick={() => setDraft({ ...draft, quality })} className={`rounded-md px-2 py-2 text-[11px] font-semibold capitalize ${draft.quality === quality ? "bg-white text-ink-text shadow-sm" : "text-ink-muted"}`}>{quality}</button>)}
              </div>
            </fieldset>
            <div className="mt-5 grid gap-x-5 sm:grid-cols-2">
              <FormField label="Output format" htmlFor="image-format"><select id="image-format" className={controlClass} value={draft.output_format} onChange={(event) => setDraft({ ...draft, output_format: event.target.value as ImageModelProfile["output_format"] })}><option value="jpeg">JPEG</option><option value="png">PNG</option></select></FormField>
              <FormField label="Timeout seconds" htmlFor="image-timeout"><input id="image-timeout" className={controlClass} type="number" min={1} value={draft.timeout_seconds} onChange={(event) => setDraft({ ...draft, timeout_seconds: Number(event.target.value) })} /></FormField>
            </div>
            <div className="flex items-center justify-between border-t border-[rgba(74,91,133,0.11)] pt-5">
              <div><p className="text-[12px] font-medium text-ink-text">Cover candidates</p><p className="mt-0.5 text-[11px] text-ink-muted">One independent request per concept</p></div>
              <div className="flex h-10 items-center rounded-lg border border-[rgba(74,91,133,0.16)] bg-white">
                <button type="button" aria-label="Decrease candidates" disabled={draft.count <= 3} onClick={() => setDraft({ ...draft, count: draft.count - 1 })} className="h-full w-10 text-[18px] text-ink-muted disabled:opacity-30">-</button>
                <output className="nums w-10 text-center text-[14px] font-semibold text-ink-text">{draft.count}</output>
                <button type="button" aria-label="Increase candidates" disabled={draft.count >= 5} onClick={() => setDraft({ ...draft, count: draft.count + 1 })} className="h-full w-10 text-[18px] text-ink-muted disabled:opacity-30">+</button>
              </div>
            </div>
          </div>
          <aside className="min-h-[360px] rounded-lg border border-[rgba(74,91,133,0.14)] bg-[#e8edf6] p-3">
            {preview ? (
              <figure><img src={preview.data_url} alt="Generated provider test" className="aspect-[2/3] w-full rounded-md bg-white object-cover shadow-sm" /><figcaption className="px-1 pb-1 pt-3 text-[11px] text-ink-muted">{preview.model} · {preview.width}×{preview.height}</figcaption></figure>
            ) : (
              <div className="flex h-full min-h-[332px] flex-col items-center justify-center text-center text-ink-muted"><Icon name="image" className="h-6 w-6" /><p className="mt-3 text-[12px] font-medium">Test image preview</p></div>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

function SectionHeading({ id, title, detail, action }: { id: string; title: string; detail?: string; action?: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-4"><div><h2 id={id} className="font-display text-[20px] font-semibold text-ink-text">{title}</h2>{detail && <p className="mt-1 text-[12px] text-ink-muted">{detail}</p>}</div>{action}</div>;
}

function EmptyState({ icon, title, action, onAction }: { icon: "waypoints" | "bot" | "image"; title: string; action?: string; onAction?: () => void }) {
  return <div className="mt-5 flex min-h-[250px] flex-col items-center justify-center rounded-lg border border-dashed border-[rgba(74,91,133,0.2)] bg-white/50 text-center"><Icon name={icon} className="h-6 w-6 text-ink-muted" /><p className="mt-3 text-[13px] font-semibold text-ink-text">{title}</p>{action && onAction && <button type="button" className="btn-secondary mt-4 !rounded-md" onClick={onAction}>{action}</button>}</div>;
}

function FormField({ label, htmlFor, compact = false, children }: { label: string; htmlFor: string; compact?: boolean; children: ReactNode }) {
  return <div className={compact ? "mt-3" : "mb-5"}><label htmlFor={htmlFor} className="mb-1.5 block text-[12px] font-medium text-ink-muted">{label}</label>{children}</div>;
}

function authLabel(authType: string): string {
  if (authType === "codex_session") return "Codex login";
  if (authType === "none") return "No key";
  return "API key";
}
