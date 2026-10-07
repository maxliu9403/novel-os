import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import { api } from "../api/client";
import { workshopApi, type WorkshopSession } from "../api/workshop";
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
    validation: { mode: "standalone_long", chapters: 80, target_words: 80000 }, output_formats: ["markdown", "html", "docx", "epub", "pdf"] } };
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
  expect(turn).toHaveBeenCalledWith("session-1", expect.any(String), 0);
  expect(screen.getByText(/不限讨论次数/)).toBeInTheDocument();
  expect(localStorage.getItem("novel-os.workshop.active-session")).toBe("session-1");
});

test("renders the skeleton table, confirms its revision and launches the complete existing pipeline", async () => {
  const current = session();
  localStorage.setItem("novel-os.workshop.active-session", current.id);
  vi.spyOn(workshopApi, "get").mockResolvedValue(current);
  const prepare = vi.spyOn(workshopApi, "prepare").mockResolvedValue(prepared(current));
  const launch = vi.spyOn(workshopApi, "launch").mockResolvedValue({ project_id: "nora", job_id: "full-run" });
  vi.spyOn(api, "getJob").mockResolvedValue({ job_id: "full-run", kind: "pipeline", status: "error", error: "Writer connection failed" });
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.getByRole("table")).toHaveTextContent("Nora");
  expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载骨架 MD" })).toHaveAttribute("href", expect.stringContaining("/sessions/session-1/prompt"));
  await user.click(screen.getByRole("button", { name: "确认骨架并生成命令" }));
  expect(prepare).toHaveBeenCalledWith(current.id, current.revision);
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
  expect(screen.queryByText(/正在推导 ·/)).not.toBeInTheDocument();
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
    expect(turn).toHaveBeenLastCalledWith(current.id, `修改伏笔 ${round}`, revision);
  }
  await user.click(screen.getByRole("button", { name: "预览当前骨架" }));
  expect(screen.queryByRole("button", { name: "一键启动全书创作" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认骨架并生成命令" })).toBeEnabled();
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
  expect(screen.getByRole("button", { name: "确认骨架并生成命令" })).toBeDisabled();
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
  expect(await screen.findByText("正在推导 · 已等待 300 秒")).toBeInTheDocument();
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open={false} /></MemoryRouter>);
  now += 60_000;
  currentView.rerender(<MemoryRouter><NovelWorkshop {...props} open /></MemoryRouter>);
  expect(await screen.findByText("正在推导 · 已等待 360 秒")).toBeInTheDocument();
  currentView.unmount();
  now += 60_000;
  view();
  await user.click(await screen.findByRole("button", { name: "继续上次讨论" }));
  expect(await screen.findByText("正在推导 · 已等待 420 秒")).toBeInTheDocument();
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
  expect(await screen.findByText("正在推导 · 已等待 300 秒")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（second-session）" }));
  expect(await screen.findByText("正在推导 · 已等待 120 秒")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "切换讨论" }));
  await user.click(await screen.findByRole("button", { name: "恢复讨论 同名小说（first-session）" }));
  expect(await screen.findByText("正在推导 · 已等待 300 秒")).toBeInTheDocument();
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
  expect(await screen.findByText(hasServerTimestamp ? "正在推导 · 已等待 1 秒" : "正在推导 · 正在同步开始时间")).toBeInTheDocument();
  await act(async () => { resolvePoll({ ...failed, status: "running", job_id: "retry-job", last_error: null, logs: [...failed.logs,
    { event: "turn_started", time: new Date(now - 2_000).toISOString(), message: "重试开始" }] }); });
  expect(await screen.findByText("正在推导 · 已等待 2 秒")).toBeInTheDocument();
  expect(screen.queryByText(/已等待 600 秒/)).not.toBeInTheDocument();
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
  expect(await screen.findByText("正在推导 · 开始时间暂不可用")).toBeInTheDocument();
  expect(screen.queryByText(/已等待|NaN 秒/)).not.toBeInTheDocument();
});
