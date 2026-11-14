import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import CommandPalette from "../components/CommandPalette";
import { api } from "../api/client";
import { libraryResource } from "../lib/libraryResource";

beforeEach(() => {
  libraryResource.reset();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(api, "libraryContext").mockResolvedValue({ scope: "test-library" });
});
afterEach(() => { libraryResource.reset(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("queries lightweight navigation for a title beyond the loaded library page", async () => {
  const legacy = vi.spyOn(api, "projects");
  const navigation = vi.spyOn(api, "libraryNavigation").mockImplementation(async (q) => ({
    items: q ? [{ id: "book-900", title: "Distant Star", genre: "Sci-Fi" }] : [],
    scope: "test-library", collection_revision: "1",
  }));
  render(<MemoryRouter><CommandPalette /></MemoryRouter>);
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  const search = await screen.findByRole("combobox");
  await waitFor(() => expect(navigation).toHaveBeenCalledTimes(1));
  fireEvent.change(search, { target: { value: "Distant" } });
  expect(await screen.findByText("Distant Star")).toBeInTheDocument();
  expect(navigation.mock.calls[1][0]).toBe("Distant");
  expect(legacy).not.toHaveBeenCalled();
});

test("aborts a closed palette request and ignores its late result", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.libraryNavigation>>) => void;
  const navigation = vi.spyOn(api, "libraryNavigation").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<MemoryRouter><CommandPalette /></MemoryRouter>);
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  await waitFor(() => expect(navigation).toHaveBeenCalledTimes(1));
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  expect(navigation.mock.calls[0][1]?.signal?.aborted).toBe(true);
  await act(async () => finish({ items: [{ id: "old", title: "Old response", genre: "" }], scope: "test-library", collection_revision: "1" }));
  expect(screen.queryByText("Old response")).not.toBeInTheDocument();
});
