import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import BookReader from "../routes/BookReader";
import { readerApi, type ReaderChapter } from "../api/reader";

const chapters = [1, 2, 3].map(number => ({ number, title: `Chapter title ${number}`, status: "draft", available: number !== 3 }));
function open(url = "/projects/book/read") {
  return render(<MemoryRouter initialEntries={[url]}><Routes><Route path="/projects/:id/read" element={<BookReader />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.spyOn(readerApi, "index").mockResolvedValue({ title: "Test novel", author: "An author", chapters });
  vi.spyOn(readerApi, "chapter").mockImplementation(async (_id, number) => ({ number, title: `Chapter title ${number}`, status: "draft", version: number === 3 ? null : number === 1 ? "final" : "draft", markdown: number === 3 ? "" : `Full text for chapter ${number}.\n\nThe complete last paragraph—unchanged.` }));
  HTMLElement.prototype.scrollTo = vi.fn();
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

test("reads full saved prose, labels versions, and keeps editing as an independent link", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  open();
  expect(await screen.findByText("Full text for chapter 1.")).toBeVisible();
  expect(screen.getByText("The complete last paragraph—unchanged.")).toBeVisible();
  expect(screen.getByText("定稿")).toBeVisible();
  expect(screen.getByRole("link", { name: "进入编辑器 ↗" })).toHaveAttribute("href", "/projects/book/chapters/1");
  expect(readerApi.chapter).toHaveBeenCalledTimes(1);
  expect(fetchSpy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "下一章 →" }));
  expect(await screen.findByText("Full text for chapter 2.")).toBeVisible();
  expect(screen.getByText("草稿")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "下一章 →" }));
  expect(await screen.findByText(/本章尚未保存正文/)).toBeVisible();
  expect(screen.queryByText("Full text for chapter 2.")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下一章 →" })).toBeDisabled();
});

test("continuous reading exposes every chapter and directory navigation does not reload the book", async () => {
  open("/projects/book/read?mode=continuous");
  expect(await screen.findByText("Full text for chapter 2.")).toBeVisible();
  expect(await screen.findByText(/已读取 3\/3 章/)).toBeVisible();
  expect(screen.getByText(/本章尚未保存正文/)).toBeVisible();
  expect(readerApi.chapter).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole("button", { name: /第 2 章.*Chapter title 2/ }));
  await waitFor(() => expect(readerApi.chapter).toHaveBeenCalledTimes(3));
});

test("corrupted or unavailable sources show an error instead of a fallback draft", async () => {
  vi.mocked(readerApi.chapter).mockRejectedValueOnce(new Error("正文来源校验失败"));
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("正文来源校验失败");
  expect(screen.queryByText(/Full text/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取正文" }));
  expect(await screen.findByText("Full text for chapter 1.")).toBeVisible();
});

test("changing chapter cancels the old read and ignores its late response", async () => {
  let finish!: (value: ReaderChapter) => void;
  let signal: AbortSignal | undefined;
  vi.mocked(readerApi.chapter).mockImplementationOnce((_id, _number, requestSignal) => {
    signal = requestSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  open();
  await screen.findByRole("heading", { name: "Chapter title 1" });
  fireEvent.click(screen.getByRole("button", { name: "下一章 →" }));
  expect(await screen.findByText("Full text for chapter 2.")).toBeVisible();
  expect(signal?.aborted).toBe(true);
  finish({ number: 1, title: "Old", status: "draft", version: "draft", markdown: "late stale body" });
  await waitFor(() => expect(screen.queryByText("late stale body")).not.toBeInTheDocument());
});
