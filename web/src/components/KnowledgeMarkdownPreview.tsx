import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { splitKnowledgeMarkdown } from "../lib/knowledgeMarkdown";

export default function KnowledgeMarkdownPreview({ markdown }: { markdown: string }) {
  const { metadata, body } = splitKnowledgeMarkdown(markdown);
  return (
    <div className="knowledge-reader">
      {metadata.length > 0 && (
        <details className="knowledge-file-info">
          <summary>文件信息</summary>
          {metadata.map((text, index) => <pre key={index}>{text}</pre>)}
        </details>
      )}
      <article className="knowledge-prose">
        {body.trim() ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            strong: ({ children }) => (
              <strong className={typeof children === "string" && /^【[^】\n]{1,40}】$/.test(children) ? "knowledge-prose-label" : undefined}>
                {children}
              </strong>
            ),
            table: ({ children }) => (
              <div className="knowledge-table-scroll" role="region" aria-label="资料表格" tabIndex={0}>
                <table>{children}</table>
              </div>
            ),
          }}>{body}</ReactMarkdown>
        ) : <p className="knowledge-muted">还没有创作说明，切换到「编写 Markdown」开始填写。</p>}
      </article>
    </div>
  );
}
