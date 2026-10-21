import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import { api } from "../api/client";
import { workshopApi, type WorkshopRuntimeProgress, type WorkshopSession } from "../api/workshop";
import NovelWorkshop, { type WorkshopSeed } from "../components/NovelWorkshop";

const seed: WorkshopSeed = { title: "Nora 的记忆", author: "Terry", genres: ["Fantasy"], premise: "Nora 是流亡的档案员，要找回家人的记忆。", method_mode: "off" };
const markdown = "# Nora 的记忆\n\n## 人物身份\n\n| 人物 | 代词 |\n| --- | --- |\n| Nora | she/her |\n\n## 第一章\n她保留证据，暂不对质。";
function session(patch: Partial<WorkshopSession> = {}): WorkshopSession {
  return { id: "session-1", revision: 2, input: { ...seed, language: "English", mode: "standalone_long", audience: "25–40 岁读者", market: "美国", constraints: "不改变Nora的姓名", chapters: 80, words_per_chapter: 1000 },
    messages: [{ role: "assistant", content: "可以把记忆的代价作为人物的内心冲突。" }],
    prompt_md: markdown, questions: [], ready_for_confirmation: true, status: "ready", last_error: null, job_id: "turn-1", skill_version: "version-1", prepared: null, project_id: null, logs: [], ...patch };
}
function prepared(current: WorkshopSession): WorkshopSession {
  return { ...current, prepared: { revision: current.revision, command: "NOVEL_OS_CHAPTERS=80 \\\nNOVEL_OS_OUTPUT='markdown html docx epub pdf' \\\n./deploy.sh novel './prompt/nora.md'", project_name: "nora", prompt_filename: "nora.md",
    validation: { title: "Nora 的记忆", mode: "standalone_long", chapters: 80, target_words: 80000 }, output_formats: ["markdown", "html", "docx", "epub", "pdf"] } };
}
function view(value = seed) {
  return render(<MemoryRouter><NovelWorkshop open seed={value} onClose={vi.fn()} onBack={vi.fn()} /></MemoryRouter>);
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "studioModels").mockRejectedValue(new Error("No configured route"));
});
afterEach(() => vi.restoreAllMocks());

test("creates an asynchronous workshop with the explicit continuous mode and original review preference", async () => {
  const initial = session({ revision: 0, prompt_md: "", messages: [], status: "draft", ready_for_confirmation: false });
  const create = vi.spyOn(workshopApi, "create").mockResolvedValue(initial);
  const turn = vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "running", error: null });
  vi.spyOn(workshopApi, "get").mockResolvedValue(session());
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "done", error: null });
  const user = userEvent.setup();
  view();
  await user.selectOptions(screen.getByLabelText("故事组织形式"), "standalone_long");
  await user.type(screen.getByLabelText("计划章节数"), "80");
  await user.type(screen.getByLabelText("每章大约多少字／词"), "1000");
  await user.type(screen.getByLabelText("正文语言"), "English");
  await user.click(screen.getByRole("button", { name: "开始头脑风暴" }));
  await screen.findByText("可以把记忆的代价作为人物的内心冲突。");
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ mode: "standalone_long", chapters: 80, words_per_chapter: 1000, method_mode: "off", premise: seed.premise }));
  expect(turn).toHaveBeenCalledWith("session-1", expect.any(String), 0, "discuss");
  expect(screen.getByText(/不限讨论次数/)).toBeInTheDocument();
  expect(localStorage.getItem("novel-os.workshop.active-session")).toBe("session-1");
});

test("renders the skeleton, suggests names, accepts the human title and launches the existing pipeline", async () => {
  let current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.mocked(workshopApi.get).mockImplementation(async () => current);
  const titles = vi.spyOn(workshopApi, "titles").mockImplementation(async () => {
    current = { ...current, revision: 4, active_operation: "titles", naming: { source_sha256: "outline", confirmed_title: null,
      candidates: [{ title: "The Lost Archive", reason: "突出记忆的代价" }], requests: [{ instructions: "", created_at: "2026-10-07T12:00:00Z" }] } };
    return { job_id: "names", kind: "workshop.titles", status: "running", error: null };
  });
  const prepare = vi.spyOn(workshopApi, "prepare").mockImplementation(async (_id, _revision, title) => {
    current = prepared({ ...current, revision: 5 });
    current.prepared!.validation.title = title;
    return current;
  });
  const launch = vi.spyOn(workshopApi, "launch").mockResolvedValue({ project_id: "nora", job_id: "full-run" });
  vi.spyOn(api, "getJob").mockImplementation(async (id) => id === "names"
    ? { job_id: id, kind: "workshop.titles", status: "done", error: null }
    : { job_id: id, kind: "pipeline", status: "error", error: "Writer connection failed" });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByRole("table")).toHaveTextContent("Nora");
  expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载骨架 MD" })).toHaveAttribute("href", expect.stringContaining("/sessions/session-1/prompt"));
  await user.click(screen.getByRole("button", { name: "确认骨架，推荐书名" }));
  expect(titles).toHaveBeenCalledWith(current.id, 2, "", "");
  await user.click(await screen.findByRole("button", { name: /The Lost Archive/ }));
  fireEvent.change(screen.getByLabelText("最终书名"), { target: { value: "The Archive We Chose" } });
  await user.click(screen.getByRole("button", { name: "确认书名并生成命令" }));
  expect(prepare).toHaveBeenCalledWith(current.id, 4, "The Archive We Chose");
  expect(await screen.findByText("MARKDOWN · HTML · DOCX · EPUB · PDF")).toBeInTheDocument();
  expect(screen.getByText(/80 章 · 总目标 80000/)).toBeInTheDocument();
  expect(screen.getByText(/先将下载的 MD 保存到仓库/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "一键启动全书创作" }));
  expect(launch).toHaveBeenCalledWith(current.id, current.revision);
  expect(await screen.findByText("Writer connection failed")).toBeInTheDocument();
  expect(screen.getByText("创作任务未完成")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "打开作品与交付" })).toBeEnabled();
});

test("server restart loses an in-memory job but the durable error restores retry and the previous skeleton", async () => {
  const running = session({ status: "running", job_id: "lost-job" });
  localStorage.setItem("novel-os.workshop.active-session", running.id);
  vi.spyOn(workshopApi, "get").mockResolvedValueOnce(running).mockResolvedValueOnce(running).mockResolvedValue(session({ status: "error", last_error: "服务重启中断了本轮讨论", job_id: "lost-job" }));
  vi.spyOn(api, "getJob").mockRejectedValue(new Error("HTTP 404"));
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("服务重启中断了本轮讨论")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试本轮" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
  expect(screen.queryByText(/等待模型回复 ·/)).not.toBeInTheDocument();
});

