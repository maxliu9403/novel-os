import { splitKnowledgeMarkdown } from "./knowledgeMarkdown";

/** A short navigation label; the editor and model retain the complete Markdown. */
export function knowledgeExcerpt(promise: string) {
  const paragraphs = splitKnowledgeMarkdown(promise).body.split(/\n\s*\n/).filter((p) => p.trim());
  const specific = paragraphs.find(
    (p) => !p.trim().replace(/\*/g, "").startsWith("创作基调与边界"),
  ) ?? paragraphs[0] ?? "";
  const plain = specific.replace(/[*`]/g, "").replace(/^\s*[->]\s*/, "");
  return plain.length > 160 ? `${plain.slice(0, 160)}…` : plain;
}
