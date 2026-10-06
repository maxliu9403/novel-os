import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, vi } from "vitest";
import CoverStudio from "../routes/CoverStudio";
import { api, type CoverDirection, type CoverStoryFacts } from "../api/client";

afterEach(() => vi.restoreAllMocks());

const facts: CoverStoryFacts = {
  brief: {
    title: "After 24 Saturdays",
    principal_characters: [{
      character_id: "char_audrey", name: "Audrey Blake",
      occupation_and_status: "Returning designer",
    }],
  },
  pending_fields: [{
    field: "principal_characters[0].age", label: "Audrey Blake age range",
    character_id: "char_audrey", character_name: "Audrey Blake",
    proposed_value: "derive from approved story facts",
  }],
  revision_sha256: "before",
};
const direction: CoverDirection = {
  direction_id: "direction-1", schema_version: 2, director_model: "fixture",
  profile_version: "fixture", brief_sha256: "after", direction_sha256: "direction-hash",
  status: "awaiting_approval", plans: [], visual_assumptions: [], brief: facts.brief,
};

function setup(storyFacts = facts) {
  vi.spyOn(api, "project").mockResolvedValue({
    id: "book", title: "After 24 Saturdays", genre: "Women's Fiction", author: "Eliza",
    chapter_count: 40, status: "completed", style: {},
  });
  vi.spyOn(api, "studioCover").mockResolvedValue({
    configured: true, has_api_key: false, base_url: "", model: "gpt-image-2",
    size: "1024x1536", quality: "high", output_format: "jpeg", count: 4,
    timeout_seconds: 300, inherits_base_url: false, inherits_api_key: false,
  });
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  const read = vi.spyOn(api, "coverStoryFacts").mockResolvedValue(storyFacts);
  const save = vi.spyOn(api, "confirmCoverStoryFacts").mockResolvedValue({
    ...facts, pending_fields: [], revision_sha256: "after",
  });
  const create = vi.spyOn(api, "createCoverDirection").mockResolvedValue(direction);
  render(<MemoryRouter initialEntries={["/projects/book/covers"]}>
    <Routes><Route path="/projects/:id/covers" element={<CoverStudio />} /></Routes>
  </MemoryRouter>);
  return { read, save, create, user: userEvent.setup() };
}

test("collects only missing cover facts before creating a direction", async () => {
  const { user, save, create } = setup();
  await user.click(await screen.findByRole("button", { name: "创建美术方向" }));
  const input = await screen.findByLabelText("Audrey Blake · 封面年龄段");
  expect(input).toHaveValue("");
  expect(screen.getByText("Returning designer")).toBeInTheDocument();
  expect(create).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "保存并创建方向" })).toBeDisabled();
  await user.type(input, "30–35 years old");
  await user.click(screen.getByRole("button", { name: "保存并创建方向" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("book", {
    expected_revision_sha256: "before",
    characters: [{ character_id: "char_audrey", age_band: "30–35 years old" }],
  }));
  expect(create).toHaveBeenCalledWith("book", 4);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("does not create a direction if saving cover facts fails", async () => {
  const { user, save, create } = setup();
  save.mockRejectedValue(new Error("资料已更新，请重新读取"));
  await user.click(await screen.findByRole("button", { name: "创建美术方向" }));
  await user.type(await screen.findByLabelText("Audrey Blake · 封面年龄段"), "30–35 years old");
  await user.click(screen.getByRole("button", { name: "保存并创建方向" }));
  expect(await screen.findByText("资料已更新，请重新读取")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(create).not.toHaveBeenCalled();
});

test("creates directly when all cover facts are already present", async () => {
  const { user, create, save, read } = setup({ ...facts, pending_fields: [] });
  await user.click(await screen.findByRole("button", { name: "创建美术方向" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith("book", 4));
  expect(read).toHaveBeenCalledWith("book");
  expect(save).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