test("failed turn retains both the previous skeleton and the unsent correction", async () => {
  const current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(workshopApi, "turn").mockRejectedValue(new Error("模型请求未提交"));
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.type(screen.getByLabelText(/补充、回答或修改设定/), "请保持她的名字和代词");
  await user.click(screen.getByRole("button", { name: "发送并继续推导" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("模型请求未提交");
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("请保持她的名字和代词");
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByRole("table")).toHaveTextContent("she/her");
});

test("ordinary sends continue discussion explicitly without requesting a skeleton", async () => {
  const current = session({ prompt_md: "", ready_for_confirmation: false });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const turn = vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "discuss-job", kind: "workshop", status: "running", error: null });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "discuss-job", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText(/普通发送会继续讨论，分阶段推进当前提案、取舍和修订/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "沿着第二个方向深化对手的选择" } });
  await user.click(screen.getByRole("button", { name: "发送并继续推导" }));
  expect(turn).toHaveBeenCalledExactlyOnceWith(current.id, "沿着第二个方向深化对手的选择", current.revision, "discuss");
});

test("direct delivery includes an unsent draft instead of discarding it", async () => {
  const complete = session();
  const current = session({ prompt_md: "", ready_for_confirmation: false,
    input: { ...complete.input, audience: "", market: "" } });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const turn = vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "deliver-job", kind: "workshop", status: "running", error: null });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "deliver-job", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "结局保留一次代价明确的和解" } });
  await user.click(screen.getByRole("button", { name: "生成完整骨架" }));
  expect(turn).toHaveBeenCalledExactlyOnceWith(current.id,
    "请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。\n\n请同时纳入这条尚未发送的补充：\n结局保留一次代价明确的和解",
    current.revision, "deliver");
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("");
});

test("a premise-only brief can request delivery before an assistant discussion reply exists", async () => {
  const complete = session();
  const current = session({ prompt_md: "", messages: [], ready_for_confirmation: false,
    input: { ...complete.input, chapters: undefined, words_per_chapter: undefined, language: "", mode: "", audience: "", market: "" } });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByRole("button", { name: "生成完整骨架" })).toBeEnabled();
});

test("delivery still requires a nonempty saved premise", async () => {
  const complete = session();
  const current = session({ prompt_md: "", messages: [], ready_for_confirmation: false,
    input: { ...complete.input, premise: "", audience: "", market: "" } });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByRole("button", { name: "生成完整骨架" })).toBeDisabled();
});

test.each([
  { name: "a running request", patch: { status: "running" as const, restart_required: false } },
  { name: "saved basics awaiting restart", patch: { status: "draft" as const, restart_required: true } },
])("disables skeleton delivery during $name", async ({ patch }) => {
  const current = session(patch);
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  if (current.status === "running") vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  if (current.restart_required) await user.click(screen.getByRole("button", { name: /2\s*头脑风暴/ }));
  expect(screen.getByRole("button", { name: "更新完整骨架" })).toBeDisabled();
});

test("an error retry reuses the persisted delivery intent", async () => {
  const current = session({ status: "error", last_error: "完整骨架生成超时", prompt_md: "", ready_for_confirmation: false, turn_intent: "deliver" });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const turn = vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "retry-delivery", kind: "workshop", status: "running", error: null });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "retry-delivery", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "重试本轮" }));
  expect(turn).toHaveBeenCalledExactlyOnceWith(current.id, expect.stringContaining("继续完成上一轮"), current.revision, "deliver");
});

test("successive corrections use the latest revision and invalidate the previous command", async () => {
  let current = prepared(session());
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  const turn = vi.spyOn(workshopApi, "turn").mockImplementation(async (_id, message) => {
    current = { ...current, revision: current.revision + 2, prepared: null,
      messages: [...current.messages, { role: "user", content: message }, { role: "assistant", content: "已更新当前设计" }] };
    return { job_id: "turn-1", kind: "workshop", status: "running", error: null };
  });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "done", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  for (let round = 0; round < 6; round++) {
    const revision = current.revision;
    fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: `修改伏笔 ${round}` } });
    await user.click(screen.getByRole("button", { name: "发送并继续推导" }));
    await waitFor(() => expect(screen.getAllByText("已更新当前设计")).toHaveLength(round + 1));
    expect(turn).toHaveBeenLastCalledWith(current.id, `修改伏笔 ${round}`, revision, "discuss");
  }
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.queryByRole("button", { name: "一键启动全书创作" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认骨架，推荐书名" })).toBeEnabled();
});

test("unresolved questions keep preview available but prevent premature launch preparation", async () => {
  const current = session({ questions: ["希望连续章节还是分卷？"], ready_for_confirmation: false, status: "draft" });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByText("希望连续章节还是分卷？")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认骨架，推荐书名" })).toBeDisabled();
});

test("returning to basic creation applies changed seed without discarding optional unsent fields", () => {
  const props = { open: true, onClose: vi.fn(), onBack: vi.fn() };
  const { rerender } = render(<MemoryRouter><NovelWorkshop {...props} seed={seed} /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("计划章节数"), { target: { value: "80" } });
  rerender(<MemoryRouter><NovelWorkshop {...props} seed={{ ...seed, title: "改后的书名", premise: "新的故事构想" }} /></MemoryRouter>);
  expect(screen.getByLabelText("暂定书名")).toHaveValue("改后的书名");
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue("新的故事构想");
  expect(screen.getByLabelText("计划章节数")).toHaveValue(80);
});

test("saved basics can be edited and saved before a separately confirmed fresh start", async () => {
  let current = session();
  const original = current.input;
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  const update = vi.spyOn(workshopApi, "updateInput").mockImplementation(async (_id, _revision, input) => {
    current = { ...current, input, initial_input: original, revision: 3, status: "draft", input_needs_revision: true, restart_required: true, ready_for_confirmation: false, prepared: null };
    return current;
  });
  const turn = vi.spyOn(workshopApi, "turn");
  const restart = vi.spyOn(workshopApi, "restart").mockImplementation(async () => {
    current = { ...current, revision: 4, status: "running", job_id: "updated", restart_required: false, messages: [], prompt_md: "" };
    return { job_id: "updated", kind: "workshop", status: "running", error: null };
  });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "updated", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: /1\s*基础设定/ }));
  expect(screen.getByLabelText("计划章节数")).toBeDisabled();
  expect(screen.getByLabelText("计划章节数")).toHaveValue(80);
  await user.click(screen.getByRole("button", { name: "修改基础设定" }));
  expect(screen.getByLabelText("计划章节数")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("计划章节数"), { target: { value: "24" } });
  await user.click(screen.getByRole("button", { name: "取消修改" }));
  expect(screen.getByLabelText("计划章节数")).toHaveValue(80);
  expect(update).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "修改基础设定" }));
  fireEvent.change(screen.getByLabelText("计划章节数"), { target: { value: "24" } });
  await user.click(screen.getByRole("button", { name: "保存基础设定" }));
  expect(update).toHaveBeenCalledWith("session-1", 2, expect.objectContaining({ chapters: 24 }));
  expect(turn).not.toHaveBeenCalled();
  expect(restart).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认，从头推导" }));
  expect(restart).toHaveBeenCalledWith("session-1", 3);
  expect(screen.queryByText("可以把记忆的代价作为人物的内心冲突。")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /1\s*基础设定/ }));
  expect(screen.getByLabelText("计划章节数")).toHaveValue(24);
  expect(screen.getByLabelText("计划章节数")).toBeDisabled();
  expect(screen.getByRole("button", { name: "修改基础设定" })).toBeDisabled();
});

