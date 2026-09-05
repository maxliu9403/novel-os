import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "motion/react";
import { api, type CoverCandidate, type CoverDirection, type CoverSet, type JobStatus, type ProjectDetail, type StudioCoverStatus } from "../api/client";
import Scene from "../components/Scene";
import Icon from "../components/Icon";
import { useToast } from "../components/toastContext";
import { useConfirm } from "../components/confirmContext";


const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

type CoverProgress = {
  mode: "generate" | "retry";
  completed: number;
  total: number;
  phase: "preparing" | "rendering";
  candidateNumber?: number;
};

type JobProgressContext =
  | { mode: "generate"; total: number; knownSetIds: Set<string> }
  | { mode: "retry"; candidateNumber: number };

export default function CoverStudio() {
  const { id = "" } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [settings, setSettings] = useState<StudioCoverStatus | null>(null);
  const [sets, setSets] = useState<CoverSet[]>([]);
  const [directions, setDirections] = useState<CoverDirection[]>([]);
  const [directionsLoaded, setDirectionsLoaded] = useState(false);
  const [selectedSetId, setSelectedSetId] = useState("");
  const [loadedProjectId, setLoadedProjectId] = useState("");
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState<CoverProgress | null>(null);
  const [error, setError] = useState("");

  const applySets = useCallback((next: CoverSet[], preferredSetId?: string) => {
    setSets(next);
    setSelectedSetId((current) => (
      preferredSetId && next.some((item) => item.cover_set_id === preferredSetId)
        ? preferredSetId
        : current && next.some((item) => item.cover_set_id === current)
          ? current
        : next[0]?.cover_set_id || ""
    ));
  }, []);

  const load = useCallback(() => Promise.all([
      api.project(id), api.studioCover(), api.covers(id),
    ]), [id]);

  useEffect(() => {
    let live = true;
    load()
      .then(([nextProject, nextSettings, nextSets]) => {
        if (!live) return;
        setProject(nextProject);
        setSettings(nextSettings);
        applySets(nextSets);
        setError("");
      })
      .catch((cause) => live && setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => live && setLoadedProjectId(id));
    return () => { live = false; };
  }, [applySets, id, load]);

  useEffect(() => {
    let live = true;
    api.coverDirections(id)
      .then((next) => live && setDirections(Array.isArray(next) ? next : []))
      .catch(() => live && setDirections([]))
      .finally(() => live && setDirectionsLoaded(true));
    return () => { live = false; };
  }, [id]);

  const loading = loadedProjectId !== id;

  const current = useMemo(
    () => sets.find((item) => item.cover_set_id === selectedSetId) || sets[0] || null,
    [selectedSetId, sets],
  );
  const approvedDirection = directions[0]?.status === "approved" ? directions[0] : null;
  const generationAllowed = Boolean(settings?.configured && approvedDirection);

  const updateProgress = (nextSets: CoverSet[], context: JobProgressContext) => {
    if (context.mode === "retry") {
      setProgress((currentProgress) => currentProgress?.mode === "retry"
        ? { ...currentProgress, phase: "rendering" }
        : currentProgress);
      return undefined;
    }

    // A generation job creates its CoverSet asynchronously. Until that new set
    // appears, keep the user in a preparation state instead of counting an old set.
    const generatedSet = nextSets.find((item) => !context.knownSetIds.has(item.cover_set_id));
    if (!generatedSet) {
      setProgress((currentProgress) => currentProgress?.mode === "generate"
        ? { ...currentProgress, phase: "preparing" }
        : currentProgress);
      return undefined;
    }
    const completed = generatedSet.candidates.filter((candidate) => candidate.status !== "pending").length;
    setProgress((currentProgress) => currentProgress?.mode === "generate"
      ? {
          ...currentProgress,
          completed: Math.min(completed, currentProgress.total),
          total: generatedSet.requested_count || currentProgress.total,
          phase: "rendering",
        }
      : currentProgress);
    return generatedSet.cover_set_id;
  };

  const waitForJob = async (initial: JobStatus, context: JobProgressContext) => {
    let job = initial;
    while (job.status === "running") {
      await wait(700);
      const [nextJob, nextSets] = await Promise.all([api.getJob(job.job_id), api.covers(id)]);
      job = nextJob;
      const preferredSetId = updateProgress(nextSets, context);
      applySets(nextSets, preferredSetId);
    }
    if (job.status === "error") throw new Error(job.error || "封面任务失败");
  };

  const reconcileSets = async () => {
    try {
      applySets(await api.covers(id));
    } catch {
      // Preserve the operation error; the next page refresh can retry reconciliation.
    }
  };

  const reconcileDirections = async () => {
    try {
      setDirections(await api.coverDirections(id));
    } catch {
      // Preserve the operation error; the next page refresh can retry reconciliation.
    }
  };

  const generate = async () => {
    if (!settings || !generationAllowed) return;
    setBusy("generate");
    setProgress({
      mode: "generate", completed: 0, total: settings.count, phase: "preparing",
    });
    setError("");
    try {
      await waitForJob(
          await (approvedDirection
            ? api.generateCovers(id, settings.count, {
                direction_id: approvedDirection.direction_id,
                approved_direction_sha256: approvedDirection.direction_sha256,
              })
            : api.generateCovers(id, settings.count)),
        { mode: "generate", total: settings.count, knownSetIds: new Set(sets.map((item) => item.cover_set_id)) },
      );
      applySets(await api.covers(id));
      toast("封面候选图已生成", "success");
    } catch (cause) {
      await Promise.all([reconcileSets(), reconcileDirections()]);
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    } finally {
      setBusy("");
      setProgress(null);
    }
  };

  const approveDirection = async (direction: CoverDirection) => {
    setBusy(`direction:${direction.direction_id}`);
    try {
      const approved = await api.approveCoverDirection(
        id, direction.direction_id, direction.brief_sha256, direction.direction_sha256,
      );
      setDirections((items) => items.map((item) => (
        item.direction_id === approved.direction_id ? approved : item
      )));
      toast("美术方向已批准", "success");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    } finally {
      setBusy("");
    }
  };

  const createDirection = async () => {
    if (!settings) return;
    setBusy("direction:create");
    setError("");
    try {
      const created = await api.createCoverDirection(id, settings.count);
      setDirections((items) => [
        created,
        ...items.filter((item) => item.direction_id !== created.direction_id),
      ]);
      toast("美术方向已生成，等待审核", "success");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      toast(message, "error");
    } finally {
      setBusy("");
    }
  };

  const retry = async (candidate: CoverCandidate, repairCodes: string[] = []) => {
    if (!current) return;
    const candidateNumber = current.candidates.findIndex((item) => item.candidate_id === candidate.candidate_id) + 1;
    setBusy(candidate.candidate_id);
    setProgress({
      mode: "retry", completed: 0, total: 1, phase: "preparing",
      candidateNumber,
    });
    try {
      await waitForJob(
        await (repairCodes.length
          ? api.retryCover(
              id, current.cover_set_id, candidate.candidate_id, current.revision, repairCodes,
            )
          : api.retryCover(id, current.cover_set_id, candidate.candidate_id, current.revision)),
        { mode: "retry", candidateNumber },
      );
      applySets(await api.covers(id));
      toast("候选图已重新生成", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
      setProgress(null);
    }
  };

  const select = async (candidate: CoverCandidate) => {
    if (!current) return;
    const stale = current.status === "stale";
    const accepted = await confirm({
      title: "使用这张封面？",
      message: stale
        ? "这张候选图基于旧版故事设计生成，仍将其设为交付封面吗？"
        : "将这张候选图设为交付封面，并重新生成交付包吗？",
      confirmLabel: "使用此封面",
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.selectCover(
        id, current.cover_set_id, candidate.candidate_id,
        current.revision, current.active_revision, stale,
      );
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast("已选择交付封面", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const reject = async (candidate: CoverCandidate) => {
    if (!current) return;
    const accepted = await confirm({
      title: "拒绝这张候选图？",
      message: "保留其他候选图，并将此方案标记为已拒绝吗？",
      confirmLabel: "拒绝候选图",
      danger: true,
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.rejectCover(
        id, current.cover_set_id, candidate.candidate_id, current.revision,
      );
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast("候选图已拒绝", "success");
    } catch (cause) {
      await reconcileSets();
      toast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  if (loading) return <LoadingWorkspace />;

  return (
    <Scene quiet>
      <div className="workspace-page">
        <Link
          to={`/projects/${id}`}
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-muted transition-colors hover:text-ink-text"
        >
          <Icon name="arrow-left" className="h-3.5 w-3.5" /> 返回作品
        </Link>

        <header className="mt-6 flex flex-wrap items-end justify-between gap-5 border-b border-[rgba(74,91,133,0.14)] pb-6">
          <div className="min-w-0">
            <p className="eyebrow">{project?.title || id}</p>
            <h1 className="font-display text-[30px] font-semibold leading-tight text-ink-text sm:text-[34px]">
              封面工作室
            </h1>
            <p className="mt-2 text-[13px] text-ink-muted">
              {settings?.model || "gpt-image-2"} · {settings?.size || "2048x3072"} · {qualityLabel(settings?.quality || "high")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {current && (
              <a
                href={api.deliveryPackageUrl(id)}
                download="book-package.zip"
                className="btn-secondary"
                aria-label="下载交付包"
              >
                <Icon name="download" className="h-4 w-4" /> 交付包
              </a>
            )}
            {directionsLoaded && !directions[0] && (
              <button
                type="button"
                className="btn-secondary"
                disabled={Boolean(busy)}
                onClick={createDirection}
                aria-label="创建美术方向"
              >
                <Icon name="sparkles" className="h-4 w-4" />
                {busy === "direction:create" ? "规划中" : "创建方向"}
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              disabled={!generationAllowed || Boolean(busy)}
              onClick={generate}
              aria-label={`生成 ${settings?.count || 4} 张封面`}
            >
              <Icon name="sparkles" className="h-4 w-4" />
              {busy === "generate" ? "生成中" : `生成 ${settings?.count || 4} 张封面`}
            </button>
          </div>
        </header>

        {error && (
          <div
            role="alert"
            className="mt-6 rounded-[8px] border border-[#df93a7] bg-[#fff1f4] px-4 py-3 text-[13px] text-[#96354e]"
          >
            {error}
          </div>
        )}

        {!settings?.configured && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-[8px] border border-[#d6a85f] bg-[#fff7e8] px-4 py-3">
            <p className="text-[13px] text-[#72511d]">{settings?.error || "封面模型配置尚未完成。"}</p>
            <Link to="/settings" className="btn-secondary" aria-label="配置封面模型">
              <Icon name="sparkles" className="h-4 w-4" /> 配置封面模型
            </Link>
          </div>
        )}

        {progress && <GenerationProgress progress={progress} />}

        {directions.length > 0 && (
          <DirectionWorkspace
            directions={directions}
            busy={busy}
            onApprove={approveDirection}
            onCreate={createDirection}
          />
        )}

        <div className="mt-7 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-[18px] font-semibold text-ink-text">
              {current ? `已生成 ${readyCount(current)}/${current.requested_count} 张` : "尚无封面方案"}
            </h2>
            <p className="mt-1 text-[12px] text-ink-muted">
              {current ? statusLabel(current.status) : `${settings?.count || 4} 个候选图位置`}
            </p>
          </div>
          {sets.length > 1 && (
            <label className="flex items-center gap-2 text-[12px] text-ink-muted">
              版本
              <select
                value={current?.cover_set_id || ""}
                onChange={(event) => setSelectedSetId(event.target.value)}
                className="h-9 rounded-[8px] border border-paper-line bg-white px-3 text-[12px] text-ink-text"
              >
                {sets.map((item, index) => (
                  <option key={item.cover_set_id} value={item.cover_set_id}>
                    {index === 0 ? "最新" : `版本 ${sets.length - index}`} · {statusLabel(item.status)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {(current?.candidates || emptyCandidates(settings?.count || 4)).map((candidate, index) => (
            <CandidateCard
              key={candidate.candidate_id}
              candidate={candidate}
              concept={current?.concepts.find((item) => item.concept_id === candidate.concept_id)}
              index={index}
              busy={busy === candidate.candidate_id}
              disabled={Boolean(busy)}
              generating={Boolean(
                progress && (
                  (progress.mode === "generate" && candidate.status === "pending")
                  || (progress.mode === "retry" && progress.candidateNumber === index + 1)
                )
              )}
              onRetry={(repairCodes) => retry(candidate, repairCodes)}
              onSelect={() => select(candidate)}
              onReject={() => reject(candidate)}
            />
          ))}
        </div>
      </div>
    </Scene>
  );
}

function DirectionWorkspace({
  directions, busy, onApprove, onCreate,
}: {
  directions: CoverDirection[];
  busy: string;
  onApprove: (direction: CoverDirection) => void;
  onCreate: () => void;
}) {
  const direction = directions[0];
  const characters = direction.brief?.principal_characters || [];
  const environment = direction.brief?.lived_environment || {};
  const identity = direction.visual_identity;
  const evidenceLedger = direction.evidence_ledger;
  const conflictContract = direction.core_conflict_visual_contract;
  const assumptions = [
    ...(Array.isArray(direction.brief?.visual_assumptions)
      ? direction.brief.visual_assumptions
      : []),
    ...(direction.visual_assumptions || []),
  ];
  return (
    <section
      aria-label="故事事实与美术方向"
      className="mt-7 border-y border-[rgba(74,91,133,0.14)] py-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">故事事实</p>
          <h2 className="mt-1 font-display text-[18px] font-semibold text-ink-text">
            美术方向审核
          </h2>
          <p className="mt-1 text-[12px] text-ink-muted">
            {direction.brief?.genre || "基于故事的专属方向"} · {direction.brief?.target_audience || "目标受众待确认"}
          </p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
          direction.status === "approved"
            ? "border-[#8dc5a6] bg-[#eaf8f0] text-[#26714a]"
            : direction.status === "stale"
              ? "border-[#df93a7] bg-[#fff1f4] text-[#96354e]"
              : "border-[#d6a85f] bg-[#fff7e8] text-[#72511d]"
        }`}>
          {directionStatusLabel(direction.status)}
        </span>
      </div>

      {characters.length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {characters.map((character) => (
            <div key={String(character.character_id)} className="rounded-[6px] border border-paper-line bg-white/65 p-3">
              <p className="text-[12px] font-semibold text-ink-text">{String(character.name || character.character_id)}</p>
              <p className="mt-1 text-[11px] leading-4 text-ink-muted">
                {character.age ? `年龄 ${character.age}` : character.age_band ? `年龄阶段：${character.age_band}` : "年龄待补充"}
                {character.occupation_and_status ? ` · ${character.occupation_and_status}` : ""}
              </p>
              <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-ink-muted">
                {character.lived_environment || character.daily_wardrobe || "生活环境待补充"}
              </p>
            </div>
          ))}
        </div>
      )}

      {environment && Object.keys(environment).length > 0 && (
        <p className="mt-3 text-[11.5px] text-ink-muted">
          主要生活空间：{Array.isArray(environment.primary_spaces) ? environment.primary_spaces.join("、") : String(environment.primary_spaces || "待补充")}
        </p>
      )}

      {identity && (
        <div className="mt-4 rounded-[8px] border border-[#c8d2ef] bg-[#f4f7ff] p-4" aria-label="本书视觉语言">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-3xl">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#526aa5]">本书视觉语言</p>
              <p className="mt-1.5 text-[13px] font-semibold leading-5 text-ink-text">{identity.design_thesis}</p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                情绪矛盾：{identity.dominant_emotional_contradiction}
              </p>
            </div>
            {evidenceLedger && (
              <span className="rounded-full border border-[#b8c6e8] bg-white px-2.5 py-1 text-[10.5px] font-semibold text-[#526aa5]">
                {evidenceLedger.items.length} 条故事证据 · {new Set(evidenceLedger.items.map((item) => item.source_type)).size} 类来源
              </span>
            )}
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <p className="text-[10.5px] font-semibold text-ink-text">小说专属视觉锚点</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {identity.uniqueness_anchors.map((anchor) => (
                  <span key={anchor} className="rounded-full bg-white px-2 py-1 text-[10.5px] text-ink-muted">{anchor}</span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[10.5px] font-semibold text-ink-text">可用视觉语法</p>
              <p className="mt-1.5 text-[11px] leading-4 text-ink-muted">{identity.visual_grammar.join(" · ")}</p>
              <p className="mt-1 text-[11px] leading-4 text-ink-muted">字体：{identity.typography_voice}</p>
            </div>
          </div>
          <details className="mt-3 text-[11px] text-ink-muted">
            <summary className="cursor-pointer font-semibold text-[#526aa5]">查看视觉证据与设计边界</summary>
            <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
              {(evidenceLedger?.items || []).slice(0, 8).map((item) => (
                <div key={item.evidence_id} className="rounded-[5px] border border-[#d8e0f4] bg-white/80 p-2.5">
                  <p className="font-semibold text-ink-text">{formatStrategy(item.source_type)} · {item.spoiler_level}</p>
                  <p className="mt-1 line-clamp-3 leading-4">{item.summary}</p>
                </div>
              ))}
            </div>
            <p className="mt-2">人物策略：{identity.cast_policy}</p>
            <p className="mt-1">避免套路：{identity.cliche_blacklist.join("、") || "无额外限制"}</p>
          </details>
        </div>
      )}

      {conflictContract && (
        <div className="mt-4 rounded-[8px] border border-[#dfb3bd] bg-[#fff6f7] p-4" aria-label="核心冲突视觉契约">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-3xl">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#96354e]">核心冲突视觉契约</p>
              <p className="mt-1.5 text-[13px] font-semibold leading-5 text-ink-text">{conflictContract.pressure_source}</p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                可见原因：{conflictContract.visible_cause}
              </p>
              <p className="mt-1 text-[11.5px] leading-5 text-ink-muted">
                主角后果：{conflictContract.decisive_consequence}
              </p>
            </div>
            <span className="rounded-full border border-[#dfb3bd] bg-white px-2.5 py-1 text-[10.5px] font-semibold text-[#96354e]">
              {formatStrategy(conflictContract.conflict_kind)}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {conflictContract.required_visual_signals.map((signal) => (
              <span key={signal} className="rounded-full bg-white px-2 py-1 text-[10.5px] text-ink-muted">{signal}</span>
            ))}
          </div>
          <p className="mt-2 text-[10.5px] leading-4 text-ink-muted">
            关系压力人物：{conflictContract.pressure_character_ids.join("、") || "非人物压力"} · 边界：{conflictContract.spoiler_boundary}
          </p>
        </div>
      )}

      {assumptions.length > 0 && (
        <div className="mt-3 border-l-2 border-[#d6a85f] bg-[#fff7e8] px-3 py-2.5 text-[11.5px] text-[#72511d]">
          <p className="font-semibold">
            本次审核包含 {assumptions.filter((item) => item.status === "pending_confirmation").length} 项待确认的视觉假设
          </p>
          <ul className="mt-1.5 space-y-1">
            {assumptions.map((item, index) => (
              <li key={`${String(item.field)}:${index}`}>
                <span className="font-medium">{String(item.field)}</span>: {String(item.proposed_value)}
                {item.reason ? <span className="text-[#86642f]"> · {String(item.reason)}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-3 lg:grid-cols-2">
        {direction.plans.map((plan, index) => (
          <article key={plan.concept_id} className="rounded-[6px] border border-paper-line bg-white/65 p-3">
            <div className="flex items-start justify-between gap-3">
              <p className="text-[12px] font-semibold text-ink-text">
                方向 {index + 1}：{formatStrategy(plan.portfolio_slot || plan.visual_strategy)}
              </p>
              <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-ink-muted">{plan.visual_hook.hook_type.replaceAll("_", " ")}</span>
            </div>
            {plan.portfolio_slot && (
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-[5px] bg-[#f5f6fb] px-2.5 py-2 text-[10.5px] leading-4 text-ink-muted sm:grid-cols-3">
                <div><dt className="font-semibold text-ink-text">构图</dt><dd>{formatStrategy(plan.composition_family)}</dd></div>
                {plan.focal_strategy && <div><dt className="font-semibold text-ink-text">主体策略</dt><dd>{formatStrategy(plan.focal_strategy)}</dd></div>}
                <div><dt className="font-semibold text-ink-text">场景</dt><dd>{formatStrategy(plan.scene_family)}</dd></div>
                <div><dt className="font-semibold text-ink-text">地点</dt><dd>{plan.location_family}</dd></div>
                <div><dt className="font-semibold text-ink-text">美术风格</dt><dd>{formatStrategy(plan.art_style)}</dd></div>
                <div><dt className="font-semibold text-ink-text">情绪</dt><dd>{formatStrategy(plan.emotion_register)}</dd></div>
                <div><dt className="font-semibold text-ink-text">字体排版</dt><dd>{formatStrategy(plan.typography_style)}</dd></div>
              </dl>
            )}
            <p className="mt-2 text-[11.5px] leading-4 text-ink-muted">{plan.frozen_action}</p>
            {plan.conflict_read && (
              <div className="mt-2 rounded-[5px] border border-[#ead0d6] bg-[#fff8f9] px-2.5 py-2 text-[10.5px] leading-4 text-ink-muted">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`rounded-full px-2 py-0.5 font-semibold ${
                    plan.causal_visibility === "direct"
                      ? "bg-[#f7dce3] text-[#96354e]"
                      : "bg-[#eeeaf8] text-[#66518f]"
                  }`}>
                    {plan.causal_visibility === "direct" ? "直接冲突" : "间接证据"}
                  </span>
                  <span>{plan.conflict_delivery}</span>
                </div>
                <p className="mt-1 font-medium text-ink-text">缩略图故事：{plan.conflict_read}</p>
                <p className="mt-1">原因：{plan.cause_signal} · 后果：{plan.consequence_signal}</p>
              </div>
            )}
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">出场人物：{plan.cast.length ? plan.cast.join("、") : "无人物方案"} · 核心道具/意象：{plan.primary_prop}</p>
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">{plan.visual_hook.open_question}</p>
            {plan.design_rationale && (
              <div className="mt-2 border-l-2 border-[#9dafdc] pl-2.5 text-[10.5px] leading-4 text-ink-muted">
                <p><span className="font-semibold text-ink-text">设计理由：</span>{plan.design_rationale}</p>
                {plan.evidence_summary && <p className="mt-1"><span className="font-semibold text-ink-text">故事依据：</span>{plan.evidence_summary}</p>}
                {plan.typography_rationale && <p className="mt-1"><span className="font-semibold text-ink-text">字体表达：</span>{plan.typography_rationale}</p>}
                {plan.novelty_rationale && <p className="mt-1"><span className="font-semibold text-ink-text">差异说明：</span>{plan.novelty_rationale}</p>}
              </div>
            )}
          </article>
        ))}
      </div>

      {direction.status !== "approved" && direction.status !== "stale" && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={Boolean(busy)}
            onClick={() => onApprove(direction)}
            aria-label="批准美术方向"
          >
            <Icon name="circle-check" className="h-4 w-4" /> 批准美术方向
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="重新规划封面方向"
          >
            <Icon name="sparkles" className="h-4 w-4" /> 重新规划方向
          </button>
        </div>
      )}
      {direction.status === "approved" && (
        <button
          type="button"
          className="btn-secondary mt-4"
          disabled={Boolean(busy)}
          onClick={onCreate}
          aria-label="重新规划封面方向"
        >
          <Icon name="sparkles" className="h-4 w-4" /> 重新规划方向
        </button>
      )}
      {direction.status === "stale" && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] font-medium text-[#96354e]">此方向已过期，请根据当前故事事实创建新方向。</p>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="创建新的美术方向"
          >
            <Icon name="sparkles" className="h-4 w-4" /> 新方向
          </button>
        </div>
      )}
    </section>
  );
}

function GenerationProgress({ progress }: { progress: CoverProgress }) {
  const isRetry = progress.mode === "retry";
  const isPreparing = progress.phase === "preparing";
  const nextCover = Math.min(progress.completed + 1, progress.total);
  const title = isRetry
    ? `正在重新生成候选图 ${progress.candidateNumber}`
    : isPreparing
      ? "正在准备生成封面"
      : progress.completed >= progress.total
        ? "正在完成本组封面"
        : `正在生成第 ${nextCover}/${progress.total} 张封面`;
  const completedLabel = isRetry
    ? "正在渲染一张候选图"
    : `已完成 ${progress.completed}/${progress.total} 张`;
  const percentage = progress.total > 0
    ? Math.round((progress.completed / progress.total) * 100)
    : 0;

  return (
    <section
      role="status"
      aria-live="polite"
      aria-label="封面生成进度"
      className="mt-6 rounded-[8px] border border-[rgba(104,86,168,0.22)] bg-[#f8f6ff] px-4 py-3.5 text-ink-text shadow-[0_8px_22px_rgba(83,67,137,0.07)]"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#e8e2ff] text-[#6552a6]">
          <Icon name="sparkles" className="h-4 w-4 animate-pulse" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-[13px] font-semibold">{title}</p>
            <span className="nums text-[11px] font-medium text-ink-muted">{completedLabel}</span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={progress.total}
            aria-valuenow={progress.completed}
            aria-label={title}
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e5e0f3]"
          >
            <span
              className="block h-full rounded-full bg-[#7864c2] transition-[width] duration-500 ease-out"
              style={{ width: `${percentage}%` }}
            />
          </div>
          <p className="mt-2 text-[11.5px] text-ink-muted">
            {isPreparing ? "每张图片返回后，预览会随即更新。" : "预览正在显示最新结果。"}
          </p>
        </div>
      </div>
    </section>
  );
}

function CandidateCard({
  candidate, concept, index, busy, disabled, generating, onRetry, onSelect, onReject,
}: {
  candidate: CoverCandidate;
  concept?: CoverSet["concepts"][number];
  index: number;
  busy: boolean;
  disabled: boolean;
  generating: boolean;
  onRetry: (repairCodes?: string[]) => void;
  onSelect: () => void;
  onReject: () => void;
}) {
  const imageUrl = api.assetUrl(candidate.url);
  const label = `候选图 ${index + 1}`;
  const repairCodes = candidate.quality_report?.repair_codes || [];
  const [repairCode, setRepairCode] = useState(repairCodes[0] || "");
  return (
    <motion.article
      data-testid="cover-slot"
      aria-busy={generating || busy}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.28 }}
      className="min-w-0 overflow-hidden rounded-[8px] border border-[rgba(74,91,133,0.16)] bg-white shadow-[0_10px_28px_rgba(37,52,91,0.08)]"
    >
      <div className="relative aspect-[2/3] w-full overflow-hidden bg-[#e9edf4]">
        {imageUrl ? (
          <img src={imageUrl} alt={`封面${label}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-5 text-center text-ink-muted">
            <span className={`flex h-10 w-10 items-center justify-center rounded-full ${
              candidate.status === "failed" ? "bg-[#f8dce4] text-[#a6425d]" : "bg-white text-ink-muted"
            }`}>
              <Icon name={candidate.status === "failed" ? "circle-alert" : "image"} className="h-5 w-5" />
            </span>
            <span className="text-[12px] font-medium">
              {generating
                ? "正在生成图片…"
                : busy
                  ? "重新生成中"
                  : candidate.status === "failed"
                    ? "生成失败"
                    : `候选图 ${index + 1}`}
            </span>
          </div>
        )}
        <span className={`absolute left-2 top-2 rounded-full border px-2 py-1 text-[10px] font-semibold capitalize backdrop-blur-md ${statusClass(candidate.status)}`}>
          {generating ? "生成中" : candidateStatusLabel(candidate.status)}
        </span>
        {imageUrl && (
          <a
            href={imageUrl}
            target="_blank"
            rel="noreferrer"
            title={`查看${label}原始尺寸`}
            aria-label={`查看${label}原始尺寸`}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full border border-white/50 bg-[#17213f]/75 text-white backdrop-blur-md transition-colors hover:bg-[#17213f]"
          >
            <Icon name="eye" className="h-4 w-4" />
          </a>
        )}
      </div>
      <div className="min-h-[142px] p-3.5">
        <p className="truncate text-[12px] font-semibold text-ink-text">
          {formatStrategy(concept?.visual_strategy) || `方向 ${index + 1}`}
        </p>
        <p className="mt-1 line-clamp-2 min-h-[34px] text-[11.5px] leading-[17px] text-ink-muted">
          {candidate.error || concept?.focal_scene || "等待生成"}
        </p>
        {candidate.quality_report && (
          <p className={`mt-2 text-[10.5px] font-semibold ${
            candidate.quality_report.status === "blocked"
              ? "text-[#a6425d]"
              : candidate.quality_report.status === "recommended_for_human_review"
                ? "text-[#26714a]"
                : "text-[#72511d]"
          }`}>
            {qualityStatusLabel(candidate.quality_report.status)}
          </p>
        )}
        <div className="mt-3 flex min-h-9 flex-wrap items-center gap-1.5">
          {candidate.status === "failed" && (
            <button type="button" className="btn-secondary px-3 py-2 text-[11.5px]" onClick={() => onRetry()} disabled={disabled} aria-label={`重试${label}`}>
              <Icon name="history" className="h-3.5 w-3.5" /> 重试
            </button>
          )}
          {candidate.status === "ready" && (
            <>
              <button type="button" className="btn-primary px-3 py-2 text-[11.5px]" onClick={onSelect} disabled={disabled} aria-label={`选择${label}`}>
                <Icon name="circle-check" className="h-3.5 w-3.5" /> 选择
              </button>
              <button type="button" className="btn-ghost px-2.5 py-2 text-[11.5px]" onClick={onReject} disabled={disabled} aria-label={`拒绝${label}`}>
                拒绝
              </button>
            </>
          )}
          {candidate.status === "selected" && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-[#26714a]">
              <Icon name="circle-check" className="h-3.5 w-3.5" /> 交付封面
            </span>
          )}
        </div>
        {candidate.status === "ready" && repairCodes.length > 0 && (
          <div className="mt-2 flex items-center gap-1.5 border-t border-paper-line pt-2">
            <select
              value={repairCode}
              onChange={(event) => setRepairCode(event.target.value)}
              aria-label={`${label}的修复原因`}
              className="min-w-0 flex-1 rounded-[6px] border border-paper-line bg-white px-2 py-1.5 text-[10.5px] text-ink-text"
            >
              {repairCodes.map((code) => (
                <option key={code} value={code}>{formatStrategy(code)}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary px-2.5 py-1.5 text-[10.5px]"
              onClick={() => onRetry([repairCode])}
              disabled={disabled || !repairCode}
              aria-label={`按修复建议重新生成${label}`}
            >
              <Icon name="history" className="h-3.5 w-3.5" /> 修复
            </button>
          </div>
        )}
      </div>
    </motion.article>
  );
}

function LoadingWorkspace() {
  return (
    <Scene quiet>
      <div className="workspace-page">
        <p className="text-[13px] text-ink-muted">正在加载封面工作区</p>
        <div className="mt-7 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {emptyCandidates(4).map((candidate) => (
            <div key={candidate.candidate_id} data-testid="cover-slot" className="aspect-[2/3] animate-pulse rounded-[8px] border border-paper-line bg-white/55" />
          ))}
        </div>
      </div>
    </Scene>
  );
}

function emptyCandidates(count: number): CoverCandidate[] {
  return Array.from({ length: count }, (_, index) => ({
    candidate_id: `empty-${index + 1}`, concept_id: `empty-concept-${index + 1}`,
    status: "pending", url: null, relative_path: "", media_id: "", sha256: "",
    width: 0, height: 0, content_type: "", error: "",
  }));
}

function readyCount(coverSet: CoverSet) {
  return coverSet.candidates.filter((item) => ["ready", "selected", "rejected"].includes(item.status)).length;
}

function statusLabel(status: CoverSet["status"]) {
  const labels: Record<CoverSet["status"], string> = {
    generating: "生成中",
    partial: "部分完成",
    ready: "已就绪",
    selected: "已选择",
    failed: "生成失败",
    stale: "已过期",
  };
  return labels[status] || status;
}

function directionStatusLabel(status: CoverDirection["status"]) {
  const labels: Record<CoverDirection["status"], string> = {
    awaiting_approval: "等待批准",
    approved: "已批准",
    stale: "已过期",
    rejected: "已拒绝",
  };
  return labels[status] || status;
}

function candidateStatusLabel(status: CoverCandidate["status"]) {
  const labels: Record<CoverCandidate["status"], string> = {
    pending: "等待生成",
    ready: "已就绪",
    selected: "已选择",
    rejected: "已拒绝",
    failed: "生成失败",
  };
  return labels[status] || status;
}

function qualityLabel(quality: string) {
  return ({ low: "低质量", medium: "中等质量", high: "高质量", auto: "自动" } as Record<string, string>)[quality] || quality;
}

function formatStrategy(value?: string) {
  return value?.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()) || "";
}

function statusClass(status: CoverCandidate["status"]) {
  if (status === "selected") return "border-[#8dc5a6] bg-[#eaf8f0]/90 text-[#26714a]";
  if (status === "failed") return "border-[#e3a0b1] bg-[#fff0f3]/90 text-[#a6425d]";
  if (status === "ready") return "border-white/60 bg-white/85 text-ink-text";
  if (status === "rejected") return "border-[#c7cbd5] bg-[#f1f2f5]/90 text-[#6e7482]";
  return "border-white/60 bg-white/80 text-ink-muted";
}

function qualityStatusLabel(status: NonNullable<CoverCandidate["quality_report"]>["status"]) {
  if (status === "recommended_for_human_review") return "建议人工审核";
  if (status === "human_review_required") return "需要人工审核";
  return "发现质量阻塞问题";
}
