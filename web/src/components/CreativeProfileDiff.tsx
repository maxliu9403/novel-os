import type { CreativeProfile } from "../api/creativeTypes";

const labels = {
  name: "名称",
  aliases: "别名",
  summary: "简介",
  dimension: "创作维度",
  reader_promises: "读者期待",
  mechanisms: "故事机制",
  character_dynamics: "人物动力",
  voice_guidance: "文风",
  pitfalls: "避免的问题",
  diagnostics: "检查",
  combinations: "组合",
  applicability: "适用范围",
  publication_mapping: "发行候选",
};
function display(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((v) => typeof v === "string"))
    return value.join("\n") || "未设置";
  return JSON.stringify(value, null, 2);
}
/** Full changed fields remain inspectable; no hidden truncation or automatic merge. */
export default function CreativeProfileDiff({
  before,
  after,
  beforeLabel = "当前版本",
  afterLabel = "待采用版本",
}: {
  before: CreativeProfile;
  after: CreativeProfile;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const changes = (Object.keys(labels) as Array<keyof typeof labels>).filter(
    (k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]),
  );
  return (
    <details className="mt-3 rounded-xl border border-paper-line bg-white/70 p-3 text-[12px]">
      <summary className="cursor-pointer text-ink">
        对照具体变化 ·{" "}
        {changes.map((k) => labels[k]).join("、") || "创作内容相同"}
      </summary>
      <div className="mt-3 max-h-80 space-y-3 overflow-auto">
        {changes.map((k) => (
          <section key={k}>
            <strong>{labels[k]}</strong>
            <div className="mt-2 grid gap-2 lg:grid-cols-2">
              {[
                { label: beforeLabel, value: before[k] },
                { label: afterLabel, value: after[k] },
              ].map((item) => (
                <div
                  key={item.label}
                  className="min-w-0 rounded-lg bg-paper/70 p-2"
                >
                  <p className="mb-2 text-ink-muted">{item.label}</p>
                  <pre className="whitespace-pre-wrap break-all font-sans leading-relaxed">
                    {display(item.value)}
                  </pre>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </details>
  );
}
