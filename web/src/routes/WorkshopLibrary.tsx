import { useSearchParams } from "react-router-dom";
import NovelWorkshop, { type WorkshopSeed } from "../components/NovelWorkshop";
import WorkshopSessions from "../components/WorkshopSessions";
import Icon from "../components/Icon";
import Scene from "../components/Scene";
import "../styles/workshop-library.css";

const EMPTY_SEED: WorkshopSeed = { title: "", author: "", genres: [], premise: "" };

export default function WorkshopLibrary() {
  const [params, setParams] = useSearchParams();
  const sessionId = params.get("session") ?? undefined;
  const creating = params.get("new") === "1";
  function close() { setParams({}); }
  return <Scene quiet><div className="workspace-page workshop-library"><div className="glass-shell workshop-library-shell p-3 sm:p-4"><div className="glass-panel workshop-library-surface px-5 py-7 sm:px-8 sm:py-9 lg:px-10">
    <header className="workshop-library-header">
      <div><h1>头脑风暴</h1>
        <p className="workshop-library-intro">查看历史讨论和任务状态，随时恢复继续。</p>
      </div>
      <button type="button" className="btn-primary workshop-library-create" onClick={() => setParams({ new: "1" })}><Icon name="plus" className="h-4 w-4" /> 新建头脑风暴</button>
    </header>
    <div className="workshop-library-section"><h2>全部讨论</h2><span>保存的设定、骨架与推导记录</span></div>
    <WorkshopSessions studio busy={false} onSelect={(id) => setParams({ session: id })} />
    </div></div>
    {(sessionId || creating) && <NovelWorkshop key={sessionId ?? "new"} open seed={EMPTY_SEED} initialSessionId={sessionId} onClose={close} onBack={close}
      onSessionChange={(id) => setParams({ session: id }, { replace: true })} onCreateNew={() => setParams({ new: "1" })} />}
  </div></Scene>;
}
