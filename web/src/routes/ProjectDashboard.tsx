import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { api, type ChapterSummary, type ProjectDeliverySummary, type ProjectDetail } from "../api/client";
import { projectStoryApi, type ProjectStory } from "../api/projectStory";
import ChoiceGroup from "../components/ChoiceGroup";
import CompilePanel from "../components/CompilePanel";
import DeliveryPackageButton from "../components/DeliveryPackageButton";
import GenerationCostPanel from "../components/GenerationCostPanel";
import {
  ProjectChaptersTab,
  ProjectCharactersTab,
  ProjectStoryTab,
  WorkspacePanel,
} from "../components/ProjectStoryWorkspace";
import Scene from "../components/Scene";
import TaskStatusPanel from "../components/TaskStatusPanel";
import WritingTargets from "../components/WritingTargets";
import { useRunPhase } from "../hooks/useRunPhase";
import { chapterHasProse } from "../lib/chapterProse";
import { useToast } from "../components/toastContext";
import "../styles/project-workspace.css";

type WorkspaceTab = "overview" | "chapters" | "story" | "characters" | "delivery";
const TABS: Array<{ id: WorkspaceTab; label: string }> = [
  { id: "overview", label: "作品概览" },
  { id: "chapters", label: "章节" },
  { id: "story", label: "故事骨架" },
  { id: "characters", label: "人物关系" },
  { id: "delivery", label: "费用与交付" },
];

const PROJECT_STATUS: Record<string, string> = {
  completed: "已完成", complete: "已完成", running: "创作中", in_progress: "创作中",
  pending: "等待开始", queued: "等待开始", paused: "已暂停", failed: "需处理",
  interrupted: "已中断", draft: "草稿",
};

const STORY_TABS = new Set<WorkspaceTab>(["chapters", "story", "characters"]);

function tabFromSearch(searchParams: URLSearchParams): WorkspaceTab {
  if (searchParams.has("codex")) return "characters";
  const requested = searchParams.get("tab") as WorkspaceTab | null;
  if (requested && TABS.some((tab) => tab.id === requested)) return requested;
  return "chapters";
}

