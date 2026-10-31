import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, vi } from "vitest";
import ProjectDashboard from "../routes/ProjectDashboard";
import * as client from "../api/client";
import { projectStoryApi, type ProjectStory, type StorySourceRef } from "../api/projectStory";

const source: StorySourceRef = {
  kind: "adopted_skeleton",
  path: "outputs/input/prompt.md",
  section: "Characters",
  line_start: 10,
  line_end: 20,
};

function story(overrides: Partial<ProjectStory> = {}): ProjectStory {
  return {
    project_id: "p",
    chapters: [{ number: 1, title: "Opening", summary: "Lena 保存证据并离开。", source }],
    characters: [
      { id: "lena", name: "Lena", role: "主角", description: "她要重新取得选择权。", relationships: [], source },
      { id: "mara", name: "Mara", role: "挚友", description: "她提供暂时住处。", relationships: [{ text: "两人是多年好友。", source }], source },
    ],
    relationship_edges: [{ id: "lena-mara", source_character_id: "lena", target_character_id: "mara", label: "挚友", description: "关系真实，但仍有未决矛盾。", source }],
    narrative: { title: "选择自己的生活", text: "Lena 拒绝让别人替她定义未来。", source },
    sections: [
      {
        id: "creative-spec",
        number: 1,
        title: "创作规格",
        markdown: "## 创作规格\n\n[BOOK_SPEC_JSON]\n{\"estimated_chapters\":20,\"language\":\"English\",\"mystery_field\":\"kept\",\"schema_version\":1,\"sha256\":\"deadbeef\"}\n[/BOOK_SPEC_JSON]",
        json_blocks: [{
          kind: "BOOK_SPEC_JSON",
          raw: "{\"estimated_chapters\":20,\"language\":\"English\",\"mystery_field\":\"kept\",\"schema_version\":1,\"sha256\":\"deadbeef\"}\n",
          data: { estimated_chapters: 20, language: "English", mystery_field: "kept", schema_version: 1, sha256: "deadbeef" },
          valid: true,
          warning: null,
          source,
        }],
        source,
      },
      { id: "story-beats", number: 2, title: "关键节点", markdown: "### 危机\n\nLena 必须作出选择。", json_blocks: [], source },
    ],
    unstructured_sections: [],
    source: {
      available: true,
      path: "outputs/input/prompt.md",
      sha256: "abc",
      bytes: 1024,
      modified_ns: 1,
      media_type: "text/markdown",
      dependencies: [],
    },
    warnings: [],
    ...overrides,
  };
}

function mockCore(options: { task?: boolean; chapters?: number } = {}) {
  const chapterCount = options.chapters ?? 1;
  vi.spyOn(client.api, "project").mockResolvedValue({
    id: "p", title: "My Novel", genre: "Drama", author: "A", premise: "A hard choice.",
    chapter_count: chapterCount, status: "completed", style: {}, word_count: 2300,
    delivery: { status: "missing", retryable: true, error: null, updated_at: null },
    ...(options.task ? { task_id: "run-1" } : {}),
  });
  vi.spyOn(client.api, "chapters").mockResolvedValue(Array.from({ length: chapterCount }, (_, index) => ({
    number: index + 1,
    title: index === 0 ? "Opening" : `Chapter ${index + 1}`,
    status: index === chapterCount - 1 && chapterCount > 1 ? "planned" : "drafted",
    word_count: index === 0 ? 2300 : 0,
    pov: "Lena",
  })));
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

function renderDashboard(path = "/projects/p") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes><Route path="/projects/:id" element={<ProjectDashboard />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

test("shows a real chapter list with adopted-skeleton introductions and no legacy library cards", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story());
  renderDashboard();

  expect(await screen.findByRole("heading", { name: "My Novel" })).toBeInTheDocument();
  expect((await screen.findAllByText("Lena 保存证据并离开。")).length).toBeGreaterThan(0);
  const row = screen.getByRole("row", { name: /Opening/ });
  expect(within(row).getByText("已有初稿")).toBeInTheDocument();
  expect(within(row).getByText("2,300")).toBeInTheDocument();
  expect(within(row).getByRole("link", { name: "阅读正文" })).toHaveAttribute("href", "/projects/p/read?chapter=1");
  expect(within(row).getByRole("link", { name: "编辑章节" })).toHaveAttribute("href", "/projects/p/chapters/1");
  expect(screen.queryByText("设定库")).not.toBeInTheDocument();
  expect(screen.queryByText("素材库")).not.toBeInTheDocument();
});

