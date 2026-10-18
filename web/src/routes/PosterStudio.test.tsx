import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import PosterStudio from "./PosterStudio";
import { api } from "../api/client";
import { posterApi, type PosterOverview, type PosterTask } from "../api/poster";

function fixture(): PosterOverview {
  return {enabled: true, title: "The Door Is Mine", source: {bytes: 1234, sha256: "source-sha", preview: "The complete source", path: "outputs/input/prompt.md"}, active_cover_set_id: "", selected_candidate_id: "", active_revision: 4,
    plans: [{plan_id: "plan-1", plan_hash: "plan-sha", pipeline_kind: "skeleton_poster", created_at: "2026-10-16T08:00:00Z", source_sha256: "source-sha", status: "approved", story: {premise: "Two families, one decision", core_conflict: "Belonging and independence", emotional_promise: "A new beginning", cast: [{name: "Mara", role: "Protagonist", identity: "Mother", relationship: "Daughter and mother"}], world: "Contemporary", hard_constraints: []}, directions: [{id: "concept-1", title: "A shared doorway", story_promise: "A contested home", composition: "One foreground character and two background figures", medium: "Cinematic", palette: ["amber", "blue"], characters: ["Mara", "Oren"], image_prompt: "A unified poster", title_position: "bottom"}]}],
    sets: [{cover_set_id: "set-1", project_id: "book", plan_id: "plan-1", requested_count: 2, revision: 7, active_revision: 4, status: "partial", selected_candidate_id: "", source_prompt_sha256: "source-sha", foundation_sha256: "", created_at: "2026-10-16T09:00:00Z", updated_at: "2026-10-16T09:01:00Z", brief: {title: "The Door Is Mine"}, concepts: [{concept_id: "concept-1", visual_strategy: "A shared doorway", focal_scene: "A mother decides", composition: "Unified depth", palette: "blue", secondary_signal: "door", title_treatment: "bottom"}], candidates: [
      {candidate_id: "candidate-1", concept_id: "concept-1", status: "ready", url: "/api/image-1", relative_path: "cover.jpg", media_id: "media-1", sha256: "image-sha", width: 1024, height: 1536, content_type: "image/jpeg", error: "", poster_metadata: {typography_available: true, requested_model: "auto", observed_model: ""}, quality_report: {status: "blocked", blockers: ["Ambiguous relation"], repair_codes: [], evidence: []}},
      {candidate_id: "candidate-2", concept_id: "concept-1", status: "failed", url: null, relative_path: "", media_id: "", sha256: "", width: 0, height: 0, content_type: "", error: "HTTP 429"},
    ]}], tasks: []};
}
const task = (kind = "batch"): PosterTask => ({task_id: "task-1", kind, status: "running", stage: "保存图片", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), completed: 1, total: 2, result: {cover_set_id: "set-1"}});
function renderStudio(query = "") { return render(<MemoryRouter initialEntries={[`/projects/book/covers${query}`]}><PosterStudio projectId="book" /></MemoryRouter>); }
beforeEach(() => { sessionStorage.clear(); vi.spyOn(posterApi, "overview").mockResolvedValue(fixture()); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("completed candidates remain directly selectable during generation and despite advisory blockers", async () => {
  const data = fixture(); data.tasks = [task()]; vi.mocked(posterApi.overview).mockResolvedValue(data);
  const select = vi.spyOn(posterApi, "select").mockImplementation(async () => { data.active_cover_set_id = "set-1"; data.selected_candidate_id = "candidate-1"; return data.sets[0]; });
  const user = userEvent.setup(); renderStudio();
  const card = await screen.findByRole("article", {name: "候选 1"});
  expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
  await user.click(within(card).getByRole("button", {name: "使用这张"}));
  expect(select).toHaveBeenCalledWith("book", "set-1", "candidate-1", 7, "image-sha", 4);
  await waitFor(() => expect(within(card).getByRole("button", {name: "当前使用"})).toBeDisabled());
  expect(screen.getByText(/交付包将使用这个版本/)).toBeInTheDocument();
});
test("failed candidates have their own explicit retry, and simply reopening never generates", async () => {
  const retry = vi.spyOn(posterApi, "retry").mockResolvedValue(task("retry"));
  const plan = vi.spyOn(posterApi, "plan"); const generate = vi.spyOn(posterApi, "generate");
  const user = userEvent.setup(); renderStudio();
  const card = await screen.findByRole("article", {name: "候选 2"});
  expect(retry).not.toHaveBeenCalled(); expect(plan).not.toHaveBeenCalled(); expect(generate).not.toHaveBeenCalled();
  await user.click(within(card).getByRole("button", {name: "重试这一张"}));
  expect(retry).toHaveBeenCalledWith("book", expect.any(String), "set-1", ["candidate-2"]);
});
test("an ambiguous submission failure reuses its same request key when the user retries", async () => {
  const retry = vi.spyOn(posterApi, "retry").mockRejectedValueOnce(new Error("connection lost")).mockResolvedValueOnce(task("retry"));
  const user = userEvent.setup(); renderStudio();
  const card = await screen.findByRole("article", {name: "候选 2"});
  await user.click(within(card).getByRole("button", {name: "重试这一张"}));
  await screen.findByRole("alert");
  await user.click(within(card).getByRole("button", {name: "重试这一张"}));
  expect(retry).toHaveBeenCalledTimes(2); expect(retry.mock.calls[0][1]).toBe(retry.mock.calls[1][1]);
});
test("failed refinement restores the saved prompt and references the original completed candidate", async () => {
  const data = fixture(); data.tasks = [{...task("refine"), status: "failed", error: "image service timeout", input: {cover_set_id: "set-1", candidate_id: "candidate-1", prompt: "Keep the cast; warmer light", keep_composition: true}}]; vi.mocked(posterApi.overview).mockResolvedValue(data);
  const refine = vi.spyOn(posterApi, "refine").mockResolvedValue(task("refine"));
  const user = userEvent.setup(); renderStudio();
  const card = await screen.findByRole("article", {name: "候选 1"});
  expect(within(card).getByRole("img")).toHaveAttribute("src", expect.stringContaining("/api/image-1"));
  await user.click(within(card).getByRole("button", {name: "重试精修"}));
  const dialog = screen.getByRole("dialog", {name: "原图精修"});
  expect(within(dialog).getByRole("textbox")).toHaveValue("Keep the cast; warmer light");
  await user.click(within(dialog).getByRole("button", {name: "开始精修这一张"}));
  expect(refine).toHaveBeenCalledWith("book", expect.any(String), "set-1", "candidate-1", "Keep the cast; warmer light", true);
});
test("full source viewing and completed-plan history do not launch an analysis chain", async () => {
  const user = userEvent.setup(); renderStudio("?poster_tab=plan");
  await screen.findByText("Two families, one decision");
  expect(screen.queryByText("美术方向已生成")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "查看来源"}));
  expect(within(screen.getByRole("dialog")).getByText("The complete source")).toBeInTheDocument();
});
test("a stale plan cannot generate but its already completed cover remains usable", async () => {
  const data = fixture(); data.plans[0].stale = true; vi.mocked(posterApi.overview).mockResolvedValue(data);
  const user = userEvent.setup(); renderStudio("?poster_tab=plan");
  expect(await screen.findByRole("button", {name: "按此方案生成 1 张"})).toBeDisabled();
  await user.click(screen.getByRole("tab", {name: /候选封面/}));
  expect(await screen.findByRole("button", {name: "使用这张"})).toBeEnabled();
});
test("direction editing saves visible composition and chosen cast together for the next generation", async () => {
  const data = fixture(); data.plans[0].story.cast.push({name: "Oren", role: "Supporting character", identity: "Neighbor", relationship: "Family friend"});
  vi.mocked(posterApi.overview).mockResolvedValue(data);
  const edit = vi.spyOn(posterApi, "editPlan").mockImplementation(async (_projectId, plan, directions) => ({...plan, plan_id: "plan-edited", directions}));
  const generate = vi.spyOn(posterApi, "generate");
  const user = userEvent.setup(); renderStudio("?poster_tab=plan");
  await user.click(await screen.findByRole("button", {name: "调整这个方向"}));
  const dialog = screen.getByRole("dialog", {name: /调整方向/});
  expect(within(dialog).getByText(/人物、构图和风格修改会用于下一次出图/)).toBeVisible();
  await user.click(within(dialog).getByRole("checkbox", {name: /Oren/}));
  const composition = within(dialog).getByRole("textbox", {name: "人物与画面构图"});
  await user.clear(composition); await user.type(composition, "Mara framed by two opposing homes");
  await user.click(within(dialog).getByRole("button", {name: "保存新方案"}));
  expect(edit).toHaveBeenCalledWith("book", data.plans[0], [expect.objectContaining({characters: ["Mara"], composition: "Mara framed by two opposing homes"})]);
  expect(generate).not.toHaveBeenCalled();
});
test("unobserved model identity is shown as unknown without substituting the text model", async () => {
  renderStudio(); await screen.findAllByText("版本与模型");
  expect(screen.getAllByText("未返回，无法确认")).toHaveLength(2);
});
test("typography preview uses local rendered bytes and saves a new version without selecting it", async () => {
  const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:rendered-preview");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const preview = vi.spyOn(posterApi, "typographyPreview").mockResolvedValue(new Blob(["png"]));
  const save = vi.spyOn(posterApi, "typography").mockResolvedValue({...task("typography"), status: "complete"});
  const select = vi.spyOn(posterApi, "select");
  const user = userEvent.setup(); renderStudio();
  await user.click(await screen.findByRole("button", {name: "标题排版"}));
  await waitFor(() => expect(preview).toHaveBeenCalled());
  expect(create).toHaveBeenCalled();
  await user.click(await screen.findByRole("button", {name: "保存为新封面版本"}));
  expect(save).toHaveBeenCalledWith("book", expect.any(String), "set-1", "candidate-1", expect.objectContaining({position: "bottom"}));
  expect(select).not.toHaveBeenCalled();
});

