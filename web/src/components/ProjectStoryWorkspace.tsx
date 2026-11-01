import { useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChapterSummary } from "../api/client";
import { chapterHasProse } from "../lib/chapterProse";
import { useChapterReviews } from "../hooks/useChapterReviews";
import {
  projectStoryApi,
  projectStorySourceUrl,
  type ProjectStory,
  type StoryCharacter,
  type StoryJsonBlock,
  type StorySection,
  type StorySourceRef,
} from "../api/projectStory";

export interface StoryLoadProps {
  story: ProjectStory | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

const CHAPTER_STATUS: Record<string, string> = {
  planned: "已规划",
  drafting: "起草中",
  writing: "起草中",
  pending: "等待处理",
  drafted: "已有初稿",
  draft: "已有初稿",
  editing: "修订中",
  edited: "已修订",
  validated: "已校验",
  revised: "已修订",
  final: "已定稿",
  approved: "已批准",
  complete: "已完成",
  completed: "已完成",
  failed: "需处理",
};

const SOURCE_KIND_LABEL: Record<StorySourceRef["kind"], string> = {
  adopted_skeleton: "采用骨架",
  outline_fallback: "大纲回退",
  state_id_map: "状态映射",
};

export function SourceTag({ source }: { source: StorySourceRef }) {
  const lines = source.line_start != null
    ? source.line_end != null && source.line_end !== source.line_start
      ? ` · 第 ${source.line_start}–${source.line_end} 行`
      : ` · 第 ${source.line_start} 行`
    : "";
  return (
    <span className={`pw-source-tag pw-source-tag--${source.kind}`}>
      {SOURCE_KIND_LABEL[source.kind]}{source.section ? ` · ${source.section}` : ""}{lines}
    </span>
  );
}

function StoryLoadState({ loading, error, onRetry, empty }: Pick<StoryLoadProps, "loading" | "error" | "onRetry"> & { empty?: string }) {
  if (loading) {
    return (
      <div className="pw-inline-state" role="status">
        <span className="pw-spinner" aria-hidden="true" />
        <div><strong>正在读取采用骨架</strong><p>章节和人物来源会在读取完成后显示。</p></div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="pw-inline-state pw-inline-state--error" role="alert">
        <div><strong>故事资料暂时无法读取</strong><p>{error}</p></div>
        <button type="button" className="pw-soft-button" onClick={onRetry}>重新读取</button>
      </div>
    );
  }
  if (empty) return <div className="pw-empty-state">{empty}</div>;
  return null;
}

export function ProjectChaptersTab({
  projectId,
  chapters,
  story,
  loading,
  error,
  onRetry,
  isRunning,
  onPlanChapter,
}: StoryLoadProps & {
  projectId: string;
  chapters: ChapterSummary[];
  isRunning: boolean;
  onPlanChapter: (number: number) => void;
}) {
  const [query, setQuery] = useState("");
  const chapterReviews = useChapterReviews(projectId);
  const [filter, setFilter] = useState<"all" | "drafted" | "attention">("all");
  const summaries = useMemo(
    () => new Map((story?.chapters ?? []).map((chapter) => [chapter.number, chapter])),
    [story],
  );
  const counts = useMemo(() => ({
    drafted: chapters.filter(chapterHasProse).length,
    attention: chapters.filter((chapter) => !chapterHasProse(chapter)).length,
  }), [chapters]);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return [...chapters]
      .sort((a, b) => a.number - b.number)
      .filter((chapter) => {
        const statusMatch = filter === "all"
          || (filter === "drafted" && chapterHasProse(chapter))
          || (filter === "attention" && !chapterHasProse(chapter));
        const source = summaries.get(chapter.number);
        const searchMatch = !needle || [chapter.title, chapter.pov, source?.summary]
          .filter(Boolean).join(" ").toLocaleLowerCase("zh-CN").includes(needle);
        return statusMatch && searchMatch;
      });
  }, [chapters, filter, query, summaries]);

  return (
    <section aria-labelledby="pw-chapters-title">
      <div className="pw-section-heading pw-chapters-heading">
        <div><span className="pw-eyebrow">章节</span><h2 id="pw-chapters-title">章节列表</h2><p>阅读章节正文，对照已采用的骨架简介。</p></div>
      </div>
      <div className="pw-chapter-toolbar">
        <label className="pw-search-field">
          <span aria-hidden="true">⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} type="search" placeholder="搜索标题或骨架简介" />
        </label>
        <div className="pw-segmented" aria-label="筛选章节">
          {([
            ["all", "全部", chapters.length],
            ["drafted", "已有正文", counts.drafted],
            ["attention", "需处理", counts.attention],
          ] as const).map(([value, label, count]) => (
            <button key={value} type="button" className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)} aria-pressed={filter === value}>
              {label} <b>{count}</b>
            </button>
          ))}
        </div>
      </div>

      <StoryLoadState loading={loading} error={error} onRetry={onRetry} />
      {story && !story.source.available && (
        <div className="pw-inline-note" role="status">未找到可读取的采用骨架；章节仍可阅读和编辑，简介会明确标记为缺失。</div>
      )}
      {chapters.length === 0 ? (
        <div className="pw-empty-state">尚无章节。先规划全书大纲，再逐章创作。</div>
      ) : visible.length === 0 ? (
        <div className="pw-empty-state">没有符合当前搜索或筛选条件的章节。</div>
      ) : (
        <div className="pw-chapter-table-wrap">
          <table className="pw-chapter-table">
            <thead><tr><th>章节</th><th>阶段</th><th>字数</th><th>采用骨架简介</th><th><span className="pw-sr-only">操作</span></th></tr></thead>
            <tbody>
              {visible.map((chapter) => {
                const source = summaries.get(chapter.number);
                const hasProse = chapterHasProse(chapter);
                const canPlan = ["planned", "failed"].includes(chapter.status);
                return (
                  <tr key={chapter.number}>
                    <th scope="row">
                      <div className="pw-chapter-title"><span>{String(chapter.number).padStart(2, "0")}</span><div><small>第 {chapter.number} 章</small><strong>{chapter.title || "未命名章节"}</strong>{chapter.pov && <em>视角：{chapter.pov}</em>}</div></div>
                    </th>
                    <td><span className={`pw-status-pill pw-status-pill--${chapter.status === "failed" ? "danger" : hasProse ? "ready" : "neutral"}`}>{CHAPTER_STATUS[chapter.status] ?? chapter.status}</span></td>
                    <td><div className="pw-word-count"><strong>{chapter.word_count > 0 ? chapter.word_count.toLocaleString("zh-CN") : "—"}</strong><small>{chapter.word_count > 0 ? "字" : "尚无正文"}</small></div></td>
                    <td>
                      <div className="pw-chapter-summary">
                        <p>{source?.summary?.trim() || (loading ? "正在读取骨架简介…" : "采用骨架未提供本章简介。")}</p>
                        {source?.summary?.trim() && (
                          <details><summary>展开完整介绍</summary><p>{source.summary}</p><SourceTag source={source.source} /></details>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="pw-row-actions">
                        {hasProse && <Link className="pw-text-button" to={`/projects/${encodeURIComponent(projectId)}/read?chapter=${chapter.number}`}>阅读正文</Link>}
                        {chapterReviews[chapter.number] && <Link className="pw-text-button" title="查看已保存正文版本的阅读评议" to={`/projects/${encodeURIComponent(projectId)}/reader-reviews/${chapterReviews[chapter.number]}`}>阅读评议 ↗</Link>}
                        <Link className="pw-mini-button" to={`/projects/${encodeURIComponent(projectId)}/chapters/${chapter.number}`}>编辑章节</Link>
                        {!hasProse && canPlan && <button type="button" className="pw-mini-button pw-mini-button--accent" disabled={isRunning} onClick={() => onPlanChapter(chapter.number)}>规划此章</button>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const FIELD_LABELS: Record<string, string> = {
  title: "标题", name: "名称", language: "语言", pov: "叙事视角", viewpoint: "叙事视角",
  tense: "时态", chapter_count: "预计章节", estimated_chapters: "预计章节", target_word_count: "目标字数",
  total_chapters: "总章节数", target_words: "目标字数", chapter_target_words: "单章目标字数", volume_count: "卷数",
  mode: "结构形式", format: "作品形式", series: "系列信息", volumes: "分卷规划", audience: "目标读者", target_audience: "目标读者", market: "市场",
  genre: "类型", genres: "类型", subgenre: "子类型", role: "身份", identity: "身份",
  primary_genre_id: "主类型", secondary_genre_ids: "次类型", story_type_ids: "故事类型", tone_ids: "基调", setting_ids: "场景类型",
  reader_contract: "读者承诺", premise_engine: "故事动力", conflict_ladder: "冲突升级", free_trial_arc: "开篇价值窗口", quality_budgets: "质量边界",
  audience_profile: "读者画像", setting_policy: "背景策略", workshop_trace: "选定设计记录", ending_contract: "结局合同",
  motivation: "动机", goal: "目标", conflict: "矛盾", dilemma: "困境", arc: "成长方向",
  growth: "成长方向", relationship: "关系", source: "关系起点", target: "关系对象",
  expected_change: "预期变化", change: "变化", secret: "秘密", content: "内容", knows: "知情范围",
  planted: "埋设", setup: "埋设", reveal: "揭示", payoff: "回收", resolution: "归宿",
  unresolved: "待收束事项", objective: "章节目标", events: "关键事件", emotional_shift: "情绪变化",
  ending_hook: "结尾钩子", hook: "结尾钩子", characters: "人物", core_conflict: "核心冲突",
  setting: "场景", visual_constraints: "视觉限制", constraints: "限制", description: "说明", notes: "备注",
};
const TECHNICAL_FIELDS = new Set(["id", "schema_version", "protocol_version", "sha256", "hash", "created_at", "updated_at"]);
const JSON_KIND_LABELS: Record<string, string> = {
  BOOK_SPEC: "创作规格", CREATIVE_SPEC: "创作规格", STORY_SPEC: "创作规格",
  NARRATIVE_FORMAT: "创作规格", NOVEL_CLASSIFICATION: "分类与受众",
  AUDIENCE: "分类与受众", MARKET_POSITIONING: "分类与受众",
  COMMERCIAL_STORY: "故事定位", APPROVED_STORY_CONTRACT: "故事合同", STORY_FOUNDATION: "故事基础",
  CHARACTERS: "人物设计", CHARACTER_PROFILES: "人物设计",
  RELATIONSHIPS: "人物关系", RELATIONSHIP_MAP: "人物关系",
  CHAPTERS: "分章规划", CHAPTER_OUTLINE: "分章规划", CHAPTER_PLAN: "分章规划",
  SECRETS: "秘密与伏笔", FORESHADOWING: "秘密与伏笔",
  ENDING: "结局要求", ENDING_REQUIREMENTS: "结局要求",
  COVER_HANDOFF: "封面交接", COVER_BRIEF: "封面交接",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function fieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key.replaceAll("_", " ");
}

function ScalarValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <>未注明</>;
  if (typeof value === "boolean") return <>{value ? "是" : "否"}</>;
  return <>{String(value)}</>;
}

function ObjectDetails({ value }: { value: Record<string, unknown> }) {
  return (
    <dl className="pw-json-details">
      {Object.entries(value).map(([key, item]) => (
        <div key={key}><dt>{fieldLabel(key)}</dt><dd><FriendlyValue value={item} /></dd></div>
      ))}
    </dl>
  );
}

function FriendlyValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <>未注明</>;
    if (value.every((item) => !isRecord(item) && !Array.isArray(item))) {
      return <span className="pw-json-tags">{value.map((item, index) => <span key={index}><ScalarValue value={item} /></span>)}</span>;
    }
    return <div className="pw-json-stack">{value.map((item, index) => <div key={index}>{isRecord(item) ? <ObjectDetails value={item} /> : <FriendlyValue value={item} />}</div>)}</div>;
  }
  if (isRecord(value)) return <ObjectDetails value={value} />;
  return <ScalarValue value={value} />;
}

function JsonCollection({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row).filter((key) => {
    const value = row[key];
    return !TECHNICAL_FIELDS.has(key) && !isRecord(value) && (!Array.isArray(value) || value.every((item) => typeof item !== "object"));
  })))].slice(0, 4);
  if (columns.length === 0) return <div className="pw-json-card-grid">{rows.map((row, index) => <article key={index}><ObjectDetails value={row} /></article>)}</div>;
  return (
    <div className="pw-json-table-wrap">
      <table><thead><tr>{columns.map((column) => <th key={column}>{fieldLabel(column)}</th>)}<th>详情</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}><FriendlyValue value={row[column]} /></td>)}<td><details><summary>查看全部字段</summary><ObjectDetails value={row} /></details></td></tr>)}</tbody>
      </table>
    </div>
  );
}

