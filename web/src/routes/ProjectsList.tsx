import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import {
  api,
  type ProjectDeletionPreview,
  type ProjectSummary,
  type StudioLlmStatus,
} from "../api/client";
import ProjectCard from "../components/ProjectCard";
import { COVER_STATUS_LABELS } from "../lib/projectCovers";
import Modal, { Field, fieldClass } from "../components/Modal";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import { useToast } from "../components/toastContext";
import { libraryResource } from "../lib/libraryResource";
import { useLibrary } from "../lib/useLibrary";

export default function ProjectsList() {
  const [llm, setLlm] = useState<StudioLlmStatus | null>(null);
  const [tourOpen, setTourOpen] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null);
  const [deletePreview, setDeletePreview] = useState<ProjectDeletionPreview | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();
  const query = searchParams.get("q") ?? "";
  const manuscriptFilter = searchParams.get("manuscript") ?? "all";
  const coverFilter = searchParams.get("cover") ?? "all";
  function setFilter(name: string, value: string) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (!value || value === "all") next.delete(name); else next.set(name, value);
      return next;
    }, { replace: true });
  }
  function clearFilters() {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      ["q", "manuscript", "cover"].forEach((key) => next.delete(key));
      return next;
    }, { replace: true });
  }
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const libraryQuery = useMemo(() => ({ q: debouncedQuery, status: manuscriptFilter, cover: coverFilter, limit: 20 }), [debouncedQuery, manuscriptFilter, coverFilter]);
  const library = useLibrary(libraryQuery);
  const projects = library.items;
  const error = library.error;
  const refreshError = library.refreshError;
  const scrollKey = JSON.stringify(libraryQuery);
  useLayoutEffect(() => {
    const saved = libraryResource.scrollPosition(JSON.parse(scrollKey));
    const container = document.querySelector<HTMLElement>("#main");
    if (container) container.scrollTop = saved;
    else if (saved) window.scrollTo(0, saved);
    return () => libraryResource.saveScroll(JSON.parse(scrollKey), container?.scrollTop ?? window.scrollY);
  }, [scrollKey]);
  useEffect(() => {
    let cancelled = false;
    api.studioLlm().then((value) => { if (!cancelled) setLlm(value); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  async function resumeProject(project: ProjectSummary) {
    if (!project.task_id || !project.task_can_resume) return;
    await api.resumeProjectTask(project.id, project.task_id);
    libraryResource.patch(project.id, { status: "running", task_can_resume: false, task_error: null });
    toast("已提交恢复，正在校验并复用原任务的有效进度", "info");
  }

  useEffect(() => {
    if (!deleteTarget) return;
    let cancelled = false;
    api.projectDeletionPreview(deleteTarget.id)
      .then((preview) => {
        if (!cancelled) setDeletePreview(preview);
      })
      .catch((e) => {
        if (!cancelled) setDeleteError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [deleteTarget]);

  function openDelete(project: ProjectSummary) {
    setDeletePreview(null);
    setDeleteConfirm("");
    setDeleteError(null);
    setDeleteTarget(project);
  }

  async function dismissOnboarding() {
    try {
      const next = await api.updateStudioLlm({ onboarding_completed: true });
      setLlm(next);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  }

  async function openSample() {
    setSampleBusy(true);
    try {
      const p = await api.createSampleProject();
      await api.updateStudioLlm({ onboarding_completed: true }).then(setLlm).catch(() => undefined);
      toast("示例作品已准备好", "success");
      navigate(`/projects/${p.id}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
      setSampleBusy(false);
    }
  }

  function closeDelete() {
    if (deleteBusy) return;
    setDeleteTarget(null);
    setDeletePreview(null);
    setDeleteConfirm("");
    setDeleteError(null);
  }

  async function deleteProject() {
    if (!deleteTarget || !deletePreview?.can_delete || deleteConfirm !== deletePreview.title) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.deleteProject(deleteTarget.id, deletePreview.title);
      libraryResource.remove(deleteTarget.id);
      toast(`《${deletePreview.title}》及其全部资料已永久删除`, "success");
      setDeleteTarget(null);
      setDeletePreview(null);
      setDeleteConfirm("");
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  }

  const showWelcome = llm && (!llm.onboarding_completed || !llm.configured);

  return (
    <Scene quiet className="library-scene">
      <div className="workspace-page library-page">
        <motion.div
          initial={false}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
          className="glass-shell library-shell p-3 sm:p-4"
        >
          <div className="glass-panel library-surface px-5 py-7 sm:px-8 sm:py-9 lg:px-10">
            <header className="library-header">
              <div>
                <p className="library-eyebrow">创作工作台</p>
                <h1>
                  作品库
                </h1>
                <p className="library-header__intro">
                  故事、文稿与封面，在这里继续。
                </p>
              </div>
              <button type="button" onClick={() => navigate("/workshop?new=1")} className="btn-primary library-create">
                <Icon name="plus" className="h-4 w-4" /> 新建小说
              </button>
            </header>

            {showWelcome && (
              <div className="mb-8 rounded-[24px] border border-[rgba(104,103,234,0.22)] bg-[linear-gradient(145deg,rgba(238,237,255,0.9),rgba(255,255,255,0.75))] p-5 sm:p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="max-w-xl">
                    <p className="text-[15px] font-semibold text-ink-text">
                      {llm.configured ? "欢迎使用 Novel OS" : "请先连接写作模型"}
                    </p>
                    <p className="mt-1.5 text-[13px] leading-relaxed text-ink-muted">
                      {llm.configured
                        ? "先搭结构，再写正文，并始终守住连续性。新建小说、规划大纲，然后逐章创作。"
                        : "智能创作需要可用的大语言模型。请前往设置选择质量、快速、本地（Ollama）或自带密钥模型。"}
                    </p>
                    <ol className="mt-3 list-decimal space-y-1 pl-4 text-[12.5px] text-ink-muted">
                      <li>在设置中配置模型</li>
                      <li>创建一部作品</li>
                      <li>规划大纲 → 规划章节 → 生成初稿 → 人工定稿</li>
                    </ol>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Link to="/settings" className="btn-primary inline-flex items-center gap-2">
                      <Icon name="sparkles" className="h-3.5 w-3.5" /> 模型设置
                    </Link>
                    <button
                      type="button"
                      onClick={openSample}
                      disabled={sampleBusy}
                      className="btn-secondary disabled:opacity-40"
                    >
                      {sampleBusy ? "正在打开…" : "打开示例"}
                    </button>
                    <button type="button" onClick={() => setTourOpen(true)} className="btn-ghost">
                      使用导览
                    </button>
                    {llm.onboarding_completed === false && (
                      <button type="button" onClick={dismissOnboarding} className="btn-ghost">
                        不再显示
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {(library.total > 0 || query || manuscriptFilter !== "all" || coverFilter !== "all") && <section className="library-toolbar" aria-label="筛选作品">
              <label className="library-search">
                <Icon name="search" className="h-4 w-4" />
                <span className="sr-only">搜索作品</span>
                <input type="search" value={query} onChange={(event) => setFilter("q", event.target.value)} placeholder="搜索书名、作者、类型或任务 ID" />
              </label>
              <label className="library-filter"><span>正文</span><select aria-label="正文状态" value={manuscriptFilter} onChange={(event) => setFilter("manuscript", event.target.value)}>
                <option value="all">全部正文状态</option><option value="writing">创作进行中</option>
                <option value="completed">正文已完成</option><option value="attention">正文待处理</option><option value="draft">尚未启动</option>
              </select></label>
              <label className="library-filter"><span>封面</span><select aria-label="封面状态" value={coverFilter} onChange={(event) => setFilter("cover", event.target.value)}>
                <option value="all">全部封面状态</option>
                {Object.entries(COVER_STATUS_LABELS).map(([status, label]) => <option key={status} value={status}>封面{label}</option>)}
              </select></label>
            </section>}

            {error && (
              <div className="mb-4 rounded-2xl border border-[rgba(74,91,133,0.16)] bg-white/80 px-4 py-3 text-[13px] text-ink-text">
                作品加载失败：{error}
                <button type="button" onClick={() => void libraryResource.refresh(libraryQuery)} className="library-inline-button ml-3">重新加载</button>
              </div>
            )}

            {!error && !projects && <SkeletonGrid />}
            {projects && library.catalogStatus === "rebuilding" && <p role="status" className="library-sync-note">
              正在同步作品摘要，已保存的作品会逐步显示。正文和创作任务不受影响。
            </p>}
            {projects && library.refreshing && <p role="status" className="library-sync-note">正在更新作品状态…</p>}

            {refreshError && (
              <p role="status" className="mb-4 text-[12px] text-[#b84363]">
                任务状态刷新失败，正在重试：{refreshError}
              </p>
            )}

            {!error && projects && library.total === 0 && library.catalogStatus !== "rebuilding" && (
              <div className="rounded-[24px] border border-dashed border-[rgba(74,91,133,0.18)] bg-white/50 px-8 py-14 text-center">
                <p className="font-display text-[18px] tracking-[-0.02em] text-ink-text">还没有作品</p>
                <p className="mt-2 text-[13px] text-ink-muted">从书名和作品类型开始创作。</p>
                <button type="button" onClick={() => navigate("/workshop?new=1")} className="btn-primary mt-5">
                  新建小说
                </button>
                <button
                  type="button"
                  onClick={openSample}
                  disabled={sampleBusy}
                  className="btn-secondary mt-3 disabled:opacity-40"
                >
                  {sampleBusy ? "正在打开…" : "体验示例作品"}
                </button>
              </div>
            )}

            {projects && library.total > 0 && (
              <>
                <div className="library-results"><span>{query || manuscriptFilter !== "all" || coverFilter !== "all"
                  ? `${library.filteredTotal} / ${library.total} 部作品` : `${library.total} 部作品`}</span><span>按任务启动时间倒序排列</span></div>
                {projects.length === 0 && <div className="library-no-results" role="status">
                  <h2>没有符合条件的作品</h2><p>试试其他关键词，或清除状态筛选。</p>
                  <button type="button" onClick={clearFilters} className="library-inline-button">清除筛选</button>
                </div>}
                <div className="library-grid" aria-busy={library.refreshing}>
                  {projects.map((p) => (
                    <ProjectCard key={`${p.id}:${p.epoch ?? 0}`} p={p} onDelete={openDelete} onResume={resumeProject}
                      onDeliveryChanged={() => libraryResource.synchronize()} />
                  ))}
                </div>
                {library.nextCursor && <div className="library-pagination">
                  <span>已显示 {projects.length} / {library.filteredTotal} 部</span>
                  <button type="button" className="btn-secondary" disabled={library.loadingMore}
                    onClick={() => void libraryResource.load(libraryQuery, true)}>
                    {library.loadingMore ? "正在加载…" : "加载更多作品"}
                  </button>
                </div>}
              </>
            )}
          </div>
        </motion.div>
      </div>

      <DeleteProjectModal
        target={deleteTarget}
        preview={deletePreview}
        confirmation={deleteConfirm}
        error={deleteError}
        busy={deleteBusy}
        onConfirmationChange={setDeleteConfirm}
        onClose={closeDelete}
        onDelete={deleteProject}
      />
      <TourModal
        open={tourOpen}
        onClose={() => setTourOpen(false)}
        onOpenSample={openSample}
        sampleBusy={sampleBusy}
        onDismiss={async () => {
          await dismissOnboarding();
          setTourOpen(false);
        }}
      />
    </Scene>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function DeleteProjectModal({
  target,
  preview,
  confirmation,
  error,
  busy,
  onConfirmationChange,
  onClose,
  onDelete,
}: {
  target: ProjectSummary | null;
  preview: ProjectDeletionPreview | null;
  confirmation: string;
  error: string | null;
  busy: boolean;
  onConfirmationChange: (value: string) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  const counts = preview?.counts ?? {};
  const historyCount = (counts.artifact_revisions ?? 0) + (counts.snapshots ?? 0);
  const reviewCount = (counts.comments ?? 0)
    + (counts.evaluation_reports ?? 0)
    + (counts.quality_findings ?? 0)
    + (counts.promotion_receipts ?? 0);
  const canonicalTitle = preview?.title ?? target?.title ?? "";
  const confirmed = Boolean(preview && confirmation === preview.title);

  return (
    <Modal open={Boolean(target)} onClose={onClose} title="永久删除作品">
      {target && (
        <div>
          <div className="rounded-2xl border border-[rgba(184,67,99,0.22)] bg-[#fff3f6] p-4">
            <p className="text-[14px] font-semibold text-[#94314e]">
              此操作无法撤销
            </p>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-[#8b5262]">
              将永久删除《{canonicalTitle}》的正文、设定、大纲、历史版本、批注、检查记录、封面与全部媒体文件。
            </p>
          </div>

          {!preview && !error && (
            <div className="py-8 text-center text-[13px] text-ink-muted">正在核对待删除资料…</div>
          )}

          {preview && (
            <>
              <dl className="my-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
                <DeletionStat label="章节" value={preview.chapter_count} />
                <DeletionStat label="创作产物" value={counts.artifacts ?? 0} />
                <DeletionStat label="历史版本" value={historyCount} />
                <DeletionStat label="批注与检查" value={reviewCount} />
                <DeletionStat label="媒体文件" value={preview.media_files} />
                <DeletionStat
                  label="文件占用"
                  value={formatBytes(preview.project_bytes + preview.media_bytes)}
                />
              </dl>

              {!preview.can_delete && (
                <div className="mb-4 rounded-2xl border border-[rgba(196,122,27,0.24)] bg-[#fff7e8] px-4 py-3 text-[12.5px] leading-relaxed text-[#885816]">
                  该作品仍有 {preview.running_job_ids.length} 个任务正在运行。请等待任务结束后再删除。
                </div>
              )}

              <Field label={`请输入书名“${canonicalTitle}”确认删除`}>
                <input
                  autoFocus
                  className={fieldClass}
                  value={confirmation}
                  onChange={(event) => onConfirmationChange(event.target.value)}
                  placeholder={canonicalTitle}
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
            </>
          )}

          {error && (
            <div role="alert" className="mb-4 rounded-2xl border border-[rgba(184,67,99,0.22)] bg-[#fff3f6] px-4 py-3 text-[12.5px] leading-relaxed text-[#94314e]">
              无法删除：{error}
            </div>
          )}

          <div className="mt-5 flex justify-end gap-3 border-t border-[rgba(74,91,133,0.08)] pt-4">
            <button type="button" onClick={onClose} disabled={busy} className="btn-ghost">
              取消
            </button>
            <button
              type="button"
              onClick={onDelete}
              disabled={!preview?.can_delete || !confirmed || busy}
              className="btn-danger"
            >
              {busy ? "正在删除…" : "永久删除"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function DeletionStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl bg-[rgba(74,91,133,0.055)] px-3 py-3">
      <dt className="text-[11px] text-ink-muted">{label}</dt>
      <dd className="nums mt-1 text-[15px] font-semibold text-ink-text">{value}</dd>
    </div>
  );
}

const TOUR_STEPS = [
  {
    title: "连接写作模型",
    body: "打开设置，选择质量、快速、本地（Ollama）或自带密钥模型。智能体需要可用的模型才能开始创作。",
  },
  {
    title: "创建或打开作品",
    body: "可以从空白作品开始，也可以打开自带人物设定、大纲和短篇初稿的示例作品。",
  },
  {
    title: "运行创作流程",
    body: "规划大纲 → 规划章节 → 撰写初稿 → 编辑 → 校验。每个阶段都会保留可查看、可修订的产物。",
  },
  {
    title: "守住故事连续性",
    body: "作品概览和章节侧栏会显示连续性检查；连续性守卫还会把设定库作为故事事实依据。",
  },
];

function TourModal({
  open, onClose, onOpenSample, sampleBusy, onDismiss,
}: {
  open: boolean;
  onClose: () => void;
  onOpenSample: () => void;
  sampleBusy: boolean;
  onDismiss: () => void;
}) {
  const [step, setStep] = useState(0);

  // Restart the tour each time it opens, during render so step 1 is what the
  // dialog paints first.
  const [lastOpen, setLastOpen] = useState(open);
  if (open !== lastOpen) {
    setLastOpen(open);
    if (open) setStep(0);
  }

  const current = TOUR_STEPS[step];

  return (
    <Modal open={open} onClose={onClose} title="工作台导览">
      <p className="text-[12px] font-medium text-ink-muted">
        第 {step + 1} 步，共 {TOUR_STEPS.length} 步
      </p>
      <h3 className="mt-2 font-display text-[20px] font-semibold tracking-tight text-ink-text">
        {current.title}
      </h3>
      <p className="mt-2 text-[14px] leading-relaxed text-ink-muted">{current.body}</p>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={onDismiss} className="btn-ghost text-[12.5px]">
          跳过
        </button>
        <div className="flex flex-wrap gap-2">
          {step > 0 && (
            <button type="button" onClick={() => setStep((s) => s - 1)} className="btn-ghost">
              上一步
            </button>
          )}
          {step < TOUR_STEPS.length - 1 ? (
            <button type="button" onClick={() => setStep((s) => s + 1)} className="btn-primary">
              下一步
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={onOpenSample}
                disabled={sampleBusy}
                className="btn-secondary disabled:opacity-40"
              >
                {sampleBusy ? "正在打开…" : "打开示例"}
              </button>
              <button type="button" onClick={onDismiss} className="btn-primary">
                完成
              </button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-44 animate-pulse rounded-[22px] bg-white/50" />
      ))}
    </div>
  );
}
