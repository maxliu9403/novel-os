import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ProjectDetail } from "../api/client";
import WorkshopProjectProgress from "./WorkshopProjectProgress";

afterEach(() => vi.restoreAllMocks());
const project: ProjectDetail = { id: "saved-book", title: "Book", genre: "Romance", author: "Author", chapter_count: 16, status: "completed", style: {}, task_id: "durable-run", task_phase: "compile" };

it("restores completed progress from the durable project without asking for a process-local job", async () => {
  const getJob = vi.spyOn(api, "getJob").mockRejectedValue(new Error("Job not found"));
  vi.spyOn(api, "project").mockResolvedValue(project);
  render(<WorkshopProjectProgress projectId={project.id} />);
  expect(screen.getByRole("status")).toHaveTextContent("正在读取作品创作状态");
  expect(await screen.findByText("作品创作 · 已完成")).toBeInTheDocument();
  expect(screen.getByText("当前任务 durable-run")).toBeInTheDocument();
  expect(screen.queryByText(/关闭窗口不会暂停/)).not.toBeInTheDocument();
  expect(getJob).not.toHaveBeenCalled();
});

it("never calls an unreadable project running and preserves its launch receipt guidance", async () => {
  vi.spyOn(api, "project").mockRejectedValue(new Error("Project not found"));
  render(<WorkshopProjectProgress projectId="missing" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Project not found");
  expect(screen.getByRole("status")).toHaveTextContent("状态暂不可确认");
  expect(screen.queryByText(/正在执行|关闭窗口不会暂停/)).not.toBeInTheDocument();
});

it("renders paused status and ignores responses from a previously selected project", async () => {
  let resolveOld!: (value: ProjectDetail) => void;
  vi.spyOn(api, "project").mockImplementation(id => id === "old" ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ ...project, status: "paused" }));
  const view = render(<WorkshopProjectProgress projectId="old" />);
  await waitFor(() => expect(resolveOld).toBeDefined());
  view.rerender(<WorkshopProjectProgress projectId="saved-book" />);
  expect(await screen.findByText("作品创作 · 已暂停")).toBeInTheDocument();
  resolveOld({ ...project, id: "old", status: "running" });
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("已暂停"));
});
