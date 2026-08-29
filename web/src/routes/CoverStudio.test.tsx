import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../components/Toaster";
import { ConfirmProvider } from "../components/Confirm";
import CoverStudio from "./CoverStudio";
import { api, type CoverSet } from "../api/client";


const project = {
  id: "cover-book", title: "The Door Is Mine", genre: "Domestic revenge",
  author: "A. Writer", chapter_count: 12, status: "in_progress", style: {},
};

const coverStatus = {
  configured: true, has_api_key: true, base_url: "https://sub2api.example/v1",
  model: "gpt-image-2", size: "2048x3072", quality: "high" as const,
  output_format: "webp" as const, count: 4, timeout_seconds: 180,
  inherits_base_url: false, inherits_api_key: false, error: null,
};

function coverSet(status: CoverSet["status"] = "partial"): CoverSet {
  const states = status === "selected"
    ? ["selected", "ready", "ready", "rejected"] as const
    : ["ready", "failed", "ready", "pending"] as const;
  return {
    cover_set_id: "cover-abc", project_id: project.id, status,
    requested_count: 4, selected_candidate_id: status === "selected" ? "candidate-1" : "",
    revision: 5, active_revision: status === "selected" ? 1 : 0,
    source_prompt_sha256: "a".repeat(64), foundation_sha256: "",
    created_at: "2026-08-29T10:00:00Z", updated_at: "2026-08-29T10:05:00Z",
    brief: {
      title: project.title, target_audience: "women 30-50",
      core_conflict: "Two households demand her labor.",
      decisive_story_node: "Her name appears alone on the deed.",
    },
    concepts: states.map((_, index) => ({
      concept_id: `concept-${index + 1}`,
      visual_strategy: ["Confrontation", "Decisive node", "Evidence", "Pressure"][index],
      focal_scene: `Scene ${index + 1}`, composition: "Portrait", palette: "Crimson",
      secondary_signal: "House key", title_treatment: "Title at top",
      generation_prompt: "redacted in UI fixture",
    })),
    candidates: states.map((candidateStatus, index) => ({
      candidate_id: `candidate-${index + 1}`, concept_id: `concept-${index + 1}`,
      status: candidateStatus, url: candidateStatus === "pending" || candidateStatus === "failed"
        ? null : `/api/projects/${project.id}/media/media-${index + 1}/raw`,
      relative_path: candidateStatus === "pending" || candidateStatus === "failed"
        ? "" : `outputs/deliverables/covers/pending/cover-0${index + 1}.webp`,
      media_id: candidateStatus === "pending" || candidateStatus === "failed" ? "" : `media-${index + 1}`,
      sha256: candidateStatus === "pending" || candidateStatus === "failed" ? "" : "b".repeat(64),
      width: candidateStatus === "pending" || candidateStatus === "failed" ? 0 : 2048,
      height: candidateStatus === "pending" || candidateStatus === "failed" ? 0 : 3072,
      content_type: candidateStatus === "pending" || candidateStatus === "failed" ? "" : "image/webp",
      error: candidateStatus === "failed" ? "Provider timeout" : "",
    })),
  };
}

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={[`/projects/${project.id}/covers`]}>
      <ToastProvider><ConfirmProvider>
        <Routes><Route path="/projects/:id/covers" element={<CoverStudio />} /></Routes>
      </ConfirmProvider></ToastProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("keeps four 2:3 slots stable while the workspace loads", () => {
  vi.spyOn(api, "project").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "studioCover").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "covers").mockReturnValue(new Promise(() => {}));

  renderStudio();

  expect(screen.getAllByTestId("cover-slot")).toHaveLength(4);
  expect(screen.getByText("Loading cover workspace")).toBeInTheDocument();
});