test("saving edited basics waits for an explicit fresh-start decision before model inference", async () => {
  let current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  const update = vi.spyOn(workshopApi, "updateInput").mockImplementation(async (_id, _revision, input) => {
    current = { ...current, input, revision: 3, status: "draft", input_needs_revision: true, restart_required: true, ready_for_confirmation: false };
    return current;
  });
  const turn = vi.spyOn(workshopApi, "turn").mockRejectedValue(new Error("unexpected model call"));
  const user = userEvent.setup(); view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: /1\s*基础设定/ }));
  await user.click(screen.getByRole("button", { name: "修改基础设定" }));
  expect(screen.getByLabelText("计划章节数")).toBeEnabled();
  expect(update).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("计划章节数"), { target: { value: "24" } });
  await user.click(screen.getByRole("button", { name: /保存.*(?:设定|骨架)/ }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(turn).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "确认，从头推导" })).toBeInTheDocument();
});

test("launched basics are a read-only snapshot", async () => {
  const current = session({ status: "launched", project_id: "nora" });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "turn-1", kind: "pipeline", status: "done", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "查看创作任务" }));
  await user.click(screen.getByRole("button", { name: /1\s*基础设定/ }));
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue(seed.premise);
  expect(screen.getByLabelText("故事构想或已有框架")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "修改基础设定" })).not.toBeInTheDocument();
});

test("a lost basics save response refreshes revision and preserves the edit for retry", async () => {
  let current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  const update = vi.spyOn(workshopApi, "updateInput").mockImplementation(async (_id, _revision, input) => {
    current = { ...current, revision: 3, input, status: "draft", ready_for_confirmation: false };
    throw new Error("响应丢失");
  });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: /1\s*基础设定/ }));
  await user.click(screen.getByRole("button", { name: "修改基础设定" }));
  fireEvent.change(screen.getByLabelText("计划章节数"), { target: { value: "24" } });
  await user.click(screen.getByRole("button", { name: "保存基础设定" }));
  await screen.findByText("响应丢失");
  await waitFor(() => expect(screen.getByRole("button", { name: "保存基础设定" })).toBeEnabled());
  expect(screen.getByLabelText("计划章节数")).toHaveValue(24);
  await user.click(screen.getByRole("button", { name: "保存基础设定" }));
  expect(update).toHaveBeenLastCalledWith(current.id, 3, expect.objectContaining({ chapters: 24 }));
});

test("a lost title confirmation response recovers the prepared command without losing the chosen title", async () => {
  let current = session({ active_operation: "titles", naming: { source_sha256: "outline", confirmed_title: null, candidates: [], requests: [] } });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  vi.spyOn(workshopApi, "prepare").mockImplementation(async (_id, _revision, title) => {
    current = prepared({ ...current, revision: 3 });
    current.prepared!.validation.title = title;
    throw new Error("确认响应丢失");
  });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  fireEvent.change(screen.getByLabelText("最终书名"), { target: { value: "My Title" } });
  await user.click(screen.getByRole("button", { name: "确认书名并生成命令" }));
  expect(await screen.findByRole("button", { name: "一键启动全书创作" })).toBeEnabled();
  expect(screen.getByLabelText("最终书名")).toHaveValue("My Title");
});

test("restored naming supports repeated hints and editing a prepared name requires a new command", async () => {
  let current = prepared(session({ active_operation: "titles", naming: { source_sha256: "outline", confirmed_title: seed.title,
    candidates: [{ title: "Memory's Price", reason: "突出代价" }], requests: [{ instructions: "不要透露结局", created_at: "2026-10-07T12:00:00Z" }] } }));
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  const titles = vi.spyOn(workshopApi, "titles").mockImplementation(async (_id, _revision, instructions) => {
    current = { ...current, revision: current.revision + 2, prepared: null,
      naming: { ...current.naming!, requests: [...current.naming!.requests, { instructions, created_at: "2026-10-07T13:00:00Z" }] } };
    return { job_id: `name-${current.revision}`, kind: "workshop.titles", status: "running", error: null };
  });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "names", kind: "workshop.titles", status: "done", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByLabelText("最终书名")).toHaveValue(seed.title);
  expect(screen.getByRole("button", { name: "一键启动全书创作" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("最终书名"), { target: { value: "My Chosen Title" } });
  expect(screen.queryByRole("button", { name: "一键启动全书创作" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认书名并生成命令" })).toBeEnabled();
  for (const hint of ["更简短", "强调女性成长"]) {
    const revision = current.revision;
    fireEvent.change(screen.getByLabelText("书名优化要求"), { target: { value: hint } });
    await user.click(screen.getByRole("button", { name: "按要求重新推荐书名" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "按要求重新推荐书名" })).toBeEnabled());
    expect(titles).toHaveBeenLastCalledWith(current.id, revision, hint, "My Chosen Title");
    expect(screen.getByLabelText("最终书名")).toHaveValue("My Chosen Title");
  }
  expect(screen.getByText("命名历史 · 3 轮")).toBeInTheDocument();
});

test("closing and reopening keeps the conversation and unsent correction", async () => {
  const current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const props = { seed, onClose: vi.fn(), onBack: vi.fn() };
  const { rerender } = render(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "还没发送的修改" } });
  rerender(<MemoryRouter><NovelWorkshop {...props} open={false} /></MemoryRouter>);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  rerender(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("还没发送的修改");
  expect(screen.getByText("可以把记忆的代价作为人物的内心冲突。")).toBeInTheDocument();
});

test("folds each message, reveals full long content and renders Markdown with original input", async () => {
  const longPremise = `## 最初的故事\n\n${"Nora需要找回家人的记忆。".repeat(30)}\n\n原始框架最后一段必须保留。`;
  const longUser = `${"请强化人物的内心抉择。".repeat(30)}\n\n用户输入最后一句也必须保留。`;
  const reply = "## 新的故事方向\n\n先让Nora承担自己的选择。\n\n- 保存证据\n- 保护家人\n\n> 记忆并不等于真相。\n\n| 伏笔 | 回收 |\n| --- | --- |\n| 密封信件 | 第六章 |\n\n```text\nNora: she/her\n```\n\n完整回复的结尾不能被截断。";
  const current = session({ input: { ...session().input, premise: longPremise },
    messages: [{ role: "assistant", content: "过去的讨论提案" }, { role: "user", content: longUser }, { role: "assistant", content: reply }],
    questions: ["### 结局选择\n\n你希望是**团聚**，还是开放结局？"] });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByRole("button", { name: "展开故事设计助手 · 第 1 条" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("heading", { name: "新的故事方向" })).toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveTextContent("密封信件");
  expect(screen.getByText("保存证据").closest("li")).toBeInTheDocument();
  expect(screen.getByText("记忆并不等于真相。").closest("blockquote")).toBeInTheDocument();
  expect(screen.getByText("Nora: she/her").closest("pre")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "结局选择" })).toBeInTheDocument();
  expect(screen.queryByText("用户输入最后一句也必须保留。")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "展开你的想法 · 第 2 条" }));
  expect(screen.getByText("用户输入最后一句也必须保留。")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "展开原始基础设定" }));
  expect(screen.getByText("原始框架最后一段必须保留。")).toBeInTheDocument();
  expect(screen.getByText("25–40 岁读者")).toBeInTheDocument();
  expect(screen.getByText("不改变Nora的姓名")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "全部收起" }));
  expect(screen.queryByRole("heading", { name: "新的故事方向" })).not.toBeInTheDocument();
  expect(screen.queryByText("原始框架最后一段必须保留。")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "展开继续完善的关键问题 · 1 项" })).toHaveAttribute("aria-expanded", "false");
  await user.click(screen.getByRole("button", { name: "全部展开" }));
  expect(screen.getByText("完整回复的结尾不能被截断。")).toBeInTheDocument();
  expect(screen.getByText("用户输入最后一句也必须保留。")).toBeInTheDocument();
  expect(screen.getByText("原始框架最后一段必须保留。")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "结局选择" })).toBeInTheDocument();
});

