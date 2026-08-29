import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Settings from "../routes/Settings";
import { ToastProvider } from "../components/Toaster";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/studio/llm")) {
      return Promise.resolve(new Response(JSON.stringify({
        configured: true,
        provider: "openai_compatible",
        model: "gpt-5.6-sol",
        preset: null,
        mature_capable: false,
        error: null,
        presets: [],
        onboarding_completed: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (path.endsWith("/api/studio/cover")) {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      return Promise.resolve(new Response(JSON.stringify({
        configured: Boolean(body.api_key) || init?.method !== "PUT",
        has_api_key: true,
        base_url: body.base_url || "https://sub2api.example/v1",
        model: body.model || "gpt-image-2",
        size: body.size || "2048x3072",
        quality: body.quality || "high",
        output_format: body.output_format || "webp",
        count: body.count || 4,
        timeout_seconds: body.timeout_seconds || 180,
        inherits_base_url: false,
        inherits_api_key: false,
        error: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response("Not found", { status: 404 }));
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Studio cover settings", () => {
  it("shows the independent image model and exact 2K output", async () => {
    render(<MemoryRouter><ToastProvider><Settings /></ToastProvider></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Cover generation" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("gpt-image-2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2048x3072")).toBeInTheDocument();
    expect(screen.getByText("Cover model ready")).toBeInTheDocument();
  });

  it("saves a cover key without putting it back in the input", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider><Settings /></ToastProvider></MemoryRouter>);

    const key = await screen.findByLabelText("Cover API key");
    await user.type(key, "new-cover-secret");
    await user.click(screen.getByRole("button", { name: "Save cover settings" }));

    await waitFor(() => expect(key).toHaveValue(""));
    const put = fetchMock.mock.calls.find((call) => (
      String(call[0]).endsWith("/api/studio/cover") && call[1]?.method === "PUT"
    ));
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ api_key: "new-cover-secret" });
  });
});
