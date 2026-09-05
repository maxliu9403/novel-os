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
    if (job.status === "error") throw new Error(job.error || "Cover job failed");
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
      toast("Cover candidates ready", "success");
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
      toast("Art direction approved", "success");
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
      toast("Art direction ready for review", "success");
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
      toast("Candidate regenerated", "success");
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
      title: "Use this cover?",
      message: stale
        ? "This candidate comes from an older story design. Use it as the delivery cover?"
        : "Use this candidate as the delivery cover and rebuild the package?",
      confirmLabel: "Use this cover",
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.selectCover(
        id, current.cover_set_id, candidate.candidate_id,
        current.revision, current.active_revision, stale,
      );
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast("Delivery cover selected", "success");
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
      title: "Reject this candidate?",
      message: "Keep the other candidates and mark this direction as rejected?",
      confirmLabel: "Reject candidate",
      danger: true,
    });
    if (!accepted) return;
    setBusy(candidate.candidate_id);
    try {
      const updated = await api.rejectCover(
        id, current.cover_set_id, candidate.candidate_id, current.revision,
      );
      applySets(sets.map((item) => item.cover_set_id === updated.cover_set_id ? updated : item));
      toast("Candidate rejected", "success");
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
      <div className="mx-auto min-h-full max-w-[1240px] px-4 py-8 sm:px-8 lg:px-10">
        <Link
          to={`/projects/${id}`}
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-muted transition-colors hover:text-ink-text"
        >
          <Icon name="arrow-left" className="h-3.5 w-3.5" /> Project
        </Link>

        <header className="mt-6 flex flex-wrap items-end justify-between gap-5 border-b border-[rgba(74,91,133,0.14)] pb-6">
          <div className="min-w-0">
            <p className="eyebrow">{project?.title || id}</p>
            <h1 className="font-display text-[30px] font-semibold leading-tight text-ink-text sm:text-[34px]">
              Cover Studio
            </h1>
            <p className="mt-2 text-[13px] text-ink-muted">
              {settings?.model || "gpt-image-2"} · {settings?.size || "2048x3072"} · {settings?.quality || "high"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {current && (
              <a
                href={api.deliveryPackageUrl(id)}
                download="book-package.zip"
                className="btn-secondary"
                aria-label="Download delivery package"
              >
                <Icon name="download" className="h-4 w-4" /> Package
              </a>
            )}
            {directionsLoaded && !directions[0] && (
              <button
                type="button"
                className="btn-secondary"
                disabled={Boolean(busy)}
                onClick={createDirection}
                aria-label="Create art direction"
              >
                <Icon name="sparkles" className="h-4 w-4" />
                {busy === "direction:create" ? "Planning" : "Create direction"}
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              disabled={!generationAllowed || Boolean(busy)}
              onClick={generate}
              aria-label={`Generate ${settings?.count || 4} covers`}
            >
              <Icon name="sparkles" className="h-4 w-4" />
              {busy === "generate" ? "Generating" : `Generate ${settings?.count || 4} covers`}
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
            <p className="text-[13px] text-[#72511d]">{settings?.error || "Cover model configuration is incomplete."}</p>
            <Link to="/settings" className="btn-secondary" aria-label="Configure cover model">
              <Icon name="sparkles" className="h-4 w-4" /> Configure cover model
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
              {current ? `${readyCount(current)} of ${current.requested_count} rendered` : "No cover set yet"}
            </h2>
            <p className="mt-1 text-[12px] text-ink-muted">
              {current ? statusLabel(current.status) : `${settings?.count || 4} candidate slots`}
            </p>
          </div>
          {sets.length > 1 && (
            <label className="flex items-center gap-2 text-[12px] text-ink-muted">
              Version
              <select
                value={current?.cover_set_id || ""}
                onChange={(event) => setSelectedSetId(event.target.value)}
                className="h-9 rounded-[8px] border border-paper-line bg-white px-3 text-[12px] text-ink-text"
              >
                {sets.map((item, index) => (
                  <option key={item.cover_set_id} value={item.cover_set_id}>
                    {index === 0 ? "Latest" : `Version ${sets.length - index}`} · {statusLabel(item.status)}
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
  const assumptions = [
    ...(Array.isArray(direction.brief?.visual_assumptions)
      ? direction.brief.visual_assumptions
      : []),
    ...(direction.visual_assumptions || []),
  ];
  return (
    <section
      aria-label="Story facts and art direction"
      className="mt-7 border-y border-[rgba(74,91,133,0.14)] py-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Story facts</p>
          <h2 className="mt-1 font-display text-[18px] font-semibold text-ink-text">
            Art direction review
          </h2>
          <p className="mt-1 text-[12px] text-ink-muted">
            {direction.brief?.genre || "Story-specific direction"} · {direction.brief?.target_audience || "Approved audience"}
          </p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
          direction.status === "approved"
            ? "border-[#8dc5a6] bg-[#eaf8f0] text-[#26714a]"
            : direction.status === "stale"
              ? "border-[#df93a7] bg-[#fff1f4] text-[#96354e]"
              : "border-[#d6a85f] bg-[#fff7e8] text-[#72511d]"
        }`}>
          {direction.status.replaceAll("_", " ")}
        </span>
      </div>

      {characters.length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {characters.map((character) => (
            <div key={String(character.character_id)} className="rounded-[6px] border border-paper-line bg-white/65 p-3">
              <p className="text-[12px] font-semibold text-ink-text">{String(character.name || character.character_id)}</p>
              <p className="mt-1 text-[11px] leading-4 text-ink-muted">
                {character.age ? `Age ${character.age}` : character.age_band ? `Age phase: ${character.age_band}` : "Age pending"}
                {character.occupation_and_status ? ` · ${character.occupation_and_status}` : ""}
              </p>
              <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-ink-muted">
                {character.lived_environment || character.daily_wardrobe || "Lived environment pending"}
              </p>
            </div>
          ))}
        </div>
      )}

      {environment && Object.keys(environment).length > 0 && (
        <p className="mt-3 text-[11.5px] text-ink-muted">
          Lived spaces: {Array.isArray(environment.primary_spaces) ? environment.primary_spaces.join(", ") : String(environment.primary_spaces || "pending")}
        </p>
      )}

      {assumptions.length > 0 && (
        <div className="mt-3 border-l-2 border-[#d6a85f] bg-[#fff7e8] px-3 py-2.5 text-[11.5px] text-[#72511d]">
          <p className="font-semibold">
            {assumptions.filter((item) => item.status === "pending_confirmation").length} visual assumptions included in this approval
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
                Direction {index + 1}: {formatStrategy(plan.portfolio_slot || plan.visual_strategy)}
              </p>
              <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-ink-muted">{plan.visual_hook.hook_type.replaceAll("_", " ")}</span>
            </div>
            {plan.portfolio_slot && (
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-[5px] bg-[#f5f6fb] px-2.5 py-2 text-[10.5px] leading-4 text-ink-muted sm:grid-cols-3">
                <div><dt className="font-semibold text-ink-text">Composition</dt><dd>{formatStrategy(plan.composition_family)}</dd></div>
                <div><dt className="font-semibold text-ink-text">Scene</dt><dd>{formatStrategy(plan.scene_family)}</dd></div>
                <div><dt className="font-semibold text-ink-text">Location</dt><dd>{plan.location_family}</dd></div>
                <div><dt className="font-semibold text-ink-text">Art style</dt><dd>{formatStrategy(plan.art_style)}</dd></div>
                <div><dt className="font-semibold text-ink-text">Emotion</dt><dd>{formatStrategy(plan.emotion_register)}</dd></div>
                <div><dt className="font-semibold text-ink-text">Typography</dt><dd>{formatStrategy(plan.typography_style)}</dd></div>
              </dl>
            )}
            <p className="mt-2 text-[11.5px] leading-4 text-ink-muted">{plan.frozen_action}</p>
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">Cast: {plan.cast.join(", ")} · Prop: {plan.primary_prop}</p>
            <p className="mt-1 text-[11px] leading-4 text-ink-muted">{plan.visual_hook.open_question}</p>
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
            aria-label="Approve art direction"
          >
            <Icon name="circle-check" className="h-4 w-4" /> Approve art direction
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="Replan cover directions"
          >
            <Icon name="sparkles" className="h-4 w-4" /> Replan directions
          </button>
        </div>
      )}
      {direction.status === "approved" && (
        <button
          type="button"
          className="btn-secondary mt-4"
          disabled={Boolean(busy)}
          onClick={onCreate}
          aria-label="Replan cover directions"
        >
          <Icon name="sparkles" className="h-4 w-4" /> Replan directions
        </button>
      )}
      {direction.status === "stale" && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] font-medium text-[#96354e]">This direction is stale. Create a new direction from the current story facts.</p>
          <button
            type="button"
            className="btn-secondary"
            disabled={Boolean(busy)}
            onClick={onCreate}
            aria-label="Create new art direction"
          >
            <Icon name="sparkles" className="h-4 w-4" /> New direction
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
    ? `Regenerating candidate ${progress.candidateNumber}`
    : isPreparing
      ? "Preparing cover generation"
      : progress.completed >= progress.total
        ? "Finishing cover set"
        : `Generating cover ${nextCover} of ${progress.total}`;
  const completedLabel = isRetry
    ? "One candidate is being rendered"
    : `${progress.completed} of ${progress.total} complete`;
  const percentage = progress.total > 0
    ? Math.round((progress.completed / progress.total) * 100)
    : 0;

  return (
    <section
      role="status"
      aria-live="polite"
      aria-label="Cover generation progress"
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
            {isPreparing ? "The preview will update as each image is returned." : "The preview is updating with the latest result."}
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
  const label = `candidate ${index + 1}`;
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
          <img src={imageUrl} alt={`Cover ${label}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-5 text-center text-ink-muted">
            <span className={`flex h-10 w-10 items-center justify-center rounded-full ${
              candidate.status === "failed" ? "bg-[#f8dce4] text-[#a6425d]" : "bg-white text-ink-muted"
            }`}>
              <Icon name={candidate.status === "failed" ? "circle-alert" : "image"} className="h-5 w-5" />
            </span>
            <span className="text-[12px] font-medium">
              {generating
                ? "Generating image..."
                : busy
                  ? "Regenerating"
                  : candidate.status === "failed"
                    ? "Generation failed"
                    : `Candidate ${index + 1}`}
            </span>
          </div>
        )}
        <span className={`absolute left-2 top-2 rounded-full border px-2 py-1 text-[10px] font-semibold capitalize backdrop-blur-md ${statusClass(candidate.status)}`}>
          {generating ? "generating" : candidate.status}
        </span>
        {imageUrl && (
          <a
            href={imageUrl}
            target="_blank"
            rel="noreferrer"
            title={`View full resolution ${label}`}
            aria-label={`View full resolution ${label}`}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full border border-white/50 bg-[#17213f]/75 text-white backdrop-blur-md transition-colors hover:bg-[#17213f]"
          >
            <Icon name="eye" className="h-4 w-4" />
          </a>
        )}
      </div>
      <div className="min-h-[142px] p-3.5">
        <p className="truncate text-[12px] font-semibold text-ink-text">
          {formatStrategy(concept?.visual_strategy) || `Direction ${index + 1}`}
        </p>
        <p className="mt-1 line-clamp-2 min-h-[34px] text-[11.5px] leading-[17px] text-ink-muted">
          {candidate.error || concept?.focal_scene || "Awaiting generation"}
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
            <button type="button" className="btn-secondary px-3 py-2 text-[11.5px]" onClick={() => onRetry()} disabled={disabled} aria-label={`Retry ${label}`}>
              <Icon name="history" className="h-3.5 w-3.5" /> Retry
            </button>
          )}
          {candidate.status === "ready" && (
            <>
              <button type="button" className="btn-primary px-3 py-2 text-[11.5px]" onClick={onSelect} disabled={disabled} aria-label={`Select ${label}`}>
                <Icon name="circle-check" className="h-3.5 w-3.5" /> Select
              </button>
              <button type="button" className="btn-ghost px-2.5 py-2 text-[11.5px]" onClick={onReject} disabled={disabled} aria-label={`Reject ${label}`}>
                Reject
              </button>
            </>
          )}
          {candidate.status === "selected" && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-[#26714a]">
              <Icon name="circle-check" className="h-3.5 w-3.5" /> Delivery cover
            </span>
          )}
        </div>
        {candidate.status === "ready" && repairCodes.length > 0 && (
          <div className="mt-2 flex items-center gap-1.5 border-t border-paper-line pt-2">
            <select
              value={repairCode}
              onChange={(event) => setRepairCode(event.target.value)}
              aria-label={`Repair reason ${label}`}
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
              aria-label={`Regenerate ${label} with repair`}
            >
              <Icon name="history" className="h-3.5 w-3.5" /> Repair
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
      <div className="mx-auto max-w-[1240px] px-4 py-8 sm:px-8 lg:px-10">
        <p className="text-[13px] text-ink-muted">Loading cover workspace</p>
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
  return status.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
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
  if (status === "recommended_for_human_review") return "Recommended for review";
  if (status === "human_review_required") return "Human review required";
  return "Quality blockers found";
}
