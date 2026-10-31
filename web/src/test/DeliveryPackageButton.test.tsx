import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import DeliveryPackageButton from "../components/DeliveryPackageButton";
import { posterApi } from "../api/poster";

beforeEach(() => { vi.spyOn(globalThis, "fetch"); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test("missing or unknown package navigates to delivery and never builds automatically", () => {
  render(<MemoryRouter><DeliveryPackageButton projectId="book" /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "查看交付" })).toHaveAttribute("href", "/projects/book?tab=delivery");
  expect(fetch).not.toHaveBeenCalled();
});
test("pending disables download; stale rebuild is explicit and updates the action", async () => {
  const build = vi.spyOn(posterApi, "rebuildDelivery").mockResolvedValue({ delivery: { status: "ready" } });
  const { rerender } = render(<MemoryRouter><DeliveryPackageButton projectId="book" delivery={{ status: "pending" }} /></MemoryRouter>);
  expect(screen.getByRole("button", { name: "交付包更新中" })).toBeDisabled();
  rerender(<MemoryRouter><DeliveryPackageButton projectId="book" delivery={{ status: "stale", retryable: true }} /></MemoryRouter>);
  expect(build).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "更新交付包" }));
  expect(await screen.findByRole("button", { name: "导出交付包" })).toBeEnabled();
  expect(build).toHaveBeenCalledExactlyOnceWith("book");
});
test("JSON error responses never get downloaded as ZIP", async () => {
  vi.mocked(fetch).mockResolvedValue(new Response('{"detail":"stale"}', { status: 409, headers: { "Content-Type": "application/json" } }));
  const changed = vi.fn();
  render(<MemoryRouter><DeliveryPackageButton projectId="book" delivery={{ status: "ready" }} onChanged={changed} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "导出交付包" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("交付包已发生变化");
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
});
test("validates payload signature and exports with a ZIP filename", async () => {
  const create = vi.fn(() => "blob:test");
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  vi.mocked(fetch).mockResolvedValue(new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1]), { headers: { "Content-Type": "application/zip" } }));
  render(<MemoryRouter><DeliveryPackageButton projectId="book" delivery={{ status: "ready" }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "导出交付包" }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(click.mock.instances[0]).toHaveAttribute("download", "book-package.zip");
  expect(create).toHaveBeenCalledOnce();
});
test("a successful HTML response is rejected instead of saving a fake ZIP", async () => {
  vi.mocked(fetch).mockResolvedValue(new Response("<html>Proxy error</html>", { headers: { "Content-Type": "text/html" } }));
  render(<MemoryRouter><DeliveryPackageButton projectId="book" delivery={{ status: "ready" }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "导出交付包" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("未返回有效 ZIP");
});
