import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import KnowledgeMarkdownPreview from "./KnowledgeMarkdownPreview";
import { splitKnowledgeMarkdown } from "../lib/knowledgeMarkdown";
import { knowledgeExcerpt } from "../lib/knowledgeExcerpt";

const header = "---\nschema: novel-knowledge.v2\nid: tag.dark_romance\nkind: tag\nname: 黑暗浪漫\n---\n";
const body = "**【系统定位】**\n作者的说明。\n\n## 自由标题\n\n| 人物 | 选择 |\n|---|---|\n| 她 | 离开 |\n\n> 一条建议。";

test("a pasted metadata envelope is collapsed, never rendered as a story heading", () => {
  render(<KnowledgeMarkdownPreview markdown={header + body} />);
  expect(screen.getByText("文件信息").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByRole("heading", { name: /schema:/ })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "自由标题" })).toBeInTheDocument();
  expect(screen.getByText("【系统定位】")).toHaveClass("knowledge-prose-label");
  expect(screen.getByRole("cell", { name: "离开" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "资料表格" })).toHaveAttribute("tabindex", "0");
  expect(screen.getByText("一条建议。")).toBeInTheDocument();
  expect(splitKnowledgeMarkdown(header + body).body).toBe(body);
  expect(knowledgeExcerpt(header + body)).not.toContain("schema:");
  expect(knowledgeExcerpt(header + body)).toContain("作者的说明");
});

test("ordinary separators, unknown front matter and fenced metadata remain visible prose", () => {
  for (const text of ["---\n序言\n---\n正文", "---\ntitle: 自己的故事\n---\n正文", "```yaml\n" + header + "```", body]) {
    expect(splitKnowledgeMarkdown(text)).toEqual({ metadata: [], body: text });
  }
  expect(splitKnowledgeMarkdown(header + header + body)).toEqual({ metadata: [header.slice(4, -5), header.slice(4, -5)], body });
});