test("typography keeps saved effects and pinned fonts until the user changes the font", async () => {
  const data = fixture(); data.sets[0].candidates[0].poster_metadata!.title_layout = {
    font_id: "serif-bold", font_sha256: "frozen-font", fonts: {"serif-bold": "frozen-font"},
    shadow: {color: "#000000", opacity: .3, blur: .003, offset_x: 0, offset_y: .002},
    stroke: {color: "#17213f", width: .0015}, backdrop: {color: "#071326", opacity: .4, mode: "gradient", direction: "left"},
  };
  vi.mocked(posterApi.overview).mockResolvedValue(data);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:type-preview"); vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const preview = vi.spyOn(posterApi, "typographyPreview").mockResolvedValue(new Blob(["png"]));
  const user = userEvent.setup(); renderStudio(); await user.click(await screen.findByRole("button", {name: "标题排版"}));
  await waitFor(() => expect(preview).toHaveBeenCalled());
  await user.click(screen.getByText("描边、阴影与背景"));
  expect(screen.getByRole("checkbox", {name: "柔和阴影"})).toBeChecked();
  expect(screen.getByRole("checkbox", {name: "轻描边"})).toBeChecked();
  fireEvent.change(screen.getByRole("slider", {name: "底衬浓度"}), {target: {value: "0.6"}});
  await waitFor(() => expect(preview.mock.lastCall?.[3]).toMatchObject({font_sha256: "frozen-font", backdrop: {opacity: .6, mode: "gradient", direction: "left"}, shadow: {opacity: .3}, stroke: {width: .0015}}));
  await user.click(screen.getByRole("checkbox", {name: "柔和阴影"}));
  await waitFor(() => expect(preview.mock.lastCall?.[3]).toMatchObject({font_sha256: "frozen-font", shadow: {opacity: 0}}));
  await user.selectOptions(screen.getByRole("combobox", {name: "字体", exact: true}), "sans-bold");
  await waitFor(() => expect(preview.mock.lastCall?.[3].font_id).toBe("sans-bold"));
  expect(preview.mock.lastCall?.[3].font_sha256).toBeUndefined();
  expect(preview.mock.lastCall?.[3].shadow?.opacity).toBe(0);
});

