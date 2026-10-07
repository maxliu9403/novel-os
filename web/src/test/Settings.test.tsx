import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Settings from "../routes/Settings";
import { ConfirmProvider } from "../components/Confirm";
import { ToastProvider } from "../components/Toaster";

const fetchMock = vi.fn();

const routeIds = ["default", "workshop", "architect", "writer", "editor", "guardian", "style", "judge", "cover_director"];

function configuration() {
  return {
    schema_version: 2,
    source: "v2",
    templates: [
      {
        id: "codex", label: "Codex", auth_type: "codex_session",
        default_base_url: "", capabilities: ["text_generation", "image_generation"],
        requires_api_key: false, model_placeholder: "Use Codex default",
      },
      {
        id: "anthropic", label: "Anthropic", auth_type: "api_key",
        default_base_url: "https://api.anthropic.com/v1", capabilities: ["text_generation"],
        requires_api_key: true, model_placeholder: "claude-sonnet-4-6",
      },
      {
        id: "openai_compatible", label: "OpenAI Compatible", auth_type: "api_key",
        default_base_url: "", capabilities: ["text_generation", "image_generation"],
        requires_api_key: true, model_placeholder: "Model id",
      },
    ],
    connections: [
      {
        id: "primary", name: "Primary models", provider: "openai_compatible",
        auth_type: "api_key", base_url: "https://models.example/v1", image_base_url: "",
        capabilities: ["text_generation", "image_generation"], has_api_key: true,
        status: "ready", error: "", last_tested_at: "", last_test_ok: null,
        last_test_error: "", discovered_models: ["story-model", "gpt-image-2"],
      },
    ],
    text_routes: routeIds.map((id) => ({
      id,
      connection_id: id === "default" ? "primary" : "",
      model: id === "default" ? "story-model" : "",
      max_tokens: 8192,
      reasoning_effort: id === "cover_director" ? "medium" : "",
      inherits_default: id !== "default",
      timeout_seconds: null,
      effective_timeout_seconds: id === "workshop" ? 900 : null,
      effective_connection_id: "primary",
      effective_connection_name: "Primary models",
      effective_model: "story-model",
      effective_reasoning_effort: id === "cover_director" ? "medium" : "",
      effective_source: id === "default" ? "default" : "default",
      configured: true,
    })),
    image_profiles: {
      cover: {
        id: "cover", connection_id: "primary", connection_name: "Primary models",
        model: "gpt-image-2", size: "2048x3072", quality: "high",
        output_format: "jpeg", count: 4, timeout_seconds: 180,
        configured: true, error: "",
      },
    },
  };
}

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/studio/models")) return response(configuration());
    if (path.endsWith("/api/studio/providers/primary/models?refresh=true")) return response(["story-model", "planning-model"]);
    if (path.endsWith("/api/studio/providers") && init?.method === "POST") {
      return response({ ...configuration().connections[0], id: "new-provider" }, 201);
    }
    if (path.endsWith("/api/studio/model-routes") && init?.method === "PUT") {
      return response(configuration().text_routes);
    }
    if (path.endsWith("/api/studio/model-routes/default/test") && init?.method === "POST") {
      return response({ job_id: "text-test-job", kind: "studio_text_test", status: "running", error: null }, 202);
    }
    if (path.endsWith("/api/jobs/text-test-job")) {
      return response({
        job_id: "text-test-job", kind: "studio_text_test", status: "done", error: null,
        meta: { route_id: "default", provider: "openai_compatible", model: "story-model", reply: "The connection is working.", duration_ms: 820 },
      });
    }
    if (path.endsWith("/api/studio/image-profiles/cover") && init?.method === "PUT") {
      return response(configuration().image_profiles.cover);
    }
    if (path.endsWith("/api/studio/image-profiles/cover/test") && init?.method === "POST") {
      return response({ job_id: "image-test-job", kind: "studio_image_test", status: "running", error: null }, 202);
    }
    if (path.endsWith("/api/jobs/image-test-job")) {
      return response({
        job_id: "image-test-job", kind: "studio_image_test", status: "done", error: null,
        meta: { ok: true, data_url: "data:image/png;base64,aW1hZ2U=", width: 1024, height: 1536, model: "gpt-image-2", request_id: "request-1" },
      });
    }
    return response({ detail: "Not found" }, 404);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function renderSettings() {
  return render(
    <MemoryRouter>
      <ToastProvider><ConfirmProvider><Settings /></ConfirmProvider></ToastProvider>
    </MemoryRouter>,
  );
}