test("historical questions remain with their assistant message and current questions are not duplicated", async () => {
  const current = session({ messages: [
    { role: "assistant", content: "先确定主角的选择", questions: ["**最初的问题**：她愿意付出什么？"] },
    { role: "user", content: "她愿意失去自己的记忆" },
    { role: "assistant", content: "接下来确认结局", questions: ["**最后的问题**：她会找回家人吗？"] },
  ], questions: ["**最后的问题**：她会找回家人吗？"] });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getAllByText("最后的问题")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: /继续完善的关键问题/ })).not.toBeInTheDocument();
  expect(screen.queryByText("最初的问题")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "展开故事设计助手 · 第 1 条" }));
  expect(screen.getByText("最初的问题")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "全部收起" }));
  expect(screen.queryByText("最初的问题")).not.toBeInTheDocument();
  expect(screen.queryByText("最后的问题")).not.toBeInTheDocument();
});

test("polling and new replies preserve manual folds and never force a reading scroll", async () => {
  const oldScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  try {
    const current = session();
    const updated = session({ revision: 4, messages: [...current.messages,
      { role: "user", content: "推迟关键揭示" }, { role: "assistant", content: "## 修改结果\n\n已把揭示移至第六章。" }] });
    localStorage.setItem("novel-os.workshop.active-session", current.id);
    vi.spyOn(workshopApi, "get").mockResolvedValueOnce(current).mockResolvedValueOnce(current)
      .mockResolvedValueOnce({ ...current, status: "running" }).mockResolvedValue(updated);
    vi.spyOn(api, "getJob").mockResolvedValueOnce({ job_id: "turn-1", kind: "workshop", status: "running", error: null })
      .mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "done", error: null });
    vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "running", error: null });
    const user = userEvent.setup();
    view();
    await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
    await user.click(screen.getByRole("button", { name: "收起故事设计助手 · 第 1 条" }));
    fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "推迟关键揭示" } });
    await user.click(screen.getByRole("button", { name: "发送并继续推导" }));
    expect(screen.getByRole("button", { name: "展开故事设计助手 · 第 1 条" })).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByRole("heading", { name: "修改结果" }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开故事设计助手 · 第 1 条" })).toHaveAttribute("aria-expanded", "false");
    expect(scroll).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "跳到最新回复" }));
    expect(scroll).toHaveBeenCalledOnce();
  } finally {
    if (oldScroll) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", oldScroll);
    else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});

function summary(current: WorkshopSession) {
  return { id: current.id, title: "同名小说", status: current.status, revision: current.revision,
    updated_at: "2026-10-07T12:40:00Z", project_id: null, has_prompt: true, message_count: current.messages.length, premise_preview: "同名也能按任务 ID 区分" };
}

test("restores exact same-title sessions with isolated drafts and folds, and starts with clean settings", async () => {
  const first = session({ id: "first-session", prompt_md: "Title: 同名小说\n\n# 第一版" });
  const second = session({ id: "second-session", messages: [{ role: "assistant", content: "第二部作品的完整讨论" }] });
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [summary(first), summary(second)], total: 2 });
  const get = vi.spyOn(workshopApi, "get").mockImplementation(async (id) => id === first.id ? first : second);
  const user = userEvent.setup();
  view();
  await user.click(screen.getByText("已有头脑风暴 · 搜索并恢复指定讨论"));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（first-session）" }));
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "第一部未发送的想法" } });
  await user.click(screen.getByRole("button", { name: "收起故事设计助手 · 第 1 条" }));
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（second-session）" }));
  expect(get).toHaveBeenLastCalledWith("second-session");
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("");
  expect(screen.getByText("第二部作品的完整讨论")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "第二部未发送的想法" } });
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（first-session）" }));
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("第一部未发送的想法");
  expect(screen.getByRole("button", { name: "展开故事设计助手 · 第 1 条" })).toHaveAttribute("aria-expanded", "false");
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(screen.getByRole("button", { name: "新建另一部作品的讨论" }));
  expect(screen.getByLabelText("暂定书名")).toHaveValue("");
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue("");
});

