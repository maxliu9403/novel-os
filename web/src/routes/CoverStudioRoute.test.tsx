import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CoverStudioRoute from "./CoverStudioRoute";

vi.mock("./CoverStudio", () => ({default: () => <p>保存的旧版封面资料</p>}));
vi.mock("./PosterStudio", () => ({default: ({projectId, onLegacy}: {projectId: string; onLegacy: () => void}) => <><p>新版故事海报 {projectId}</p><button onClick={onLegacy}>打开历史工作室</button></>}));
afterEach(cleanup);
function Location() { const location = useLocation(); return <output aria-label="route">{location.pathname}{location.search}</output>; }
function visit(query = "", workshop = false) {
  return render(<MemoryRouter initialEntries={[workshop ? `/workshop/session/covers${query}` : `/projects/book/covers${query}`]}><Location /><Routes><Route path="/projects/:id/covers" element={<CoverStudioRoute />} /><Route path="/workshop/:sessionId/covers" element={<CoverStudioRoute />} /></Routes></MemoryRouter>);
}

test.each(["analysis", "directions"])("preserves the actual legacy artifact for a bookmarked %s tab", async tab => {
  const user = userEvent.setup(); visit(`?tab=${tab}&context=keep`);
  await screen.findByText("保存的旧版封面资料");
  expect(screen.queryByText(/新版故事海报/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "返回新版封面工作室 →"}));
  await screen.findByText("新版故事海报 book");
  const route = screen.getByLabelText("route").textContent || "";
  expect(route).toContain("context=keep"); expect(route).not.toContain(`?tab=${tab}`);
  expect(route).toContain(tab === "analysis" ? "poster_drawer=source" : "poster_tab=plan");
});
test.each(["costs", "gallery"])("keeps %s deep links in the new workspace with their query intent", async tab => {
  visit(`?tab=${tab}&context=keep`); await screen.findByText("新版故事海报 book");
  expect(screen.getByLabelText("route")).toHaveTextContent(`tab=${tab}&context=keep`);
});
test("explicit legacy mode and return preserve gallery selection without trapping navigation", async () => {
  const user = userEvent.setup(); visit("?legacy=1&tab=gallery&context=keep");
  await screen.findByText("保存的旧版封面资料");
  await user.click(screen.getByRole("button", {name: "返回新版封面工作室 →"}));
  await screen.findByText("新版故事海报 book");
  expect(screen.getByLabelText("route")).toHaveTextContent("poster_tab=gallery");
  await user.click(screen.getByRole("button", {name: "打开历史工作室"}));
  await waitFor(() => expect(screen.getByLabelText("route")).toHaveTextContent("legacy=1"));
  expect(screen.getByLabelText("route")).toHaveTextContent("context=keep");
});
test("workshop sessions always retain their supported legacy workspace", async () => {
  visit("?tab=costs", true); await screen.findByText("保存的旧版封面资料");
  expect(screen.queryByRole("button", {name: "返回新版封面工作室 →"})).not.toBeInTheDocument();
});