export default function ProjectDashboard() {
  const { id = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = tabFromSearch(searchParams);
  const focusCharacterId = searchParams.get("codex");
  const [coreResult, setCoreResult] = useState<{ projectId: string; project: ProjectDetail; chapters: ChapterSummary[] } | null>(null);
  const [coreFailure, setCoreFailure] = useState<{ projectId: string; message: string } | null>(null);
  const [externalRefreshKey, setExternalRefreshKey] = useState(0);
  const coreRequest = useRef(0);
  const [storyLoad, setStoryLoad] = useState<{
    projectId: string;
    status: "loading" | "ready" | "error";
    data: ProjectStory | null;
    error: string | null;
  } | null>(null);
  const storyRequest = useRef(0);
  const storyAbort = useRef<AbortController | null>(null);
  const project = coreResult?.projectId === id ? coreResult.project : null;
  const chapters = coreResult?.projectId === id ? coreResult.chapters : [];
  const coreError = coreFailure?.projectId === id ? coreFailure.message : null;
  const story = storyLoad?.projectId === id ? storyLoad.data : null;
  const storyStatus = storyLoad?.projectId === id ? storyLoad.status : "idle";
  const storyError = storyLoad?.projectId === id ? storyLoad.error : null;

  const loadCore = useCallback(async () => {
    const request = ++coreRequest.current;
    try {
      const [nextProject, nextChapters] = await Promise.all([api.project(id), api.chapters(id)]);
      if (request !== coreRequest.current) return;
      setCoreResult({ projectId: id, project: nextProject, chapters: nextChapters });
      setCoreFailure(null);
    } catch (error) {
      if (request !== coreRequest.current) return;
      setCoreFailure({ projectId: id, message: error instanceof Error ? error.message : String(error) });
    }
  }, [id]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadCore(), 0);
    return () => {
      window.clearTimeout(timer);
      coreRequest.current += 1;
    };
  }, [loadCore]);

  const loadStory = useCallback(async () => {
    const request = ++storyRequest.current;
    storyAbort.current?.abort();
    const controller = new AbortController();
    storyAbort.current = controller;
    setStoryLoad({ projectId: id, status: "loading", data: null, error: null });
    try {
      const data = await projectStoryApi.get(id, controller.signal);
      if (request !== storyRequest.current || controller.signal.aborted) return;
      setStoryLoad({ projectId: id, status: "ready", data, error: null });
    } catch (error) {
      if (request !== storyRequest.current || controller.signal.aborted) return;
      setStoryLoad({ projectId: id, status: "error", data: null, error: error instanceof Error ? error.message : String(error) });
    }
  }, [id]);

  useEffect(() => {
    return () => storyAbort.current?.abort();
  }, [id]);

  useEffect(() => {
    if (!STORY_TABS.has(activeTab) || storyStatus !== "idle") return;
    const timer = window.setTimeout(() => void loadStory(), 0);
    return () => window.clearTimeout(timer);
  }, [activeTab, loadStory, storyStatus]);

  const refreshExternalProgress = useCallback(() => {
    void loadCore();
    setExternalRefreshKey((value) => value + 1);
  }, [loadCore]);

  useEffect(() => {
    let lastRefresh = Number.NEGATIVE_INFINITY;
    const refresh = () => {
      const now = performance.now();
      if (now - lastRefresh < 250) return;
      lastRefresh = now;
      refreshExternalProgress();
    };
    const refreshVisible = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [refreshExternalProgress]);

  const { run, runningStage, isRunning } = useRunPhase(id, refreshExternalProgress);

  function activateTab(tab: WorkspaceTab, focus = false) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("tab", tab);
      if (tab !== "characters") next.delete("codex");
      return next;
    }, { replace: true });
    if (focus) window.requestAnimationFrame(() => document.getElementById(`pw-tab-${tab}`)?.focus());
  }

  function onTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    activateTab(TABS[next].id, true);
  }

  if (!project && !coreError) return <ProjectWorkspaceLoading />;
  if (!project && coreError) {
    return <Scene quiet><div className="project-workspace"><div className="pw-shell"><div className="pw-core-error" role="alert"><strong>作品详情加载失败</strong><p>{coreError || "找不到这个作品。"}</p><button type="button" className="pw-soft-button" onClick={() => void loadCore()}>重新加载</button></div></div></div></Scene>;
  }
  if (!project) return <ProjectWorkspaceLoading />;

  const drafted = chapters.filter(chapterHasProse).length;
  const words = project.word_count ?? chapters.reduce((sum, chapter) => sum + chapter.word_count, 0);
  const nextChapter = chapters.length ? Math.max(...chapters.map((chapter) => chapter.number)) + 1 : 1;
  const coverUrl = api.assetUrl(project.cover?.image_url);
  const delivery = project.delivery;
  const projectStatusTone = ["failed", "interrupted"].includes(project.status)
    ? "danger"
    : ["pending", "queued", "paused"].includes(project.status)
      ? "neutral"
      : "ready";

  return (
    <Scene quiet>
      <div className="project-workspace">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }} className="pw-shell">
          <header className="pw-book-header">
            <div className="pw-book-header__copy">
              <Link to="/" className="pw-back-link">← 返回作品库</Link>
              <div className="pw-book-identity">
                {coverUrl ? <img className="pw-book-cover" src={coverUrl} alt={`${project.title} 当前封面`} /> : <div className="pw-book-cover pw-book-cover--empty" aria-label="尚未选择封面">{project.title.trim().slice(0, 1) || "书"}</div>}
                <div className="pw-book-copy">
                  <div className="pw-title-row"><div><span className="pw-eyebrow">{project.genre || "类型未注明"}</span><h1>{project.title}</h1></div><span className={`pw-status-pill pw-status-pill--${projectStatusTone}`}>{PROJECT_STATUS[project.status] ?? project.status}</span></div>
                  <p className="pw-book-author">{project.author || "作者未注明"}</p>
                  <p className="pw-book-deck">{project.premise || "这部作品尚未填写简介。"}</p>
                  <div className="pw-book-meta" aria-label="作品状态"><span><strong>{drafted} / {project.chapter_count || chapters.length}</strong> 章已有正文</span><i /><span><strong>{words.toLocaleString("zh-CN")}</strong> 字</span><i /><span>{project.cover?.status === "selected" ? "正式封面已选定" : project.cover?.detail || "封面尚未选定"}</span></div>
                </div>
              </div>
            </div>
            <div className="pw-header-actions">
              <DeliveryPackageButton projectId={id} delivery={delivery} onChanged={refreshExternalProgress} />
              <Link className="pw-primary-button" to={`/projects/${encodeURIComponent(id)}/read`}>阅读全文</Link>
              <Link className="pw-soft-button" to={`/projects/${encodeURIComponent(id)}/covers`}>封面工作室</Link>
            </div>
          </header>

          <nav className="pw-tabs" role="tablist" aria-label="作品详情标签页">
            {TABS.map((tab, index) => <button key={tab.id} id={`pw-tab-${tab.id}`} type="button" role="tab" aria-selected={activeTab === tab.id} aria-controls={`pw-panel-${tab.id}`} tabIndex={activeTab === tab.id ? 0 : -1} onClick={() => activateTab(tab.id)} onKeyDown={(event) => onTabKeyDown(event, index)}><span>{String(index + 1).padStart(2, "0")}</span>{tab.label}</button>)}
          </nav>

          <div className="pw-panel-wrap">
            {coreError && <div className="pw-refresh-error" role="alert"><span>作品进度刷新失败：{coreError}</span><button type="button" className="pw-text-button" onClick={() => void loadCore()}>重试</button></div>}
            {project.task_id && activeTab !== "overview" && <details className="pw-task-drawer">
              <summary><span>创作任务</span><strong>查看状态、暂停或恢复</strong></summary>
              <TaskStatusPanel key={`${project.id}:${project.task_id}`} projectId={project.id} runId={project.task_id} onTaskChanged={refreshExternalProgress} />
            </details>}
            <WorkspacePanel id={`pw-panel-${activeTab}`} labelledBy={`pw-tab-${activeTab}`}>
              {activeTab === "overview" && <ProjectOverview project={project} chapters={chapters} words={words} drafted={drafted} isRunning={isRunning} runningStage={runningStage} nextChapter={nextChapter} onRun={(stage, payload) => run(stage, payload)} onProject={(nextProject) => setCoreResult({ projectId: id, project: nextProject, chapters })} onTaskChanged={refreshExternalProgress} onGo={activateTab} />}
              {activeTab === "chapters" && <ProjectChaptersTab projectId={id} chapters={chapters} story={story} loading={storyStatus === "loading" || storyStatus === "idle"} error={storyError} onRetry={() => void loadStory()} isRunning={isRunning} onPlanChapter={(number) => run("plan_chapter", { number })} />}
              {activeTab === "story" && <ProjectStoryTab projectId={id} story={story} loading={storyStatus === "loading" || storyStatus === "idle"} error={storyError} onRetry={() => void loadStory()} />}
              {activeTab === "characters" && <ProjectCharactersTab story={story} loading={storyStatus === "loading" || storyStatus === "idle"} error={storyError} onRetry={() => void loadStory()} focusCharacterId={focusCharacterId} />}
              {activeTab === "delivery" && <ProjectDeliveryTab projectId={id} delivery={delivery} refreshKey={externalRefreshKey} onChanged={refreshExternalProgress} />}
            </WorkspacePanel>
          </div>
        </motion.div>
      </div>
    </Scene>
  );
}

