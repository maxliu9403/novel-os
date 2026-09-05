import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, vi } from "vitest";
import ProjectsList from "../routes/ProjectsList";
import * as client from "../api/client";

afterEach(() => vi.restoreAllMocks());

test("renders project cards from the API", async () => {
  vi.spyOn(client.api, "projects").mockResolvedValue([
    { id: "the-last-signal", title: "The Last Signal", genre: "Sci-Fi", chapter_count: 3, status: "in_progress" },
  ]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  expect(await screen.findByText("The Last Signal")).toBeInTheDocument();
  expect(screen.getByText(/已写 0\/3 章/)).toBeInTheDocument();
});

test("previews and permanently deletes every resource owned by a project", async () => {
  const project = {
    id: "last-signal",
    title: "最后的信号",
    genre: "科幻",
    chapter_count: 3,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  const preview = vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 3,
    exists: true,
    counts: {
      projects: 1,
      chapters: 3,
      artifacts: 8,
      artifact_revisions: 4,
      snapshots: 2,
      comments: 1,
      evaluation_reports: 2,
      quality_findings: 3,
      promotion_receipts: 1,
      media: 2,
    },
    project_files: 18,
    project_bytes: 8192,
    media_files: 2,
    media_bytes: 4096,
    running_job_ids: [],
    can_delete: true,
  });
  const destroy = vi.spyOn(client.api, "deleteProject").mockResolvedValue({
    project_id: project.id,
    status: "deleted",
    before: {} as client.ProjectDeletionInventory,
    after: {} as client.ProjectDeletionInventory,
    cleared_consequence_previews: 0,
  });
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  expect(await screen.findByText(project.title)).toBeInTheDocument();

  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));
  expect(await screen.findByText("此操作无法撤销")).toBeInTheDocument();
  expect(preview).toHaveBeenCalledWith(project.id);

  const confirmation = await screen.findByLabelText(`请输入书名“${project.title}”确认删除`);
  await user.type(confirmation, project.title);
  await user.click(screen.getByRole("button", { name: "永久删除" }));

  await waitFor(() => expect(destroy).toHaveBeenCalledWith(project.id, project.title));
  await waitFor(() => expect(screen.queryByText(project.title)).not.toBeInTheDocument());
});

test("blocks deletion while a project task is running", async () => {
  const project = {
    id: "busy-book",
    title: "仍在生成的小说",
    genre: "悬疑",
    chapter_count: 2,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 2,
    exists: true,
    counts: {},
    project_files: 4,
    project_bytes: 1024,
    media_files: 0,
    media_bytes: 0,
    running_job_ids: ["job-1"],
    can_delete: false,
  });
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await screen.findByText(project.title);
  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));

  expect(await screen.findByText(/仍有 1 个任务正在运行/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "永久删除" })).toBeDisabled();
});

test("keeps the project visible when permanent deletion fails", async () => {
  const project = {
    id: "renamed-book",
    title: "改名前的小说",
    genre: "剧情",
    chapter_count: 1,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 1,
    exists: true,
    counts: {},
    project_files: 2,
    project_bytes: 512,
    media_files: 0,
    media_bytes: 0,
    running_job_ids: [],
    can_delete: true,
  });
  vi.spyOn(client.api, "deleteProject").mockRejectedValue(
    new Error("小说标题已变化，请重新预览后再删除。"),
  );
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await screen.findByText(project.title);
  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));
  await user.type(
    await screen.findByLabelText(`请输入书名“${project.title}”确认删除`),
    project.title,
  );
  await user.click(screen.getByRole("button", { name: "永久删除" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("小说标题已变化");
  expect(screen.getByText(project.title)).toBeInTheDocument();
});