test("an old poll resolving alongside restoration cannot overwrite the selected session", async () => {
  const first = session({ id: "first-session", status: "running" });
  const second = session({ id: "second-session", messages: [{ role: "assistant", content: "已恢复第二个讨论" }] });
  localStorage.setItem("novel-os.workshop.active-session", first.id);
  let finishPoll!: (value: WorkshopSession) => void;
  let finishRestore!: (value: WorkshopSession) => void;
  const pendingPoll = new Promise<WorkshopSession>((resolve) => { finishPoll = resolve; });
  const pendingRestore = new Promise<WorkshopSession>((resolve) => { finishRestore = resolve; });
  let reads = 0;
  vi.spyOn(workshopApi, "get").mockImplementation((id) => id === second.id ? pendingRestore : ++reads <= 2 ? Promise.resolve(first) : pendingPoll);
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [summary(first), summary(second)], total: 2 });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await waitFor(() => expect(reads).toBe(3));
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（second-session）" }));
  expect(screen.getByRole("button", { name: /2.*头脑风暴/ })).toBeDisabled();
  await act(async () => { finishRestore(second); finishPoll(first); });
  expect(await screen.findByText("已恢复第二个讨论")).toBeInTheDocument();
  expect(screen.queryByText("可以把记忆的代价作为人物的内心冲突。")).not.toBeInTheDocument();
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("");
});

test("a failed first round preserves its input without claiming a skeleton already exists", async () => {
  const failed = session({ status: "error", prompt_md: "", messages: [], last_error: "模型等待超时" });
  localStorage.setItem("novel-os.workshop.active-session", failed.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(failed);
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByRole("alert")).toHaveTextContent("基础设定和已提交的对话仍保留。重试会使用已保存的完整上下文。");
  expect(screen.getByRole("alert")).not.toHaveTextContent("已有骨架");
  expect(screen.getByRole("button", { name: "重试本轮" })).toBeEnabled();
});

function timedSession(secondsAgo: number, now: number, patch: Partial<WorkshopSession> = {}) {
  return session({ status: "running", logs: [{ event: "turn_started", time: new Date(now - secondsAgo * 1000).toISOString(), message: "模型正在处理本轮意见" }], ...patch });
}

function runtimeProgress(now: number, patch: Partial<WorkshopRuntimeProgress> = {}): WorkshopRuntimeProgress {
  return { revision: 2, operation: "brainstorm", job_id: "turn-1", attempt: 1, request_id: "request-1",
    started_at: new Date(now - 300_000).toISOString(), deadline_at: new Date(now + 600_000).toISOString(),
    phase: "waiting", last_worker_check_at: new Date(now - 1_000).toISOString(), last_cli_event_at: new Date(now - 3_000).toISOString(),
    last_content_at: null, content_chars: 0, last_upstream_event_at: new Date(now - 3_000).toISOString(),
    upstream_event: "response.completed", configured_max_tokens: 14000, effective_max_tokens: null, output_limit_supported: false, ...patch };
}

test("separates worker and upstream signals from model content without claiming thought or fabricating output", async () => {
  const now = Date.parse("2026-10-08T07:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const current = timedSession(600, now, { runtime_progress: runtimeProgress(now) });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const turn = vi.spyOn(workshopApi, "turn");
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("等待模型回复 · 已用时 300 秒")).toBeInTheDocument();
  const card = screen.getByLabelText("本轮运行状态");
  expect(within(card).getByText("后台最近检查").parentElement).toHaveTextContent("1 秒前");
  expect(within(card).getByText("最近收到模型内容").parentElement).toHaveTextContent("尚未收到");
  expect(within(card).getByText(/已有 5 分钟未收到新内容/)).toBeInTheDocument();
  expect(within(card).queryByText(/模型正在思考|已收到 \d+ 字符/)).not.toBeInTheDocument();
  const details = within(card).getByText("运行详情").parentElement;
  expect(details).not.toHaveAttribute("open");
  await user.click(within(card).getByText("运行详情"));
  expect(details).toHaveAttribute("open");
  expect(within(card).getByText(/本轮最长等待/)).toHaveTextContent("15 分钟");
  expect(within(card).getByText(/最近上游事件/)).toHaveTextContent("response.completed");
  expect(within(card).getByText(/输出预算/)).toHaveTextContent("14000 tokens（配置值）；当前调用方式不支持应用侧输出上限，此值未生效。");
  expect(turn).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "重试本轮" })).not.toBeInTheDocument();
});

test("restores server progress and content receipt across a full remount without resetting elapsed time", async () => {
  let now = Date.parse("2026-10-08T07:00:00Z");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const current = session({ status: "running", logs: [], runtime_progress: runtimeProgress(now, {
    phase: "receiving", last_content_at: new Date(now - 5_000).toISOString(), content_chars: 2821,
    output_limit_supported: true, effective_max_tokens: 14000,
  }) });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  const first = view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("正在接收回复 · 已用时 300 秒")).toBeInTheDocument();
  expect(screen.getByText("最近收到模型内容").parentElement).toHaveTextContent("5 秒前 · 本轮已收到 2821 字符");
  expect(screen.queryByText(/分钟未收到新内容/)).not.toBeInTheDocument();
  expect(screen.getByText(/输出预算/)).toHaveTextContent("实际发送上限 14000 tokens");
  first.unmount();
  now += 60_000;
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("正在接收回复 · 已用时 360 秒")).toBeInTheDocument();
  expect(screen.getByText("最近收到模型内容").parentElement).toHaveTextContent("65 秒前 · 本轮已收到 2821 字符");
});

test("keeps completed response evidence visible during a format retry and after remount", async () => {
  let now = Date.parse("2026-10-08T07:00:00Z");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const current = session({ status: "running", logs: [], runtime_progress: runtimeProgress(now, {
    phase: "waiting", attempt: 3, request_id: "repair-request", content_chars: 0, last_content_at: null,
    cumulative_content_chars: 42684, last_turn_content_at: new Date(now - 10_000).toISOString(),
    last_completed_attempt: 2, repairing_response: true,
  }) });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  const first = view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("正在修复回复格式 · 等待模型回复 · 已用时 300 秒")).toBeInTheDocument();
  expect(screen.getByText("最近收到模型内容").parentElement).toHaveTextContent("10 秒前 · 本轮已收到 42684 字符");
  expect(screen.getByText(/第 2 次请求已返回完整内容，正在修复格式/)).toBeInTheDocument();
  expect(screen.queryByText(/分钟未收到新内容/)).not.toBeInTheDocument();
  await user.click(screen.getByText("运行详情"));
  expect(screen.getByText("本次请求内容：尚未收到")).toBeInTheDocument();
  expect(screen.getByText(/最近完整返回：第 2 次请求/)).toBeInTheDocument();
  first.unmount();
  now += 60_000;
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("正在修复回复格式 · 等待模型回复 · 已用时 360 秒")).toBeInTheDocument();
  expect(screen.getByText("最近收到模型内容").parentElement).toHaveTextContent("70 秒前 · 本轮已收到 42684 字符");
});