export function StoryJsonBlockView({ block }: { block: StoryJsonBlock }) {
  const normalizedKind = block.kind.toUpperCase();
  const label = JSON_KIND_LABELS[normalizedKind]
    ?? JSON_KIND_LABELS[normalizedKind.replace(/_JSON$/, "")]
    ?? (block.kind.replaceAll("_", " ") || "结构化内容");
  const data = block.data;
  const mainEntries = isRecord(data) ? Object.entries(data).filter(([key]) => key in FIELD_LABELS && !TECHNICAL_FIELDS.has(key)) : [];
  const otherEntries = isRecord(data) ? Object.entries(data).filter(([key]) => !(key in FIELD_LABELS) && !TECHNICAL_FIELDS.has(key)) : [];
  const technicalEntries = isRecord(data) ? Object.entries(data).filter(([key]) => TECHNICAL_FIELDS.has(key)) : [];
  return (
    <section className="pw-json-block" aria-label={label}>
      <div className="pw-json-block__heading"><div><span className="pw-eyebrow">结构化资料</span><h4>{label}</h4></div><SourceTag source={block.source} /></div>
      {!block.valid || data == null ? (
        <div className="pw-json-warning" role="status">{block.warning || "这段结构化资料无法解析，原始内容仍完整保留。"}</div>
      ) : Array.isArray(data) && data.every(isRecord) ? (
        <JsonCollection rows={data} />
      ) : isRecord(data) ? (
        <>
          <div className="pw-json-card-grid">
            {mainEntries.map(([key, value]) => <article key={key}><span>{fieldLabel(key)}</span><div><FriendlyValue value={value} /></div></article>)}
          </div>
          {otherEntries.length > 0 && <details className="pw-json-other"><summary>其他信息</summary><ObjectDetails value={Object.fromEntries(otherEntries)} /></details>}
          {technicalEntries.length > 0 && <details className="pw-json-other"><summary>技术信息</summary><ObjectDetails value={Object.fromEntries(technicalEntries)} /></details>}
        </>
      ) : <div className="pw-json-card-grid"><article><FriendlyValue value={data} /></article></div>}
      <details className="pw-json-raw"><summary>查看原始数据</summary><pre>{block.raw}</pre></details>
    </section>
  );
}