test("a failed refinement child shows the original image and retries the failed child checkpoint", async () => {
  const data = fixture(); const parent = data.sets[0];
  const child = {...parent, cover_set_id: "child-set", created_at: "2026-10-16T10:00:00Z",
    repair_lineage: {parent_cover_set_id: "set-1", parent_candidate_id: "candidate-1", target_concept_id: "concept-1", operation: "image_repair"},
    candidates: [{...parent.candidates[1], candidate_id: "child-candidate", error: "Title composition failed"}]};
  data.sets = [child, parent];
  const retry = vi.spyOn(posterApi, "retry").mockResolvedValue(task("retry"));
  vi.mocked(posterApi.overview).mockResolvedValue(data);
  const user = userEvent.setup(); renderStudio();
  const card = await screen.findByRole("article", {name: "候选 1"});
  expect(within(card).getByRole("img")).toHaveAttribute("src", expect.stringContaining("/api/image-1"));
  expect(within(card).getByText("原封面 · 新版本未完成")).toBeInTheDocument();
  expect(within(card).getByRole("button", {name: "使用原封面"})).toBeEnabled();
  await user.click(within(card).getByRole("button", {name: "重试这一张"}));
  expect(retry).toHaveBeenCalledWith("book", expect.any(String), "child-set", ["child-candidate"]);
});

