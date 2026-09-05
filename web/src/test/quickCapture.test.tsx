import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import QuickCapture from "../components/QuickCapture";
import { ToastProvider } from "../components/Toaster";
import { api } from "../api/client";

function renderCapture(onCaptured = () => {}) {
  return render(
    <ToastProvider>
      <input aria-label="manuscript" />
      <QuickCapture projectId="book" chapterNumber={3} onCaptured={onCaptured} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

test("stays out of the way until asked for", () => {
  renderCapture();
  expect(screen.queryByLabelText("快速记录")).not.toBeInTheDocument();
});

test("cmd+. opens it and focuses the field", async () => {
  renderCapture();
  await userEvent.keyboard("{Meta>}.{/Meta}");

  const field = await screen.findByLabelText("快速记录");
  await waitFor(() => expect(field).toHaveFocus());
});

test("Enter files the note against the current chapter", async () => {
  const add = vi.spyOn(api, "addComment").mockResolvedValue({
    id: "c1", body: "fix her coat", quote: "", resolved: false,
  } as never);
  const onCaptured = vi.fn();
  renderCapture(onCaptured);

  await userEvent.keyboard("{Meta>}.{/Meta}");
  await userEvent.type(await screen.findByLabelText("快速记录"), "fix her coat{Enter}");

  await waitFor(() =>
    expect(add).toHaveBeenCalledWith("book", 3, "fix her coat", "", null, null),
  );
  expect(onCaptured).toHaveBeenCalled();
});

test("filing a note hands focus back to where you were", async () => {
  vi.spyOn(api, "addComment").mockResolvedValue({} as never);
  renderCapture();

  const manuscript = screen.getByLabelText("manuscript");
  manuscript.focus();

  await userEvent.keyboard("{Meta>}.{/Meta}");
  await userEvent.type(await screen.findByLabelText("快速记录"), "later{Enter}");

  await waitFor(() => expect(manuscript).toHaveFocus());
});

test("Escape abandons the note and returns focus", async () => {
  const add = vi.spyOn(api, "addComment");
  renderCapture();

  const manuscript = screen.getByLabelText("manuscript");
  manuscript.focus();

  await userEvent.keyboard("{Meta>}.{/Meta}");
  await userEvent.type(await screen.findByLabelText("快速记录"), "never mind{Escape}");

  await waitFor(() => expect(manuscript).toHaveFocus());
  expect(add).not.toHaveBeenCalled();
});

test("an empty note is not filed", async () => {
  const add = vi.spyOn(api, "addComment");
  renderCapture();

  await userEvent.keyboard("{Meta>}.{/Meta}");
  await userEvent.type(await screen.findByLabelText("快速记录"), "   {Enter}");

  expect(add).not.toHaveBeenCalled();
});

test("a failed save keeps the box open so the words are not lost", async () => {
  vi.spyOn(api, "addComment").mockRejectedValue(new Error("offline"));
  renderCapture();

  await userEvent.keyboard("{Meta>}.{/Meta}");
  const field = await screen.findByLabelText("快速记录");
  await userEvent.type(field, "important thought{Enter}");

  await waitFor(() => expect(field).toHaveValue("important thought"));
});
