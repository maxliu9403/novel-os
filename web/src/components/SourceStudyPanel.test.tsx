import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import SourceStudyPanel from "./SourceStudyPanel";
it("shows a long wait without treating heartbeat as model content or inventing completion", () => {
  const old = new Date(Date.now() - 300000).toISOString();
  render(
    <SourceStudyPanel
      id="s"
      onRefresh={vi.fn()}
      study={{
        status: "running",
        total: 1,
        completed: 0,
        current: 1,
        started_at: old,
        checked_at: new Date().toISOString(),
        worker_active: true,
        progress: {
          request_started_at: old,
          received_chars: 0,
          heartbeat_at: new Date().toISOString(),
        },
      }}
    />,
  );
  expect(screen.getByText("正在等待模型返回分析内容。")).toBeVisible();
  expect(screen.getByText(/这不能证明模型持续推理/)).toBeVisible();
  expect(screen.getByText(/关闭窗口后任务继续/)).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0");
  expect(screen.getByText(/本次用时 5 分/)).toBeVisible();
});
it("disables pause while the existing worker drains", () => {
  render(
    <SourceStudyPanel
      id="s"
      onRefresh={vi.fn()}
      study={{
        status: "running",
        total: 1,
        completed: 0,
        worker_active: true,
        cancel_requested: true,
      }}
    />,
  );
  expect(screen.getByRole("button", { name: "暂停处理中…" })).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "继续未完成部分" }),
  ).not.toBeInTheDocument();
});