test("a bookmarked costs tab opens the new drawer and closing it stays closed after a refresh", async () => {
  vi.spyOn(api, "generationCosts").mockRejectedValue(new Error("No cost fixture"));
  const user = userEvent.setup(); renderStudio("?tab=costs&context=keep");
  const drawer = await screen.findByRole("dialog", {name: "模型与费用"});
  await user.click(within(drawer).getByRole("button", {name: "关闭面板"}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", {name: /设计方案/}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
test("a migrated analysis link opens the complete-source drawer without a planning request", async () => {
  const plan = vi.spyOn(posterApi, "plan"); renderStudio("?poster_drawer=source");
  expect(await screen.findByRole("dialog", {name: "完整骨架来源"})).toBeInTheDocument();
  expect(plan).not.toHaveBeenCalled();
});

test("delivery failure preserves the selected cover and requires a local package retry before download", async () => {
  const data = fixture(); data.active_cover_set_id = "set-1"; data.selected_candidate_id = "candidate-1";
  data.delivery = {status: "failed", error: "EPUB replacement failed", retryable: true};
  vi.mocked(posterApi.overview).mockImplementation(async () => structuredClone(data));
  const rebuild = vi.spyOn(posterApi, "rebuildDelivery").mockImplementation(async () => {
    data.delivery = {status: "ready", retryable: false}; return {delivery: data.delivery};
  });
  const generate = vi.spyOn(posterApi, "generate"); const select = vi.spyOn(posterApi, "select");
  const user = userEvent.setup(); renderStudio();
  expect(await screen.findByRole("button", {name: "当前使用"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "交付包"})).toBeDisabled();
  expect(screen.queryByRole("link", {name: "交付包"})).not.toBeInTheDocument();
  const status = screen.getByRole("status", {name: "交付包同步状态"});
  expect(status).toHaveTextContent("封面选择已保存"); expect(status).toHaveTextContent("EPUB replacement failed");
  await user.click(within(status).getByRole("button", {name: "重新生成交付包"}));
  expect(rebuild).toHaveBeenCalledExactlyOnceWith("book");
  expect(await screen.findByRole("link", {name: "交付包"})).toHaveAttribute("download", "book-package.zip");
  expect(screen.queryByRole("status", {name: "交付包同步状态"})).not.toBeInTheDocument();
  expect(select).not.toHaveBeenCalled(); expect(generate).not.toHaveBeenCalled();
});

test("pending delivery does not expose a stale ZIP or trigger an automatic retry", async () => {
  const data = fixture(); data.selected_candidate_id = "candidate-1"; data.delivery = {status: "pending", retryable: false};
  vi.mocked(posterApi.overview).mockResolvedValue(data);
  const rebuild = vi.spyOn(posterApi, "rebuildDelivery"); renderStudio();
  expect(await screen.findByRole("button", {name: "交付包"})).toBeDisabled();
  expect(screen.queryByRole("button", {name: "重新生成交付包"})).not.toBeInTheDocument();
  expect(rebuild).not.toHaveBeenCalled();
});

test("model diagnostics show the last actual upstream event, independent of a newer process heartbeat", async () => {
  const data = fixture(); data.tasks = [{...task("plan"), completed_model_calls: 1,
    last_model_event: {event: "heartbeat"}, last_model_event_at: "2026-10-16T09:10:00Z",
    last_upstream_event: {event: "upstream.event", upstream_event: "codex.sse_event", event_kind: "response.in_progress", source_timestamp: "2026-10-16T09:08:00Z"},
  }];
  vi.mocked(posterApi.overview).mockResolvedValue(data); const user = userEvent.setup(); renderStudio();
  await user.click(await screen.findByText("调用进度详情"));
  const status = screen.getByRole("status", {name: "封面任务状态"});
  expect(within(status).getByText("response.in_progress")).toBeVisible();
  expect(status.querySelector("time")).toHaveAttribute("datetime", "2026-10-16T09:08:00Z");
  expect(within(status).getByText("1 次")).toBeVisible();
  expect(within(status).getByText(/不代表持续生成或已经完成/)).toBeVisible();
});
test("a process heartbeat without an upstream timestamp is not presented as recent model work", async () => {
  const data = fixture(); data.tasks = [{...task("plan"), last_model_event: {event: "heartbeat"}}];
  vi.mocked(posterApi.overview).mockResolvedValue(data); const user = userEvent.setup(); renderStudio();
  await user.click(await screen.findByText("调用进度详情"));
  const status = screen.getByRole("status", {name: "封面任务状态"});
  expect(within(status).getByText("未记录")).toBeVisible();
  expect(within(status).getByText(/不代表模型正在思考/)).toBeVisible();
  expect(status.querySelector("time")).toBeNull();
});