function ProjectOverview({ project, chapters, words, drafted, isRunning, runningStage, nextChapter, onRun, onProject, onTaskChanged, onGo }: {
  project: ProjectDetail; chapters: ChapterSummary[]; words: number; drafted: number; isRunning: boolean;
  runningStage: string | null; nextChapter: number; onRun: (stage: string, payload: Record<string, unknown>) => void;
  onProject: (project: ProjectDetail) => void; onTaskChanged: () => void; onGo: (tab: WorkspaceTab, focus?: boolean) => void;
}) {
  const toast = useToast();
  const total = project.chapter_count || chapters.length;
  const percent = total > 0 ? Math.min(100, Math.round((drafted / total) * 100)) : 0;
  return (
    <section aria-labelledby="pw-overview-title">
      <div className="pw-section-heading"><div><span className="pw-eyebrow">作品进度</span><h2 id="pw-overview-title">作品概览</h2><p>查看全书进度、故事简介和当前创作任务。</p></div><div className="pw-heading-actions"><Link className="pw-primary-button" to={`/projects/${encodeURIComponent(project.id)}/read`}>阅读全文</Link><button type="button" className="pw-soft-button" onClick={() => onGo("delivery", true)}>查看交付</button></div></div>
      <div className="pw-metric-grid"><article><span>章节进度</span><strong>{drafted} / {total}</strong><p>{percent}% 已有正文</p></article><article><span>当前字数</span><strong>{words.toLocaleString("zh-CN")}</strong><p>{project.target_word_count ? `目标 ${project.target_word_count.toLocaleString("zh-CN")} 字` : "目标字数未设置"}</p></article><article><span>作品状态</span><strong>{PROJECT_STATUS[project.status] ?? project.status}</strong><p>{project.task_id ? "创作任务记录可查看" : "没有关联中的创作任务"}</p></article></div>
      <div className="pw-progress-line" aria-label={`章节进度 ${percent}%`}><span style={{ width: `${percent}%` }} /></div>
      {project.task_id && <TaskStatusPanel key={`${project.id}:${project.task_id}`} projectId={project.id} runId={project.task_id} onTaskChanged={onTaskChanged} />}
      <div className="pw-overview-grid"><article className="pw-quiet-card"><span className="pw-eyebrow">故事简介</span><h3>{project.title}</h3><p>{project.premise || "尚未填写作品简介。故事骨架中的内容会在“故事骨架”标签按来源展示。"}</p><button type="button" className="pw-text-button" onClick={() => onGo("story", true)}>查看故事骨架 →</button></article><article className="pw-quiet-card"><span className="pw-eyebrow">继续创作</span><h3>规划与设置</h3><p>规划操作会启动创作任务；章节阅读和编辑使用各自独立入口。</p><div className="pw-card-actions"><button type="button" className="pw-soft-button" disabled={isRunning} onClick={() => onRun("plan_outline", { chapters: total || 12, words: project.target_word_count || 24000 })}>{runningStage === "plan_outline" ? "正在规划…" : "规划全书大纲"}</button><button type="button" className="pw-soft-button" disabled={isRunning} onClick={() => onRun("plan_chapter", { number: nextChapter })}>{runningStage === "plan_chapter" ? "正在规划…" : `规划第 ${nextChapter} 章`}</button></div></article></div>
      <div className="pw-overview-settings">
        <WritingTargets projectId={project.id} project={project} wordCount={words} onUpdated={onProject} />
        <section className="pw-rating-setting" aria-label="内容分级"><div><strong>内容分级</strong><p>用于封面、发布和阅读提示。</p></div><ChoiceGroup label="内容分级" variant="segmented" size="sm" value={project.content_rating === "mature" ? "mature" : "general"} onChange={async (next) => {
          try { onProject(await api.updateProject(project.id, { content_rating: next })); toast(next === "mature" ? "已标记为成人内容" : "已标记为普通内容", "success"); }
          catch (error) { toast(error instanceof Error ? error.message : String(error), "error"); }
        }} options={[{ value: "general", label: "普通" }, { value: "mature", label: "成人" }]} /></section>
      </div>
    </section>
  );
}

