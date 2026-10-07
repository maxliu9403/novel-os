import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  api,
  type ProjectDeletionPreview,
  type ProjectSummary,
  type StudioLlmStatus,
} from "../api/client";
import ProjectCard from "../components/ProjectCard";
import Modal, { Field, fieldClass, textareaClass } from "../components/Modal";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import GenreChips from "../components/GenreChips";
import NovelWorkshop, { type WorkshopSeed } from "../components/NovelWorkshop";
import { mergeGenres } from "../lib/genres";
import { useToast } from "../components/toastContext";

const grid = { hidden: {}, show: { transition: { staggerChildren: 0.045 } } };
const card = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.2, 0.8, 0.2, 1] as const } },
};

export default function ProjectsList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [llm, setLlm] = useState<StudioLlmStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null);
  const [deletePreview, setDeletePreview] = useState<ProjectDeletionPreview | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    api.projects().then(setProjects).catch((e) => setError(String(e)));
    api.studioLlm().then(setLlm).catch(() => setLlm(null));
  }, []);

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
      setProjects((current) => current?.filter((project) => project.id !== deleteTarget.id) ?? current);
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
    <Scene>
      <div className="workspace-page">
        <motion.div
          initial={{ opacity: 0, y: 18, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
          className="glass-shell p-3 sm:p-4"
        >
          <div className="glass-panel px-5 py-7 sm:px-8 sm:py-9 lg:px-10">
            <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="eyebrow">创作工作台</p>
                <h1 className="font-display text-[32px] font-semibold leading-none tracking-[-0.035em] text-ink-text sm:text-[36px]">
                  作品库
                </h1>
                <p className="mt-3 max-w-lg text-[14px] leading-relaxed text-ink-muted">
                  在一个工作台中管理作品、章节与智能创作流程。
                </p>
              </div>
              <button type="button" onClick={() => setOpen(true)} className="btn-primary shrink-0">
                新建作品
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
                        ? "先搭结构，再写正文，并始终守住连续性。新建作品、规划大纲，然后逐章创作。"
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

            {error && (
              <div className="mb-4 rounded-2xl border border-[rgba(74,91,133,0.16)] bg-white/80 px-4 py-3 text-[13px] text-ink-text">
                作品加载失败：{error}
              </div>
            )}

            {!error && !projects && <SkeletonGrid />}

            {!error && projects && projects.length === 0 && (
              <div className="rounded-[24px] border border-dashed border-[rgba(74,91,133,0.18)] bg-white/50 px-8 py-14 text-center">
                <p className="font-display text-[18px] tracking-[-0.02em] text-ink-text">还没有作品</p>
                <p className="mt-2 text-[13px] text-ink-muted">从书名和作品类型开始创作。</p>
                <button type="button" onClick={() => setOpen(true)} className="btn-primary mt-5">
                  新建作品
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

            {projects && projects.length > 0 && (
              <motion.div
                variants={grid}
                initial="hidden"
                animate="show"
                className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
              >
                {projects.map((p) => (
                  <motion.div key={p.id} variants={card}>
                    <ProjectCard p={p} onDelete={openDelete} />
                  </motion.div>
                ))}
              </motion.div>
            )}
          </div>
        </motion.div>
      </div>

      <NewProjectModal open={open} onClose={() => setOpen(false)} />
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

function NewProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [genres, setGenres] = useState<string[]>([]);
  const [otherGenre, setOtherGenre] = useState("");
  const [premise, setPremise] = useState("");
  const [methodReview, setMethodReview] = useState(true);
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState(false);
  const [workshopOpen, setWorkshopOpen] = useState(false);
  const [workshopSeed, setWorkshopSeed] = useState<WorkshopSeed | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      const merged = mergeGenres(genres, otherGenre);
      const p = await api.createProject({
        title: title.trim(),
        author: author.trim(),
        genres: merged,
        genre: merged.join(" · "),
        premise: premise.trim(),
        method_mode: methodReview ? "advisory" : "off",
      });
      toast("作品已创建", "success");
      navigate(`/projects/${p.id}`);
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
      setBusy(false);
    }
  }

  return (
    <>
    <Modal open={open && !workshopOpen} onClose={onClose} title="新建作品">
      <form onSubmit={create}>
        <Field label="书名">
          <input
            autoFocus
            className={fieldClass}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：最后的信号"
          />
        </Field>
        <Field label="作者">
          <input
            className={fieldClass}
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            placeholder="留空则自动生成笔名"
            autoComplete="name"
          />
        </Field>
        <div className="mb-4">
          <span className="mb-1.5 block text-[12px] font-medium tracking-[-0.01em] text-ink-muted">
            作品类型
          </span>
          <GenreChips
            selected={genres}
            onChange={setGenres}
            other={otherGenre}
            onOtherChange={setOtherGenre}
          />
          <p className="mt-1.5 text-[11.5px] text-ink-muted">
            可选择一个或多个类型，也可以组合类型。
          </p>
        </div>
        <div className="mb-4">
          <label htmlFor="manuscript-premise" className="mb-1.5 block text-[12px] font-medium tracking-[-0.01em] text-ink-muted">
            故事构想
          </label>
          <textarea
            id="manuscript-premise"
            className={textareaClass}
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            placeholder="一座被浓雾笼罩的港口，一只永远不指向北方的罗盘……"
            rows={2}
          />
          <p className="mt-1.5 text-[11.5px] text-ink-muted">
            选填。用两到四句话描述构想，架构师会以此规划故事。
          </p>
        </div>
        <label className="flex items-start gap-2 rounded-lg bg-white/50 p-3 text-xs leading-relaxed text-ink-muted">
          <input type="checkbox" checked={methodReview} onChange={e => setMethodReview(e.target.checked)} className="mt-1" />
          <span>启用只读写作评审：检查英文表达与免费章节的选择、回报和阅读期待。
            全书任务使用 Judge 路由，按已批准的免费窗口每章增加一次评审，格式修正最多一次；可能增加模型费用。
            不自动修改正文，单阶段写作不触发。可在章节评审面板关闭未来运行的默认值。</span>
        </label>
        <div className="sticky bottom-0 -mx-1 mt-4 flex flex-wrap justify-end gap-2 border-t border-[rgba(74,91,133,0.08)] bg-gradient-to-t from-white/95 via-white/90 to-transparent px-1 pb-1 pt-4">
          <button type="button" onClick={onClose} className="btn-ghost">
            取消
          </button>
          <button type="submit" disabled={!title.trim() || busy} className="btn-secondary disabled:opacity-40">
            {busy ? "正在创建…" : "创建作品"}
          </button>
          <button type="button" disabled={busy} className="btn-primary !bg-violet hover:!bg-[#5d5bd2] !shadow-[0_8px_20px_rgba(104,103,234,0.18)]" onClick={() => {
            setWorkshopSeed({ title, author, genres: mergeGenres(genres, otherGenre), premise, method_mode: methodReview ? "advisory" : "off" });
            setWorkshopOpen(true);
          }}>头脑风暴完善</button>
        </div>
      </form>
    </Modal>
    {workshopSeed && <NovelWorkshop open={open && workshopOpen} seed={workshopSeed} onClose={onClose} onBack={() => setWorkshopOpen(false)} />}
    </>
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