test.each([
  ["preparing", "正在准备本轮讨论"], ["starting", "正在启动模型请求"], ["parsing", "正在整理回复"],
  ["validating", "正在检查骨架完整性"], ["repairing", "正在修复回复格式"],
] as const)("shows the actual %s stage instead of continuing to report model waiting", async (phase, label) => {
  const now = Date.parse("2026-10-08T07:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const current = session({ status: "running", runtime_progress: runtimeProgress(now, { phase, attempt: 2 }) });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText(`${label} · 已用时 300 秒`)).toBeInTheDocument();
  expect(screen.getByText(/请求尝试/)).toHaveTextContent("第 2 次");
  expect(screen.queryByText(/分钟未收到新内容/)).not.toBeInTheDocument();
});

test.each([{ job_id: "previous-job" }, { revision: 1 }, { operation: "titles" as const }])("ignores progress from another job, revision or operation (%j)", async (mismatch) => {
  const now = Date.parse("2026-10-08T07:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const current = timedSession(10, now, { runtime_progress: runtimeProgress(now, { ...mismatch, phase: "receiving", content_chars: 9000 }) });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByText("等待模型回复 · 已用时 10 秒")).toBeInTheDocument();
  expect(screen.queryByText(/已收到 9000 字符|300 秒/)).not.toBeInTheDocument();
  expect(screen.getByText("最近收到模型内容").parentElement).toHaveTextContent("尚未收到");
});

test("restores elapsed time from durable logs across closing and a full browser remount", async () => {
  let now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const current = timedSession(300, now);
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const props = { seed, onClose: vi.fn(), onBack: vi.fn() };
  const currentView = render(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("等待模型回复 · 已用时 300 秒")).toBeInTheDocument();
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open={false} /></MemoryRouter>);
  now += 60_000;
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  expect(await screen.findByText("等待模型回复 · 已用时 360 秒")).toBeInTheDocument();
  currentView.unmount();
  now += 60_000;
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("等待模型回复 · 已用时 420 秒")).toBeInTheDocument();
});

test("switches between each session's persisted latest turn without restarting either clock", async () => {
  const now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const first = timedSession(300, now, { id: "first-session", job_id: "first-job" });
  const second = timedSession(120, now, { id: "second-session", job_id: "second-job" });
  second.logs.unshift({ event: "turn_started", time: new Date(now - 1_800_000).toISOString(), message: "上一轮" });
  localStorage.setItem("novel-os.workshop.active-session", first.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async (id) => id === first.id ? first : second);
  vi.spyOn(api, "getJob").mockImplementation(async (id) => ({ job_id: id, kind: "workshop", status: "running", error: null }));
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [summary(first), summary(second)], total: 2 });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("等待模型回复 · 已用时 300 秒")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（second-session）" }));
  expect(await screen.findByText("等待模型回复 · 已用时 120 秒")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（first-session）" }));
  expect(await screen.findByText("等待模型回复 · 已用时 300 秒")).toBeInTheDocument();
});

test.each([false, true])("a retry starts a new clock before fresh logs arrive (server timestamp: %s)", async (hasServerTimestamp) => {
  const now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const failed = timedSession(600, now, { status: "error", last_error: "上一轮超时" });
  localStorage.setItem("novel-os.workshop.active-session", failed.id);
  let resolvePoll!: (value: WorkshopSession) => void;
  const freshPoll = new Promise<WorkshopSession>((resolve) => { resolvePoll = resolve; });
  vi.spyOn(workshopApi, "get").mockResolvedValueOnce(failed).mockResolvedValueOnce(failed).mockReturnValue(freshPoll);
  vi.spyOn(workshopApi, "turn").mockResolvedValue({ job_id: "retry-job", kind: "workshop", status: "running", error: null, started_at: hasServerTimestamp ? new Date(now - 1_000).toISOString() : undefined });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "retry-job", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "重试本轮" }));
  expect(await screen.findByText(hasServerTimestamp ? "等待模型回复 · 已用时 1 秒" : "等待模型回复 · 正在同步开始时间")).toBeInTheDocument();
  await act(async () => { resolvePoll({ ...failed, status: "running", job_id: "retry-job", last_error: null, logs: [...failed.logs,
    { event: "turn_started", time: new Date(now - 2_000).toISOString(), message: "重试开始" }] }); });
  expect(await screen.findByText("等待模型回复 · 已用时 2 秒")).toBeInTheDocument();
  expect(screen.queryByText(/已用时 600 秒/)).not.toBeInTheDocument();
});

test.each(["missing", "invalid latest"])("shows an unavailable timer for %s persisted start time", async (kind) => {
  const now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const current = timedSession(300, now);
  if (kind === "missing") current.logs = [];
  else current.logs.push({ event: "turn_started", time: "invalid date", message: "最新一轮" });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("等待模型回复 · 开始时间暂不可用")).toBeInTheDocument();
  expect(screen.queryByText(/已用时|NaN 秒/)).not.toBeInTheDocument();
});

test("failed staged delivery restores saved results and resumes explicitly without adding a new turn", async () => {
  const current = session({ status: "error", last_error: "章节规划请求超时", active_operation: "brainstorm", delivery_progress: {
    status: "error", completed_stages: ["story_design"], active_stage: "chapter_plan", source_revision: 2, resumable: true,
    stages: [{ stage: "story_design", status: "saved", content_md: "## 已保存的故事设计\n\nNora 保留证据。" }],
  } });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const turn = vi.spyOn(workshopApi, "turn");
  const resume = vi.spyOn(workshopApi, "resume").mockResolvedValue({ job_id: "resumed-stages", kind: "workshop", status: "running", error: null });
  const user = userEvent.setup();
  const props = { open: true, seed, onClose: vi.fn(), onBack: vi.fn(), initialSessionId: current.id };
  const first = render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("已保存 1/3 个阶段");
  expect(screen.getByRole("button", { name: "从已保存阶段继续" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "重试本轮" })).not.toBeInTheDocument();
  await user.click(screen.getByText("查看已保存的全局故事设计"));
  expect(screen.getByRole("heading", { name: "已保存的故事设计" })).toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  first.unmount();
  render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("已保存 1/3 个阶段");
  expect(resume).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
  vi.mocked(workshopApi.get).mockResolvedValue({ ...current, status: "running", last_error: null, job_id: "resumed-stages",
    delivery_progress: { ...current.delivery_progress!, status: "running", resumable: false } });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "resumed-stages", kind: "workshop", status: "running", error: null });
  await user.click(screen.getByRole("button", { name: "从已保存阶段继续" }));
  await waitFor(() => expect(resume).toHaveBeenCalledExactlyOnceWith(current.id, current.revision));
  expect(turn).not.toHaveBeenCalled();
  expect(screen.getByText("已保存 1/3 个阶段")).toBeInTheDocument();
});

