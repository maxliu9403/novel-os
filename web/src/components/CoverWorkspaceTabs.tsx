import { useRef } from "react";

export type CoverWorkspaceTab = "gallery" | "directions" | "analysis" | "costs";
const tabs: Array<{id: CoverWorkspaceTab; label: string}> = [
  {id: "gallery", label: "候选封面"},
  {id: "directions", label: "美术方向"},
  {id: "analysis", label: "故事分析"},
  {id: "costs", label: "费用"},
];

export default function CoverWorkspaceTabs({selected, onSelect, indicators}: {
  selected: CoverWorkspaceTab;
  onSelect: (tab: CoverWorkspaceTab) => void;
  indicators: Partial<Record<CoverWorkspaceTab, string>>;
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  return <div className="cover-workspace-tabs" role="tablist" aria-label="封面工作室子页">
    {tabs.map((tab, index) => <button key={tab.id} type="button" role="tab"
      ref={element => { buttons.current[index] = element; }}
      id={`cover-tab-${tab.id}`} aria-controls={`cover-panel-${tab.id}`}
      aria-selected={selected === tab.id} tabIndex={selected === tab.id ? 0 : -1}
      onClick={() => onSelect(tab.id)}
      onKeyDown={event => {
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
          : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
          : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        onSelect(tabs[next].id);
        buttons.current[next]?.focus();
      }}>
      {tab.label}{indicators[tab.id] && <span className="cover-workspace-tabs__indicator">{indicators[tab.id]}</span>}
    </button>)}
  </div>;
}