test("uses actual prose evidence for chapter progress and reading actions", async () => {
  mockCore({ chapters: 2 });
  vi.mocked(client.api.chapters).mockResolvedValue([
    { number: 1, title: "In progress", status: "writing", word_count: 0, pov: "Lena" },
    { number: 2, title: "Saved after failure", status: "failed", word_count: 480, pov: "Mara" },
  ]);
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story({ chapters: [] }));
  renderDashboard();

  const writingRow = await screen.findByRole("row", { name: /In progress/ });
  expect(within(writingRow).queryByRole("link", { name: "阅读正文" })).not.toBeInTheDocument();
  expect(within(writingRow).queryByRole("button", { name: "规划此章" })).not.toBeInTheDocument();
  const failedRow = screen.getByRole("row", { name: /Saved after failure/ });
  expect(within(failedRow).getByRole("link", { name: "阅读正文" })).toHaveAttribute("href", "/projects/p/read?chapter=2");

  fireEvent.click(screen.getByRole("tab", { name: /作品概览/ }));
  const progress = (await screen.findByText("章节进度")).closest("article");
  expect(progress).toHaveTextContent("1 / 2");
});

test("keeps the selected tab in the URL, supports keyboard navigation, and lazy-loads story data", async () => {
  mockCore();
  const loadStory = vi.spyOn(projectStoryApi, "get").mockResolvedValue(story());
  renderDashboard("/projects/p?tab=overview");

  expect(await screen.findByRole("heading", { name: "作品概览" })).toBeInTheDocument();
  expect(loadStory).not.toHaveBeenCalled();
  const overview = screen.getByRole("tab", { name: /作品概览/ });
  fireEvent.keyDown(overview, { key: "ArrowRight" });
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("?tab=chapters"));
  expect(loadStory).toHaveBeenCalledTimes(1);
  const chapterTab = screen.getByRole("tab", { name: /章节/ });
  expect(chapterTab).toHaveAttribute("aria-selected", "true");
  expect(chapterTab.getAttribute("aria-controls")).toBe(screen.getByRole("tabpanel").id);
});

test("loads the active creation task on the default chapter tab without expanding it", async () => {
  mockCore({ task: true });
  vi.spyOn(client.api, "projectTask").mockImplementation(() => new Promise(() => {}));
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story());
  renderDashboard();

  const drawer = (await screen.findByText("查看状态、暂停或恢复")).closest("details");
  expect(drawer).not.toHaveAttribute("open");
  await waitFor(() => expect(client.api.projectTask).toHaveBeenCalledWith("p", "run-1"));
});

test("renders structured JSON as business fields, preserves unknown fields, and keeps raw data collapsed", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story());
  const sourceRequest = vi.spyOn(projectStoryApi, "source").mockResolvedValue("# Raw skeleton");
  renderDashboard("/projects/p?tab=story");

  expect(await screen.findByRole("heading", { name: "故事骨架" })).toBeInTheDocument();
  const directory = within(screen.getByLabelText("骨架目录"));
  fireEvent.click(directory.getByRole("button", { name: /关键节点/ }));
  expect(await screen.findByText("Lena 必须作出选择。")).toBeInTheDocument();
  fireEvent.click(directory.getByRole("button", { name: /创作规格/ }));
  expect(await screen.findByText("预计章节")).toBeInTheDocument();
  expect(screen.getByText("语言")).toBeInTheDocument();
  expect(screen.queryByText("[BOOK_SPEC_JSON]")).not.toBeInTheDocument();
  const otherSummary = screen.getByText("其他信息");
  expect(otherSummary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(otherSummary);
  expect(screen.getByText("mystery field")).toBeInTheDocument();
  const technicalSummary = screen.getByText("技术信息");
  expect(technicalSummary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(technicalSummary);
  expect(screen.getByText("schema version")).toBeInTheDocument();
  expect(screen.getByText("sha256")).toBeInTheDocument();
  const rawSummary = screen.getByText("查看原始数据");
  expect(rawSummary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(rawSummary);
  expect(screen.getByText(/"mystery_field":"kept"/)).toBeInTheDocument();
  expect(sourceRequest).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看完整原文" }));
  expect(await screen.findByText("Raw skeleton")).toBeInTheDocument();
  expect(sourceRequest).toHaveBeenCalledWith("p");
});

test("routes old codex deep links to sourced characters and never infers extra relationships", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story());
  renderDashboard("/projects/p?tab=overview&codex=mara");

  expect(await screen.findByRole("heading", { name: "人物与关系" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /人物关系/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("option", { name: /Mara/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("关系真实，但仍有未决矛盾。")).toBeInTheDocument();
  expect(screen.getAllByText(/采用骨架/).length).toBeGreaterThan(0);
  expect(screen.queryByText("爱情关系")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /章节/ }));
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("?tab=chapters"));
  expect(screen.getByTestId("location")).not.toHaveTextContent("codex=");
});