test("refreshing a running delivery restores stage and native activity without restarting requests", async () => {
  const now = Date.parse("2026-10-12T07:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const current = session({ status: "running", active_operation: "brainstorm", delivery_progress: {
    status: "running", completed_stages: ["story_design"], active_stage: "chapter_plan", source_revision: 2, resumable: false,
  }, runtime_progress: runtimeProgress(now, { delivery_stage: "chapter_plan", stage_index: 2, completed_stages: 1,
    activity_time_basis: "batch_observed", last_reasoning_observed_at: new Date(now - 4000).toISOString(), reasoning_event_count: 3,
  }) });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: current.job_id!, kind: "workshop", status: "running", error: null });
  const turn = vi.spyOn(workshopApi, "turn"); const resume = vi.spyOn(workshopApi, "resume");
  const props = { open: true, seed, onClose: vi.fn(), onBack: vi.fn(), initialSessionId: current.id };
  const first = render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("完整章节规划 · 等待模型回复 · 已用时 300 秒");
  expect(screen.getByText("最近观测到推理摘要").parentElement).toHaveTextContent("4 秒前");
  expect(screen.getByText("已保存 1/3 个阶段")).toBeInTheDocument();
  first.unmount();
  render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("完整章节规划 · 等待模型回复 · 已用时 300 秒");
  expect(screen.getByText("最近观测到推理摘要").parentElement).toHaveTextContent("4 秒前");
  expect(turn).not.toHaveBeenCalled(); expect(resume).not.toHaveBeenCalled();
});

test.each(["error", "paused"] as const)("%s assembly restores saved parts across refresh and resumes only on request", async (status) => {
  const current = session({ status, last_error: status === "error" ? "交付部分请求超时" : null, active_operation: "brainstorm", delivery_progress: {
    status, completed_stages: ["story_design", "chapter_plan"], active_stage: "assembly", resumable: true,
    assembly: { completed_parts: 1, total_parts: 2, active_part: "world", parts: [
      { part: "people", label: "人物与关系", status: "saved", content_md: "已保存的人物关系。" },
      { part: "world", label: "世界与线索", status: "running" },
    ] },
  } });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const resume = vi.spyOn(workshopApi, "resume").mockResolvedValue({ job_id: "resume-part", kind: "workshop", status: "running", error: null });
  const turn = vi.spyOn(workshopApi, "turn");
  const user = userEvent.setup();
  const props = { open: true, seed, onClose: vi.fn(), onBack: vi.fn(), initialSessionId: current.id };
  const first = render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("已保存 1/2 个部分");
  expect(screen.getByRole("button", { name: "从已保存部分继续" })).toBeEnabled();
  expect(screen.getByText("待继续：世界与线索")).toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  first.unmount();
  render(<MemoryRouter><NovelWorkshop {...props} /></MemoryRouter>);
  await screen.findByText("已保存 1/2 个部分");
  expect(resume).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  vi.mocked(workshopApi.get).mockResolvedValue({ ...current, status: "running", job_id: "resume-part", last_error: null,
    delivery_progress: { ...current.delivery_progress!, status: "running", resumable: false } });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "resume-part", kind: "workshop", status: "running", error: null });
  await user.click(screen.getByRole("button", { name: "从已保存部分继续" }));
  await waitFor(() => expect(resume).toHaveBeenCalledExactlyOnceWith(current.id, current.revision));
  expect(turn).not.toHaveBeenCalled();
  expect(await screen.findByText("正在补全：世界与线索")).toBeInTheDocument();
});

test("an invalid checkpoint explains why recovery is unavailable and cannot invoke resume", async () => {
  const current = session({ status: "error", last_error: "阶段内容校验失败", delivery_progress: {
    status: "error", completed_stages: [], active_stage: null, source_revision: 1, resumable: false, invalid_reason: "基础设定版本已改变",
  } });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const resume = vi.spyOn(workshopApi, "resume");
  render(<MemoryRouter><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} initialSessionId={current.id} /></MemoryRouter>);
  await screen.findByText("阶段恢复暂不可用：基础设定版本已改变");
  expect(screen.queryByRole("button", { name: "从已保存阶段继续" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试本轮" })).toBeEnabled();
  expect(resume).not.toHaveBeenCalled();
});

test.each(["error", "paused"] as const)("saved assembly can finish local publication after %s without offering another model inference", async (status) => {
  const current = session({ status, last_error: status === "error" ? "最终保存中断" : null, delivery_progress: {
    status: "complete", completed_stages: ["story_design", "chapter_plan", "assembly"], active_stage: null, source_revision: 2, resumable: true,
  } });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const resume = vi.spyOn(workshopApi, "resume").mockResolvedValue({ job_id: "finish-save", kind: "workshop", status: "running", error: null });
  const turn = vi.spyOn(workshopApi, "turn");
  const user = userEvent.setup();
  render(<MemoryRouter><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} initialSessionId={current.id} /></MemoryRouter>);
  await screen.findByText("已保存 3/3 个阶段");
  expect(screen.getByText(/全部阶段成果已保存.*无需再次调用模型/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "从已保存阶段继续" })).not.toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled(); expect(turn).not.toHaveBeenCalled();
  vi.mocked(workshopApi.get).mockResolvedValue({ ...current, status: "ready", last_error: null, job_id: "finish-save" });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "finish-save", kind: "workshop", status: "done", error: null });
  await user.click(screen.getByRole("button", { name: "完成骨架保存" }));
  await waitFor(() => expect(resume).toHaveBeenCalledExactlyOnceWith(current.id, current.revision));
  expect(turn).not.toHaveBeenCalled();
});


