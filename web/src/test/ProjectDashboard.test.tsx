import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { vi } from "vitest";
import ProjectDashboard from "../routes/ProjectDashboard";
import * as client from "../api/client";

test("shows project title and chapter cards", async () => {
  vi.spyOn(client.api, "project").mockResolvedValue({
    id: "p", title: "My Novel", genre: "Drama", author: "A",
    chapter_count: 1, status: "in_progress", style: {},
  });
  vi.spyOn(client.api, "chapters").mockResolvedValue([
    { number: 1, title: "Opening", status: "drafted", word_count: 2300, pov: "Lena" },
  ]);
  vi.spyOn(client.api, "binder").mockResolvedValue([
    {
      id: "part-manuscript", type: "part", title: "Manuscript",
      children: [{
        id: "ch-001", type: "chapter", title: "Opening", chapter_number: 1,
        synopsis: "Lena opens the pier.", status: "drafted", pov: "Lena",
        word_count: 2300, parent_id: "part-manuscript", children: [],
      }],
    },
  ]);
  vi.spyOn(client.api, "codex").mockResolvedValue([]);
  vi.spyOn(client.api, "relationships").mockResolvedValue([]);
  vi.spyOn(client.api, "continuity").mockResolvedValue({ findings: [], summary: "" } as never);
  render(
    <MemoryRouter initialEntries={["/projects/p"]}>
      <Routes><Route path="/projects/:id" element={<ProjectDashboard />} /></Routes>
    </MemoryRouter>
  );
  expect(await screen.findByText("My Novel")).toBeInTheDocument();
  expect(screen.getByText("Opening")).toBeInTheDocument();
  expect(screen.getByText("已有初稿")).toBeInTheDocument();
  expect(screen.getByPlaceholderText("添加章节梗概…")).toBeInTheDocument();
});

test("refreshes externally generated chapter progress when the window regains focus", async () => {
  vi.restoreAllMocks();
  vi.spyOn(client.api, "project").mockResolvedValue({
    id: "p", title: "My Novel", genre: "Drama", author: "A",
    chapter_count: 15, status: "in_progress", style: {},
  });
  const firstThree = Array.from({ length: 3 }, (_, index) => ({
    number: index + 1,
    title: `Chapter ${index + 1}`,
    status: "complete",
    word_count: 1000,
    pov: "Lena",
  }));
  const allChapters = Array.from({ length: 15 }, (_, index) => ({
    number: index + 1,
    title: `Chapter ${index + 1}`,
    status: "complete",
    word_count: 1000,
    pov: "Lena",
  }));
  vi.spyOn(client.api, "chapters")
    .mockResolvedValueOnce(firstThree)
    .mockResolvedValue(allChapters);
  vi.spyOn(client.api, "codex").mockResolvedValue([]);
  vi.spyOn(client.api, "relationships").mockResolvedValue([]);
  vi.spyOn(client.api, "continuity").mockResolvedValue({ findings: [], summary: "" } as never);
  vi.spyOn(client.api, "bookShape").mockResolvedValue({ chapters: [], stalls: [] });
  vi.spyOn(client.api, "statistics").mockResolvedValue({
    word_count: 0,
    reading_minutes: 0,
    avg_sentence_length: 0,
    unique_content_words: 0,
    top_words: [],
    echoes: [],
    chapter_count: 15,
    chapters_with_prose: 0,
  });

  render(
    <MemoryRouter initialEntries={["/projects/p"]}>
      <Routes><Route path="/projects/:id" element={<ProjectDashboard />} /></Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText("3/15")).toBeInTheDocument();
  fireEvent.focus(window);

  await waitFor(() => expect(screen.getByText("15/15")).toBeInTheDocument());
  expect(screen.getByText("15,000")).toBeInTheDocument();
});