test("shows story failures and retries without hiding the basic chapter list", async () => {
  mockCore();
  const loadStory = vi.spyOn(projectStoryApi, "get")
    .mockRejectedValueOnce(new Error("source unavailable"))
    .mockResolvedValueOnce(story());
  renderDashboard();

  expect(await screen.findByText("Opening")).toBeInTheDocument();
  expect(await screen.findByText("source unavailable")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect((await screen.findAllByText("Lena 保存证据并离开。")).length).toBeGreaterThan(0);
  expect(loadStory).toHaveBeenCalledTimes(2);
});

test("states empty source data plainly instead of creating placeholder people or chapter facts", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story({
    chapters: [], characters: [], relationship_edges: [], sections: [], narrative: null,
    source: { available: false, path: "", sha256: "", bytes: 0, modified_ns: 0, media_type: "text/markdown", dependencies: [] },
    warnings: ["Adopted skeleton is missing"],
  }));
  renderDashboard("/projects/p?tab=characters");

  expect(await screen.findByText("采用骨架没有可提取的人物资料。页面不会用空头像或推断关系补位。")).toBeInTheDocument();
  expect(screen.getByText(/不会据共同出现推断关系/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /故事骨架/ }));
  expect(await screen.findByText(/当前作品没有可读取的采用骨架/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看完整原文" })).not.toBeInTheDocument();
});

test("keeps malformed JSON reachable as raw source without rendering invented fields", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story({
    sections: [{
      id: "broken", number: 1, title: "损坏模块",
      markdown: "BROKEN_JSON_BEGIN\n{\"unfinished\":\nBROKEN_JSON_END",
      json_blocks: [{ kind: "BROKEN_JSON", raw: "{\"unfinished\":\n", data: null, valid: false, warning: "Unexpected end of JSON input", source }],
      source,
    }],
  }));
  renderDashboard("/projects/p?tab=story");

  const directory = await screen.findByLabelText("骨架目录");
  fireEvent.click(within(directory).getByRole("button", { name: /损坏模块/ }));
  expect(screen.getByText("Unexpected end of JSON input")).toBeInTheDocument();
  expect(screen.queryByText("BROKEN_JSON_BEGIN")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("查看原始数据"));
  expect(screen.getByText(/"unfinished"/)).toBeInTheDocument();
});

test("refreshes externally generated chapter progress when the window regains focus", async () => {
  vi.spyOn(client.api, "project").mockResolvedValue({ id: "p", title: "My Novel", genre: "Drama", author: "A", chapter_count: 15, status: "in_progress", style: {}, delivery: { status: "missing", retryable: true, error: null, updated_at: null } });
  const firstThree = Array.from({ length: 3 }, (_, index) => ({ number: index + 1, title: `Chapter ${index + 1}`, status: "complete", word_count: 1000, pov: "Lena" }));
  const allChapters = Array.from({ length: 15 }, (_, index) => ({ number: index + 1, title: `Chapter ${index + 1}`, status: "complete", word_count: 1000, pov: "Lena" }));
  vi.spyOn(client.api, "chapters").mockResolvedValueOnce(firstThree).mockResolvedValue(allChapters);
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story({ chapters: [] }));
  renderDashboard("/projects/p?tab=overview");

  const progress = (await screen.findByText("章节进度")).closest("article");
  expect(progress).toHaveTextContent("3 / 15");
  fireEvent.focus(window);
  await waitFor(() => expect(progress).toHaveTextContent("15 / 15"));
  expect(screen.getAllByText("15,000").length).toBeGreaterThan(0);
});

test("keeps partially recognized character originals accessible beside source-backed cards", async () => {
  mockCore();
  vi.spyOn(projectStoryApi, "get").mockResolvedValue(story({
    unstructured_sections: [{
      kind: "characters", title: "Character Ledger", text: "人物候选仍有一部分未确定。", source,
    }],
  }));
  renderDashboard("/projects/p?tab=characters");
  const original = await screen.findByText("人物档案原文 · Character Ledger");
  fireEvent.click(original);
  expect(screen.getByText("人物候选仍有一部分未确定。")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Lena/ })).toBeInTheDocument();
});
