import { lazy, Suspense } from "react";
import { useParams, useSearchParams } from "react-router-dom";

const LegacyCoverStudio = lazy(() => import("./CoverStudio"));
const PosterStudio = lazy(() => import("./PosterStudio"));

/** Keep old approvals/history available without making them a new-flow prerequisite. */
export default function CoverStudioRoute() {
  const { id = "", sessionId } = useParams();
  const [search, setSearch] = useSearchParams();
  // Old analysis and direction links identify saved V11 artifacts, not a new
  // poster plan. Keep their actual history visible until the user switches.
  const oldTab = search.get("tab");
  const legacy = !!sessionId || search.get("legacy") === "1"
    || oldTab === "analysis" || oldTab === "directions";
  const switchMode = (old: boolean) => setSearch(previous => {
    const next = new URLSearchParams(previous);
    if (old) next.set("legacy", "1");
    else {
      next.delete("legacy");
      const tab = next.get("tab");
      next.delete("tab");
      if (tab === "directions") next.set("poster_tab", "plan");
      if (tab === "gallery") next.set("poster_tab", "gallery");
      if (tab === "analysis" || tab === "costs") next.set("poster_drawer", tab === "analysis" ? "source" : "costs");
    }
    return next;
  });
  return <Suspense fallback={<p className="p-8 text-muted">正在打开封面工作室…</p>}>
    {legacy ? <>
      {!sessionId && <div className="px-8 pt-4"><button type="button" className="text-sm text-accent" onClick={() => switchMode(false)}>返回新版封面工作室 →</button></div>}
      <LegacyCoverStudio />
    </> : <PosterStudio key={id} projectId={id} onLegacy={() => switchMode(true)} />}
  </Suspense>;
}