describe("model provider settings", () => {
  it("shows provider connections and capability health", async () => {
    renderSettings();

    expect(await screen.findByRole("heading", { name: "模型与服务商" })).toBeInTheDocument();
    expect(screen.getByText("Primary models")).toBeInTheDocument();
    expect(screen.getByText("文本模型已就绪")).toBeInTheDocument();
    expect(screen.getByText("图像模型已就绪")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "文本路由" })).toBeInTheDocument();
  });

  it("creates a provider connection with explicit capabilities and a write-only key", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "添加连接" }));
    await user.selectOptions(screen.getByLabelText("服务商"), "openai_compatible");
    await user.clear(screen.getByLabelText("连接名称"));
    await user.type(screen.getByLabelText("连接名称"), "Shared gateway");
    await user.type(screen.getByLabelText("API 基础地址"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API 密钥"), "write-only-secret");
    await user.click(screen.getByRole("button", { name: "保存连接" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/providers") && item[1]?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        name: "Shared gateway",
        capabilities: ["text_generation", "image_generation"],
        secret_action: "replace",
        api_key: "write-only-secret",
      });
    });
    await waitFor(() => expect(screen.queryByDisplayValue("write-only-secret")).not.toBeInTheDocument());
  });

  it("disables capabilities that the selected provider does not support", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "添加连接" }));
    await user.selectOptions(screen.getByLabelText("服务商"), "anthropic");

    expect(screen.getByRole("button", { name: "图像生成" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "文本生成" })).toBeEnabled();
  });

  it("saves a configurable image model through the image profile", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "图像生成" }));
    const model = screen.getByLabelText("图像模型");
    await user.clear(model);
    await user.type(model, "publisher/image-v3");
    await user.click(screen.getByRole("button", { name: "1024x1536" }));
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/image-profiles/cover") && item[1]?.method === "PUT");
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        connection_id: "primary",
        model: "publisher/image-v3",
        size: "1024x1536",
      });
    });
  });

  it("keeps specialist text routes inherited from the default", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(screen.getByLabelText("执笔者模型")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存路由" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "writer")).toMatchObject({ inherits_default: true });
    });
  });

  it("saves Cover Director reasoning separately while its model stays inherited", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(screen.getByLabelText("封面指导模型")).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("封面指导推理强度"), "high");
    await user.click(screen.getByRole("button", { name: "保存路由" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "cover_director")).toMatchObject({
        inherits_default: true,
        reasoning_effort: "high",
      });
    });
  });

  it("configures a separate workshop model without changing the architect or writer routes", async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(screen.getByLabelText("头脑风暴模型")).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "头脑风暴使用默认模型" }));
    await user.selectOptions(screen.getByLabelText("头脑风暴连接"), "primary");
    await user.selectOptions(screen.getByLabelText("头脑风暴模型"), "manual");
    await user.type(screen.getByLabelText("头脑风暴自定义模型"), "planning-model");
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "workshop")).toMatchObject({
        inherits_default: false, model: "planning-model", connection_id: "primary",
      });
      for (const id of ["architect", "writer"]) {
        expect(routes.find((route: { id: string }) => route.id === id)).toMatchObject({ inherits_default: true, model: "" });
      }
      expect(routes.find((route: { id: string }) => route.id === "default")).toMatchObject({ model: "story-model" });
    });
  });

  it("loads models from the connection API once for inherited routes and saves an actual dropdown choice", async () => {
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => String(input).endsWith("/api/studio/providers/primary/models?refresh=true")
      ? response(["fresh-api-model", "another-api-model"]) : baseFetch(input, init));
    const user = userEvent.setup();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    const model = screen.getByRole("combobox", { name: "默认写作模型模型" });
    await within(model).findByRole("option", { name: "fresh-api-model" });
    expect(within(model).getByRole("option", { name: "story-model（当前 / 自定义）" })).toBeInTheDocument();
    expect(model).toHaveValue("model:story-model");
    expect(fetchMock.mock.calls.filter((call) => String(call[0]).includes("/providers/primary/models"))).toHaveLength(1);
    await user.selectOptions(model, "model:fresh-api-model");
    expect(screen.getByLabelText("头脑风暴模型")).toHaveValue("model:fresh-api-model");
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      expect(JSON.parse(String(call?.[1]?.body)).routes.find((route: { id: string }) => route.id === "default")).toMatchObject({ model: "fresh-api-model" });
    });
  });

  it("keeps custom models through discovery failure and refresh without replacing unsaved manual input", async () => {
    let attempts = 0;
    const config = configuration();
    config.text_routes[0].model = "private-model";
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/studio/models")) return response(config);
      if (String(input).endsWith("/api/studio/providers/primary/models?refresh=true")) return ++attempts === 1 ? response({ detail: "Discovery is unavailable" }, 502) : response(["public-model"]);
      return baseFetch(input, init);
    });
    const user = userEvent.setup();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Discovery is unavailable");
    const model = screen.getByLabelText("默认写作模型模型");
    expect(model).toHaveValue("model:private-model");
    await user.selectOptions(model, "manual");
    const custom = screen.getByLabelText("默认写作模型自定义模型");
    await user.clear(custom);
    await user.type(custom, "my/manual-model");
    await user.click(screen.getByRole("button", { name: "刷新 Primary models 的模型列表" }));
    await within(model).findByRole("option", { name: "public-model" });
    expect(custom).toHaveValue("my/manual-model");
    expect(screen.queryByText("Discovery is unavailable")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      expect(JSON.parse(String(call?.[1]?.body)).routes.find((route: { id: string }) => route.id === "default")).toMatchObject({ model: "my/manual-model" });
    });
  });

  it("does not mix a late model response into the newly selected connection", async () => {
    const config = configuration();
    config.connections.push({ ...config.connections[0], id: "secondary", name: "Second connection", discovered_models: [] });
    let resolvePrimary!: (result: Response) => void;
    const primaryReply = new Promise<Response>((resolve) => { resolvePrimary = resolve; });
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/studio/models")) return response(config);
      if (String(input).endsWith("/api/studio/providers/primary/models?refresh=true")) return primaryReply;
      if (String(input).endsWith("/api/studio/providers/secondary/models?refresh=true")) return response(["second-connection-model"]);
      return baseFetch(input, init);
    });
    const user = userEvent.setup();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    await user.selectOptions(screen.getByLabelText("默认写作模型连接"), "secondary");
    const model = screen.getByLabelText("默认写作模型模型");
    await within(model).findByRole("option", { name: "second-connection-model" });
    await act(async () => { resolvePrimary(new Response(JSON.stringify(["late-primary-model"]), { status: 200 })); });
    expect(within(model).queryByRole("option", { name: "late-primary-model" })).not.toBeInTheDocument();
    expect(within(model).getByRole("option", { name: "second-connection-model" })).toBeInTheDocument();
    expect(model).toHaveValue("model:story-model");
    expect(fetchMock.mock.calls.filter((call) => String(call[0]).includes("/providers/secondary/models"))).toHaveLength(1);
    await user.selectOptions(model, "model:");
    expect(screen.getByLabelText("头脑风暴模型")).toHaveValue("model:");
    await user.selectOptions(screen.getByLabelText("默认写作模型连接"), "");
    expect(within(model).queryByRole("option", { name: "second-connection-model" })).not.toBeInTheDocument();
  });

  it("saves workshop minutes independently of inherited models and restores them after reopening", async () => {
    let savedSeconds = 900;
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const config = configuration();
      config.text_routes = config.text_routes.map((route) => route.id === "workshop" ? { ...route, effective_timeout_seconds: savedSeconds } : route);
      if (String(input).endsWith("/api/studio/models")) return response(config);
      if (String(input).endsWith("/api/studio/model-routes") && init?.method === "PUT") {
        const routes = JSON.parse(String(init.body)).routes;
        savedSeconds = routes.find((route: { id: string }) => route.id === "workshop").timeout_seconds;
        return response(config.text_routes.map((route) => route.id === "workshop" ? { ...route, effective_timeout_seconds: savedSeconds } : route));
      }
      return baseFetch(input, init);
    });
    const user = userEvent.setup();
    const currentView = renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    const timeout = screen.getByLabelText("头脑风暴请求超时（分钟）");
    expect(timeout).toHaveValue(15);
    expect(timeout).toBeEnabled();
    expect(screen.getByLabelText("头脑风暴模型")).toBeDisabled();
    await user.clear(timeout);
    await user.type(timeout, "31");
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    expect(await screen.findByText("头脑风暴超时请填写 1–30 分钟")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(false);
    await user.clear(timeout);
    await user.type(timeout, "20");
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "workshop")).toMatchObject({ timeout_seconds: 1200, inherits_default: true });
      for (const id of ["default", "architect", "writer"]) {
        expect(routes.find((route: { id: string }) => route.id === id)).not.toHaveProperty("timeout_seconds");
      }
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "保存路由" })).toBeEnabled());
    currentView.unmount();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(screen.getByLabelText("头脑风暴请求超时（分钟）")).toHaveValue(20);
    expect(screen.getByLabelText("头脑风暴模型")).toBeDisabled();
  });

  it("preserves an unedited legacy timeout below one minute while saving other route settings", async () => {
    const config = configuration();
    config.text_routes = config.text_routes.map((route) => route.id === "workshop" ? { ...route, effective_timeout_seconds: 30 } : route);
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/studio/models")) return response(config);
      if (String(input).endsWith("/api/studio/model-routes") && init?.method === "PUT") return response(config.text_routes);
      return baseFetch(input, init);
    });
    const user = userEvent.setup();
    renderSettings();
    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    expect(screen.getByLabelText("头脑风暴请求超时（分钟）")).toHaveValue(0.5);
    await user.selectOptions(screen.getByLabelText("封面指导推理强度"), "high");
    await user.click(screen.getByRole("button", { name: "保存路由" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "workshop")).not.toHaveProperty("timeout_seconds");
      expect(routes.find((route: { id: string }) => route.id === "cover_director")).toMatchObject({ reasoning_effort: "high" });
    });
    expect(screen.queryByText("头脑风暴超时请填写 1–30 分钟")).not.toBeInTheDocument();
    expect(screen.getByLabelText("头脑风暴请求超时（分钟）")).toHaveValue(0.5);
  });

  it("runs a real text response test as a background job", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "文本路由" }));
    await user.click(screen.getByRole("button", { name: "测试响应" }));

    expect(await screen.findByText("The connection is working.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/studio/model-routes/default/test"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("polls an asynchronous image test and displays its preview", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "图像生成" }));
    await user.click(screen.getByRole("button", { name: "测试图片" }));
    await user.click(screen.getByRole("button", { name: "生成测试图片" }));

    expect(await screen.findByRole("img", { name: "服务商生成测试图" })).toHaveAttribute(
      "src",
      "data:image/png;base64,aW1hZ2U=",
    );
  });
});