function removeJsonTransport(markdown: string, block: StoryJsonBlock) {
  const index = markdown.indexOf(block.raw);
  if (index < 0) return markdown;
  const before = markdown.slice(0, index).replace(
    /(^|\r?\n)[ \t]*(?:\[[A-Za-z][A-Za-z0-9_.-]*JSON[A-Za-z0-9_.-]*\]|[A-Za-z][A-Za-z0-9_]*_BEGIN|```(?:json|application\/json))[ \t]*\r?\n$/i,
    "$1",
  );
  const after = markdown.slice(index + block.raw.length).replace(
    /^[ \t]*(?:\[\/[A-Za-z][A-Za-z0-9_.-]*JSON[A-Za-z0-9_.-]*\]|[A-Za-z][A-Za-z0-9_]*_END|```)[ \t]*(?:\r?\n|$)/i,
    "",
  );
  return before + after;
}

function markdownWithoutJson(section: StorySection) {
  return section.json_blocks.reduce(removeJsonTransport, section.markdown).trim();
}

function MarkdownBody({ children }: { children: string }) {
  return (
    <div className="pw-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        table: ({ children: tableChildren }) => <div className="pw-markdown-table"><table>{tableChildren}</table></div>,
      }}>{children}</ReactMarkdown>
    </div>
  );
}