test("pauses the exact running job, ignores its late poll and resumes without discarding local corrections", async () => {
  let now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const initial = timedSession(300, now);
  const paused = { ...initial, status: "paused" as const, revision: 3, paused_at: new Date(now).toISOString(),
    logs: [...initial.logs, { event: "turn_paused", time: new Date(now).toISOString(), message: "用户暂停了本轮" }] };
  localStorage.setItem("novel-os.workshop.active-session", initial.id);
  let finishOldPoll!: (value: WorkshopSession) => void;
  const oldPoll = new Promise<WorkshopSession>((resolve) => { finishOldPoll = resolve; });
  const get = vi.spyOn(workshopApi, "get").mockResolvedValueOnce(initial).mockResolvedValueOnce(initial).mockReturnValueOnce(oldPoll).mockResolvedValue(paused);
  vi.spyOn(api, "getJob").mockImplementation(async (id) => ({ job_id: id, kind: "workshop", status: "running", error: null }));
  let finishPause!: (value: WorkshopSession) => void;
  const pauseReply = new Promise<WorkshopSession>((resolve) => { finishPause = resolve; });
  const pause = vi.spyOn(workshopApi, "pause").mockReturnValue(pauseReply);
  const resume = vi.spyOn(workshopApi, "resume").mockImplementation(async () => {
    get.mockResolvedValue({ ...paused, status: "running", revision: 4, job_id: "resumed-job", paused_at: null,
      logs: [...paused.logs, { event: "turn_started", time: new Date(now).toISOString(), message: "重新开始本轮" }] });
    return { job_id: "resumed-job", kind: "workshop", status: "running", error: null, started_at: new Date(now).toISOString() };
  });
  const props = { seed, onClose: vi.fn(), onBack: vi.fn() };
  const currentView = render(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "暂停后要补充的意见" } });
  await user.click(screen.getByRole("button", { name: "暂停推导" }));
  expect(pause).toHaveBeenCalledWith(initial.id, initial.revision, initial.job_id);
  expect(screen.getByRole("button", { name: "正在暂停…" })).toBeDisabled();
  await act(async () => { finishPause(paused); finishOldPoll(initial); });
  expect(await screen.findByText("推导已暂停 · 本轮已运行 300 秒")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "继续推导" })).toBeEnabled();
  expect(screen.queryByText(/等待模型回复 ·/)).not.toBeInTheDocument();
  now += 60_000;
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open={false} /></MemoryRouter>);
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  expect(screen.getByText("推导已暂停 · 本轮已运行 300 秒")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /1.*基础设定/ }));
  expect(screen.getByRole("button", { name: "修改基础设定" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: /2.*头脑风暴/ }));
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("暂停后要补充的意见");
  await user.click(screen.getByRole("button", { name: "继续推导" }));
  expect(resume).toHaveBeenCalledWith(initial.id, 3);
  expect(await screen.findByText("等待模型回复 · 已用时 0 秒")).toBeInTheDocument();
  expect(screen.getByLabelText(/补充、回答或修改设定/)).toHaveValue("暂停后要补充的意见");
  expect(screen.getByText("可以把记忆的代价作为人物的内心冲突。")).toBeInTheDocument();
});

test("pauses and resumes title recommendations in preview while retaining title edits", async () => {
  let current = session({ status: "running", active_operation: "titles", naming: { source_sha256: "outline", confirmed_title: null,
    candidates: [{ title: "Nora Remembers", reason: "突出记忆主题" }], requests: [] } });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  vi.spyOn(api, "getJob").mockImplementation(async (id) => ({ job_id: id, kind: "workshop.titles", status: "running", error: null }));
  vi.spyOn(workshopApi, "pause").mockImplementation(async () => { current = { ...current, status: "paused", revision: 3, paused_at: new Date().toISOString() }; return current; });
  const resume = vi.spyOn(workshopApi, "resume").mockImplementation(async () => { current = { ...current, status: "running", job_id: "titles-resumed", revision: 4 }; return { job_id: "titles-resumed", kind: "workshop.titles", status: "running", error: null }; });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(screen.getByRole("region", { name: "小说书名确认" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "暂停推荐" }));
  expect(await screen.findByText("书名推荐已暂停")).toBeInTheDocument();
  await user.type(screen.getByLabelText("最终书名"), "My Title");
  await user.type(screen.getByLabelText("书名优化要求"), "保持短标题");
  await user.click(screen.getByRole("button", { name: "继续推荐" }));
  expect(resume).toHaveBeenCalledWith(current.id, 3);
  expect(await screen.findByRole("button", { name: "暂停推荐" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "小说书名确认" })).toBeInTheDocument();
  expect(screen.getByLabelText("最终书名")).toHaveValue("My Title");
  expect(screen.getByLabelText("书名优化要求")).toHaveValue("保持短标题");
});

test("a pause conflict refreshes the finished session instead of claiming it was paused", async () => {
  let current = session({ status: "running" });
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockImplementation(async () => current);
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "turn-1", kind: "workshop", status: "running", error: null });
  vi.spyOn(workshopApi, "pause").mockImplementation(async () => { current = { ...current, status: "ready", revision: 4 }; throw new Error("HTTP 409：本轮已完成"); });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "暂停推导" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本轮已完成");
  expect(screen.queryByRole("button", { name: "继续推导" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "暂停推导" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/补充、回答或修改设定/), { target: { value: "新的意见" } });
  expect(screen.getByRole("button", { name: "发送并继续推导" })).toBeEnabled();
});

test("restores a paused session from the task list without polling its cancelled job", async () => {
  const now = Date.parse("2026-10-07T14:00:00Z");
  const current = timedSession(300, now, { status: "paused", paused_at: new Date(now).toISOString() });
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [summary(current)], total: 1 });
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const job = vi.spyOn(api, "getJob").mockRejectedValue(new Error("cancelled"));
  const user = userEvent.setup();
  view();
  await user.click(screen.getByText("已有头脑风暴 · 搜索并恢复指定讨论"));
  expect(await screen.findByText("已暂停")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "恢复讨论 同名小说（session-1）" }));
  expect(await screen.findByText("推导已暂停 · 本轮已运行 300 秒")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "继续推导" })).toBeEnabled();
  expect(job).not.toHaveBeenCalled();
});

test("an old cancelled job cannot mark a session resumed in another tab as failed", async () => {
  const now = Date.parse("2026-10-07T14:00:00Z");
  vi.spyOn(Date, "now").mockReturnValue(now);
  const old = timedSession(300, now, { job_id: "cancelled-job" });
  const resumed = timedSession(5, now, { job_id: "resumed-in-another-tab", revision: 4 });
  localStorage.setItem("novel-os.workshop.active-session", old.id);
  vi.spyOn(workshopApi, "get").mockResolvedValueOnce(old).mockResolvedValueOnce(old).mockResolvedValue(resumed);
  const getJob = vi.spyOn(api, "getJob").mockImplementation(async (id) => ({ job_id: id, kind: "workshop",
    status: id === old.job_id ? "error" : "running", error: id === old.job_id ? "Old request was cancelled" : null }));
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await waitFor(() => expect(getJob).toHaveBeenCalledWith("resumed-in-another-tab"));
  expect(await screen.findByText("等待模型回复 · 已用时 5 秒")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "暂停推导" })).toBeEnabled();
  expect(screen.queryByText("Old request was cancelled")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试本轮" })).not.toBeInTheDocument();
});
