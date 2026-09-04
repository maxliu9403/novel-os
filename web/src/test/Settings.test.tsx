import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Settings from "../routes/Settings";
import { ConfirmProvider } from "../components/Confirm";
import { ToastProvider } from "../components/Toaster";

const fetchMock = vi.fn();

const routeIds = ["default", "architect", "writer", "editor", "guardian", "style", "judge", "cover_director"];

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
      inherits_default: id !== "default",
      effective_connection_id: "primary",
      effective_connection_name: "Primary models",
      effective_model: "story-model",
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

    expect(await screen.findByRole("heading", { name: "Models & providers" })).toBeInTheDocument();
    expect(screen.getByText("Primary models")).toBeInTheDocument();
    expect(screen.getByText("Text model ready")).toBeInTheDocument();
    expect(screen.getByText("Image model ready")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Text routing" })).toBeInTheDocument();
  });

  it("creates a provider connection with explicit capabilities and a write-only key", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Add connection" }));
    await user.selectOptions(screen.getByLabelText("Provider"), "openai_compatible");
    await user.clear(screen.getByLabelText("Connection name"));
    await user.type(screen.getByLabelText("Connection name"), "Shared gateway");
    await user.type(screen.getByLabelText("API base URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API key"), "write-only-secret");
    await user.click(screen.getByRole("button", { name: "Save connection" }));

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

    await user.click(await screen.findByRole("button", { name: "Add connection" }));
    await user.selectOptions(screen.getByLabelText("Provider"), "anthropic");

    expect(screen.getByRole("button", { name: "Image generation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Text generation" })).toBeEnabled();
  });

  it("saves a configurable image model through the image profile", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "Image generation" }));
    const model = screen.getByLabelText("Image model");
    await user.clear(model);
    await user.type(model, "publisher/image-v3");
    await user.click(screen.getByRole("button", { name: "1024x1536" }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

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

    await user.click(await screen.findByRole("tab", { name: "Text routing" }));
    expect(screen.getByLabelText("Writer model")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save routes" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/api/studio/model-routes") && item[1]?.method === "PUT");
      const routes = JSON.parse(String(call?.[1]?.body)).routes;
      expect(routes.find((route: { id: string }) => route.id === "writer")).toMatchObject({ inherits_default: true });
    });
  });

  it("runs a real text response test as a background job", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "Text routing" }));
    await user.click(screen.getByRole("button", { name: "Test response" }));

    expect(await screen.findByText("The connection is working.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/studio/model-routes/default/test"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("polls an asynchronous image test and displays its preview", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("tab", { name: "Image generation" }));
    await user.click(screen.getByRole("button", { name: "Test image" }));
    await user.click(screen.getByRole("button", { name: "Generate test" }));

    expect(await screen.findByRole("img", { name: "Generated provider test" })).toHaveAttribute(
      "src",
      "data:image/png;base64,aW1hZ2U=",
    );
  });
});