export function ProjectStoryTab({ projectId, story, loading, error, onRetry }: StoryLoadProps & { projectId: string }) {
  const [selectionState, setSelectionState] = useState({ projectId, value: "overview" });
  const [rawState, setRawState] = useState<{ projectId: string; loading: boolean; text: string | null; error: string | null }>({ projectId: "", loading: false, text: null, error: null });
  const rawRequest = useRef(0);
  const selection = selectionState.projectId === projectId ? selectionState.value : "overview";
  const raw = rawState.projectId === projectId ? rawState.text : null;
  const rawLoading = rawState.projectId === projectId && rawState.loading;
  const rawError = rawState.projectId === projectId ? rawState.error : null;
  const selected = story?.sections.find((section) => section.id === selection) ?? null;

  const setSelection = (value: string) => setSelectionState({ projectId, value });

  async function showRaw() {
    setSelection("raw");
    if (raw != null || rawLoading) return;
    const request = ++rawRequest.current;
    setRawState({ projectId, loading: true, text: null, error: null });
    try {
      const text = await projectStoryApi.source(projectId);
      if (request === rawRequest.current) setRawState({ projectId, loading: false, text, error: null });
    } catch (nextError) {
      if (request === rawRequest.current) setRawState({ projectId, loading: false, text: null, error: nextError instanceof Error ? nextError.message : String(nextError) });
    }
  }

  if (!story) return <StoryLoadState loading={loading} error={error} onRetry={onRetry} empty="故事骨架尚无可展示内容。" />;
  return (
    <section aria-labelledby="pw-story-title">
      <div className="pw-section-heading">
        <div><span className="pw-eyebrow">完整创作设计</span><h2 id="pw-story-title">故事骨架</h2><p>从全书主线到每一章的选择、伏笔和结局。</p></div>
        {story.source.available && <div className="pw-heading-actions">
          <button type="button" className="pw-soft-button" onClick={() => void showRaw()}>查看完整原文</button>
          <a className="pw-soft-button" href={projectStorySourceUrl(projectId)} download>↓ 下载骨架</a>
        </div>}
      </div>
      {story.warnings.length > 0 && <details className="pw-warning-list"><summary>资料读取提示（{story.warnings.length}）</summary><ul>{story.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
      {!story.source.available ? (
        <div className="pw-empty-state">当前作品没有可读取的采用骨架。已有大纲回退资料会标明来源，不会伪装成采用骨架。</div>
      ) : (
        <div className="pw-skeleton-meta"><span><i /> 已采用骨架</span><span>{story.sections.length} 个模块</span><span>{story.source.bytes.toLocaleString("zh-CN")} 字节</span></div>
      )}
      <div className="pw-skeleton-workbench">
        <aside className="pw-skeleton-directory" aria-label="骨架目录">
          <div className="pw-skeleton-directory__head"><strong>内容目录</strong><span>按模块浏览</span></div>
          <nav>
            <button type="button" className={selection === "overview" ? "is-active" : ""} aria-current={selection === "overview" ? "page" : undefined} onClick={() => setSelection("overview")}><span>◎</span>全书总览</button>
            {story.sections.map((section, index) => <button key={section.id} type="button" className={selection === section.id ? "is-active" : ""} aria-current={selection === section.id ? "page" : undefined} onClick={() => setSelection(section.id)}><span>{String(section.number ?? index + 1).padStart(2, "0")}</span>{section.title}</button>)}
            {story.source.available && <button type="button" className={selection === "raw" ? "is-active" : ""} aria-current={selection === "raw" ? "page" : undefined} onClick={() => void showRaw()}><span>↗</span>完整原文</button>}
          </nav>
        </aside>
        <div className="pw-skeleton-reading" tabIndex={0}>
          {selection === "overview" ? (
            <StoryOverview story={story} onSelect={setSelection} />
          ) : selection === "raw" ? (
            rawLoading ? <StoryLoadState loading error={null} onRetry={() => void showRaw()} />
              : rawError ? <StoryLoadState loading={false} error={rawError} onRetry={() => { setRawState({ projectId: "", loading: false, text: null, error: null }); void showRaw(); }} />
                : raw != null ? <><div className="pw-content-heading"><div><span className="pw-eyebrow">完整来源</span><h3>采用骨架原文</h3></div></div><p className="pw-source-hash">SHA-256：{story.source.sha256}</p><MarkdownBody>{raw}</MarkdownBody></>
                  : <div className="pw-empty-state">原文尚未载入。</div>
          ) : selected ? (
            <div>
              <div className="pw-content-heading"><div><span className="pw-eyebrow">骨架模块 {selected.number ?? ""}</span><h3>{selected.title}</h3></div><SourceTag source={selected.source} /></div>
              {markdownWithoutJson(selected) ? <MarkdownBody>{markdownWithoutJson(selected)}</MarkdownBody> : null}
              {selected.json_blocks.map((block, index) => <StoryJsonBlockView key={`${block.kind}-${index}`} block={block} />)}
              {!selected.markdown.trim() && selected.json_blocks.length === 0 && <div className="pw-empty-state">本模块没有可展示内容。</div>}
            </div>
          ) : <div className="pw-empty-state">找不到所选骨架模块。</div>}
        </div>
      </div>
    </section>
  );
}

function StoryOverview({ story, onSelect }: { story: ProjectStory; onSelect: (id: string) => void }) {
  const chapterSection = story.sections.find((section) => /chapter|章节|分章/i.test(`${section.id} ${section.title}`));
  return (
    <div>
      <div className="pw-content-heading"><div><span className="pw-eyebrow">全书总览</span><h3>{story.narrative?.title || "故事设计"}</h3></div>{story.narrative && <SourceTag source={story.narrative.source} />}</div>
      <p className="pw-story-intro">{story.narrative?.text || "采用骨架尚未提供可单独提取的全书梗概，请从左侧目录查看完整模块。"}</p>
      <div className="pw-story-facts"><div><span>骨架模块</span><strong>{story.sections.length || "未注明"}</strong></div><div><span>分章规划</span><strong>{story.chapters.length ? `${story.chapters.length} 章` : "未注明"}</strong></div><div><span>明确人物</span><strong>{story.characters.length || "未注明"}</strong></div><div><span>明确关系</span><strong>{story.relationship_edges.length || "未注明"}</strong></div></div>
      <div className="pw-depth-grid">
        {story.sections.slice(0, 6).map((section, index) => <button type="button" key={section.id} onClick={() => onSelect(section.id)}><strong>{String(section.number ?? index + 1).padStart(2, "0")}</strong><span>{section.title}</span><small>查看完整模块 →</small></button>)}
      </div>
      {chapterSection && <button type="button" className="pw-text-button pw-story-chapter-link" onClick={() => onSelect(chapterSection.id)}>查看完整分章规划 →</button>}
    </div>
  );
}

function CharacterDetail({ character }: { character: StoryCharacter }) {
  return (
    <article className="pw-character-detail" data-index={character.name.slice(0, 1)}>
      <span className="pw-eyebrow">骨架人物</span><h3>{character.name}</h3><span className="pw-character-role">{character.role || "身份未注明"}</span>
      <p>{character.description || "采用骨架没有提供这名人物的独立介绍。"}</p>
      {character.relationships.length > 0 && <div className="pw-character-notes"><strong>骨架中的关系说明</strong>{character.relationships.map((relationship, index) => <div key={index}><p>{relationship.text}</p><SourceTag source={relationship.source} /></div>)}</div>}
      <SourceTag source={character.source} />
    </article>
  );
}

export function ProjectCharactersTab({ story, loading, error, onRetry, focusCharacterId }: StoryLoadProps & { focusCharacterId?: string | null }) {
  const [selection, setSelection] = useState<{ storyId: string; characterId: string } | null>(null);
  if (!story) return <StoryLoadState loading={loading} error={error} onRetry={onRetry} empty="人物资料尚无可展示内容。" />;
  const manual = selection?.storyId === story.project_id && story.characters.some((character) => character.id === selection.characterId) ? selection.characterId : null;
  const focused = focusCharacterId && story.characters.some((character) => character.id === focusCharacterId) ? focusCharacterId : null;
  const selectedId = manual ?? focused ?? story.characters[0]?.id ?? null;
  const selected = story.characters.find((character) => character.id === selectedId) ?? story.characters[0];
  const names = new Map(story.characters.map((character) => [character.id, character.name]));
  function onCharacterKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % story!.characters.length;
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index - 1 + story!.characters.length) % story!.characters.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = story!.characters.length - 1;
    else return;
    event.preventDefault();
    const character = story!.characters[next];
    setSelection({ storyId: story!.project_id, characterId: character.id });
    window.requestAnimationFrame(() => document.getElementById(`pw-character-${next}`)?.focus());
  }
  return (
    <section aria-labelledby="pw-characters-title">
      <div className="pw-section-heading"><div><span className="pw-eyebrow">人物</span><h2 id="pw-characters-title">人物与关系</h2><p>只展示采用骨架或明确回退来源中记载的人物事实与关系。</p></div></div>
      {story.characters.length === 0 ? <div className="pw-empty-state">采用骨架没有可提取的人物资料。页面不会用空头像或推断关系补位。</div> : (
        <div className="pw-character-workbench">
          <div className="pw-character-list" role="listbox" aria-label="人物列表">
            {story.characters.map((character, index) => <button key={character.id} id={`pw-character-${index}`} type="button" role="option" aria-selected={selected?.id === character.id} tabIndex={selected?.id === character.id ? 0 : -1} className={selected?.id === character.id ? "is-active" : ""} onClick={() => setSelection({ storyId: story.project_id, characterId: character.id })} onKeyDown={(event) => onCharacterKeyDown(event, index)}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{character.name}</strong><small>{character.role || "身份未注明"}</small></div></button>)}
          </div>
          {selected && <CharacterDetail character={selected} />}
        </div>
      )}
      {story.unstructured_sections.filter((section) => section.kind === "characters").map((section) => <details className="pw-unstructured" key={`${section.kind}-${section.source.line_start}`}><summary>人物档案原文 · {section.title}</summary><MarkdownBody>{section.text}</MarkdownBody><SourceTag source={section.source} /></details>)}
      <section className="pw-relationship-ledger" aria-labelledby="pw-relations-title">
        <div><span className="pw-eyebrow">关系表</span><h3 id="pw-relations-title">来源明确的关系</h3></div>
        {story.relationship_edges.length > 0 ? (
          <div className="pw-relationship-table-wrap"><table><thead><tr><th>人物</th><th>关系</th><th>人物</th><th>骨架说明</th><th>来源</th></tr></thead><tbody>
            {story.relationship_edges.map((edge) => <tr key={edge.id}><th scope="row">{names.get(edge.source_character_id) || edge.source_character_id}</th><td><span className="pw-relation-chip">{edge.label || "关系未命名"}</span></td><th>{names.get(edge.target_character_id) || edge.target_character_id}</th><td>{edge.description || "未注明"}</td><td><SourceTag source={edge.source} /></td></tr>)}
          </tbody></table></div>
        ) : <div className="pw-empty-state">采用骨架没有明确、可结构化的关系边。人物条目中的原文关系说明仍保留在人物详情中，不会据共同出现推断关系。</div>}
      </section>
      {story.unstructured_sections.filter((section) => section.kind === "relationships").map((section) => <details className="pw-unstructured" key={`${section.kind}-${section.title}`}><summary>{section.title || "未结构化关系原文"}</summary><p>{section.text}</p><SourceTag source={section.source} /></details>)}
    </section>
  );
}

export function WorkspacePanel({ children, id, labelledBy }: { children: ReactNode; id?: string; labelledBy?: string }) {
  return <div className="pw-tab-panel" id={id} role="tabpanel" aria-labelledby={labelledBy}>{children}</div>;
}
