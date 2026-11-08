import { sourceBooksApi, type SourceDetail } from "../api/sourceBooks";
import SourceStudyPanel from "./SourceStudyPanel";
import SourcePlan from "./SourcePlan";
export default function SourceBookDetails({
  book,
  onRefresh,
}: {
  book: SourceDetail;
  onRefresh: () => void;
}) {
  return (
    <article className="source-book-summary">
      {book.cover_url && (
        <img src={sourceBooksApi.coverUrl(book)} alt="来源小说封面" />
      )}
      <div>
        <h4>{book.title}</h4>
        <p>
          {book.chapter_count} 篇清单章节
          {book.supplement_count
            ? ` · ${book.supplement_count} 篇附加资料`
            : ""}
        </p>
        {book.warnings.map((w) => (
          <p key={w}>{w}</p>
        ))}
        <details>
          <summary>官方 Synopsis</summary>
          <p className="source-synopsis">{book.synopsis}</p>
        </details>
        <SourceStudyPanel
          key={book.id}
          id={book.id}
          study={book.study}
          hasPlan={
            book.versions?.[0]?.has_plan ?? !!book.result?.continuation_plan
          }
          onRefresh={onRefresh}
        />
        {book.result &&
          book.versions?.[0] &&
          book.result.analysis_sha256 !== book.versions[0].analysis_sha256 && (
            <p className="knowledge-muted">
              当前展示历史分析；带入头脑风暴时会固定使用此版本。
            </p>
          )}
        {book.result?.continuation_plan && (
          <SourcePlan plan={book.result.continuation_plan} />
        )}
        {book.result && (
          <details>
            <summary>查看阅读摘要</summary>
            <p>{book.result.analysis.story_summary}</p>
            {book.result.analysis.uncertainties.map((u, i) => (
              <p key={i}>{u}</p>
            ))}
            <p className="knowledge-muted">
              已读事实与新增续写提案分别保存。未提供的原作后续仍未知。
            </p>
          </details>
        )}
      </div>
    </article>
  );
}
