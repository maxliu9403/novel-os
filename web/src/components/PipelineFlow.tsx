import type { ChapterStages, StageProvenance } from "../api/client";
import Icon, { type IconName } from "./Icon";
import { agentLabel as localizeAgent } from "../lib/displayLabels";

export type StageKey = "outline" | "draft" | "revised" | "final";

const STAGES: { key: StageKey; label: string; agent: string; icon: IconName }[] = [
  { key: "outline", label: "大纲", agent: "架构师", icon: "compass" },
  { key: "draft", label: "初稿", agent: "执笔者", icon: "pen-line" },
  { key: "revised", label: "修订稿", agent: "编辑", icon: "scissors" },
  { key: "final", label: "定稿", agent: "你", icon: "scroll-text" },
];

function agentLabel(fallback: string, prov?: StageProvenance) {
  const raw = (prov?.produced_by_agent || "").trim();
  if (!raw) return fallback;
  if (raw === "author") return "你";
  return localizeAgent(raw, raw);
}

/** Pipeline ribbon with P3.2 provenance (agent + model). */
export default function PipelineFlow({
  stages,
  selected,
  onSelect,
}: {
  stages: ChapterStages;
  selected: StageKey;
  onSelect: (s: StageKey) => void;
}) {
  const provenance = stages.provenance ?? {};

  return (
    <div className="flex items-stretch gap-1.5 rounded-[22px] border border-[rgba(96,112,153,0.14)] bg-white/50 p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] backdrop-blur-md">
      {STAGES.map((s) => {
        const present = stages[s.key] != null;
        const isSel = selected === s.key;
        const prov = provenance[s.key];
        const who = present ? agentLabel(s.agent, prov) : "尚未运行";
        const model = (prov?.produced_by_model || "").trim();
        const needsReview =
          present
          && (s.key === "draft" || s.key === "revised")
          && !(prov?.reviewed_by || "").trim();
        const sub = needsReview
          ? `${who} · 待审核`
          : model && present
            ? `${who} · ${model}`
            : who;
        return (
          <button
            key={s.key}
            type="button"
            onClick={() => onSelect(s.key)}
            title={
              present
                ? [
                    needsReview ? "待审核" : null,
                    prov?.updated_at ? `更新于 ${prov.updated_at}` : null,
                    prov?.reviewed_by ? `审核人：${localizeAgent(prov.reviewed_by, prov.reviewed_by)}` : null,
                    prov?.word_count ? `${prov.word_count} 字` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || s.label
                : `尚未生成${s.label}`
            }
            className={`group flex flex-1 flex-col items-start rounded-2xl px-3.5 py-2.5 text-left transition-all duration-200 ${
              isSel
                ? "bg-[var(--color-violet)] text-white shadow-[0_10px_24px_rgba(104,103,234,0.28)]"
                : "text-ink-muted hover:bg-white/70 hover:text-ink"
            }`}
          >
            <span className="flex items-center gap-1.5">
              <Icon
                name={s.icon}
                className={`h-3.5 w-3.5 ${
                  present
                    ? isSel
                      ? "text-white"
                      : "text-[var(--color-violet)]"
                    : isSel
                      ? "text-white/40"
                      : "text-paper-muted"
                }`}
              />
              <span className={`text-[13px] font-semibold tracking-[-0.02em] ${
                isSel ? "text-white" : "text-ink-text"
              }`}>
                {s.label}
              </span>
              {needsReview && !isSel && (
                <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-violet)]" aria-hidden />
              )}
            </span>
            <span className={`mt-0.5 line-clamp-2 pl-5 text-[11px] font-medium tracking-[-0.01em] ${
              isSel ? "text-white/55" : "text-paper-muted"
            }`}>
              {sub}
            </span>
          </button>
        );
      })}
    </div>
  );
}