function ProjectDeliveryTab({ projectId, delivery, refreshKey, onChanged }: { projectId: string; delivery?: ProjectDeliverySummary; refreshKey: number; onChanged: () => void }) {
  return (
    <section aria-labelledby="pw-delivery-title">
      <div className="pw-section-heading"><div><span className="pw-eyebrow">交付</span><h2 id="pw-delivery-title">费用与交付</h2><p>查看制作费用、Token 用量与可下载文件。</p></div><DeliveryPackageButton projectId={projectId} delivery={delivery} onChanged={onChanged} /></div>
      <GenerationCostPanel owner={projectId} refreshKey={refreshKey} />
      <div className="pw-compile-wrap"><CompilePanel projectId={projectId} /></div>
      <section className="pw-delivery-note"><div><span className="pw-eyebrow">交付包</span><h3>与正式封面保持同步</h3><p>ZIP 只有在交付状态就绪且内容与当前选定封面一致时才可下载。其他格式由上方编译工具单独生成，不会冒充交付包。</p></div><DeliveryPackageButton projectId={projectId} delivery={delivery} onChanged={onChanged} /></section>
    </section>
  );
}

function ProjectWorkspaceLoading() {
  return <Scene quiet><div className="project-workspace"><div className="pw-shell pw-loading" role="status" aria-label="正在加载作品详情"><div className="pw-loading-cover" /><div className="pw-loading-copy"><span /><strong /><p /><p /></div></div></div></Scene>;
}