test("routes an unconfigured workspace to independent cover settings", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue({
    ...coverStatus, configured: false, has_api_key: false, error: "Add a cover API key.",
  });
  vi.spyOn(api, "covers").mockResolvedValue([]);

  renderStudio();

  expect(await screen.findByRole("heading", { name: "Cover Studio" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Configure cover model" })).toHaveAttribute("href", "/settings");
  expect(screen.getByRole("button", { name: "Generate 4 covers" })).toBeDisabled();
});

test("shows partial results and retries only the failed candidate", async () => {
  const partial = coverSet();
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([partial]);
  const retry = vi.spyOn(api, "retryCover").mockResolvedValue({
    job_id: "job-retry", kind: "cover.retry", status: "done", error: null, meta: {},
  });

  renderStudio();
  const user = userEvent.setup();

  expect(await screen.findByText("Provider timeout")).toBeInTheDocument();
  expect(screen.getAllByTestId("cover-slot")).toHaveLength(4);
  expect(screen.getByRole("link", { name: "View full resolution candidate 1" })).toHaveAttribute(
    "href", expect.stringContaining("/media/media-1/raw"),
  );
  await user.click(screen.getByRole("button", { name: "Retry candidate 2" }));
  await waitFor(() => expect(retry).toHaveBeenCalledWith(
    project.id, partial.cover_set_id, "candidate-2", partial.revision,
  ));
});

test("requires confirmation before selection and exposes the delivery package", async () => {
  const ready = coverSet("ready");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([ready]);
  const select = vi.spyOn(api, "selectCover").mockResolvedValue(coverSet("selected"));
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "Cover Studio" });
  await user.click(screen.getByRole("button", { name: "Select candidate 1" }));

  expect(select).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "Use this cover?" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Use this cover" }));
  await waitFor(() => expect(select).toHaveBeenCalledWith(
    project.id, ready.cover_set_id, "candidate-1", ready.revision, ready.active_revision, false,
  ));
  expect(screen.getByRole("link", { name: "Download delivery package" })).toHaveAttribute(
    "href", expect.stringContaining("/deliverables/package"),
  );
});

test("generates the configured candidate count from the persisted story handoff", async () => {
  const ready = coverSet("ready");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValueOnce([]).mockResolvedValue([ready]);
  const generate = vi.spyOn(api, "generateCovers").mockResolvedValue({
    job_id: "job-generate", kind: "cover.generate", status: "done", error: null, meta: {},
  });
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "Cover Studio" });
  await user.click(screen.getByRole("button", { name: "Generate 4 covers" }));

  await waitFor(() => expect(generate).toHaveBeenCalledWith(project.id, 4));
  expect(await screen.findByText("2 of 4 rendered")).toBeInTheDocument();
});

test("requires confirmation before rejecting a ready candidate", async () => {
  const ready = coverSet("ready");
  const rejected = {
    ...ready,
    revision: ready.revision + 1,
    candidates: ready.candidates.map((candidate, index) => (
      index === 0 ? { ...candidate, status: "rejected" as const } : candidate
    )),
  };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([ready]);
  const reject = vi.spyOn(api, "rejectCover").mockResolvedValue(rejected);
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "Cover Studio" });
  await user.click(screen.getByRole("button", { name: "Reject candidate 1" }));

  expect(reject).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Reject candidate" }));
  await waitFor(() => expect(reject).toHaveBeenCalledWith(
    project.id, ready.cover_set_id, "candidate-1", ready.revision,
  ));
  expect(screen.getByText("rejected")).toBeInTheDocument();
});

test("reloads cover revisions after a selection conflict", async () => {
  const ready = coverSet("ready");
  const refreshed = { ...ready, revision: ready.revision + 2, active_revision: 3 };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  const covers = vi.spyOn(api, "covers")
    .mockResolvedValueOnce([ready])
    .mockResolvedValue([refreshed]);
  vi.spyOn(api, "selectCover").mockRejectedValue(new Error("Cover set revision changed"));
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "Cover Studio" });
  await user.click(screen.getByRole("button", { name: "Select candidate 1" }));
  await user.click(screen.getByRole("button", { name: "Use this cover" }));

  await waitFor(() => expect(covers).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("Cover set revision changed")).toBeInTheDocument();
});
