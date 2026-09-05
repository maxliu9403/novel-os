import { useRef } from "react";
import { Link } from "react-router-dom";
import type { ProjectSummary } from "../api/client";
import StatusPill from "./StatusPill";
import Icon from "./Icon";

const TILES = ["violet", "blue", "cyan", "amber", "rose", "indigo"] as const;
const TILE_CLASS: Record<(typeof TILES)[number], string> = {
  violet: "text-[#625edb] bg-[#eeedff]",
  blue: "text-[#3974db] bg-[#e8f1ff]",
  cyan: "text-[#1591ab] bg-[#e3f8fa]",
  amber: "text-[#c47a1b] bg-[#fff2dc]",
  rose: "text-[#c85177] bg-[#ffeaf1]",
  indigo: "text-[#4f69cf] bg-[#e9edff]",
};

function tileFor(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h + id.charCodeAt(i) * (i + 1)) % TILES.length;
  return TILES[h];
}

function when(iso: string | null | undefined) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  } catch {
    return null;
  }
}

export default function ProjectCard({
  p,
  onDelete,
}: {
  p: ProjectSummary;
  onDelete?: (project: ProjectSummary) => void;
}) {
  const tile = tileFor(p.id);
  const menuRef = useRef<HTMLDetailsElement>(null);
  const progress = (p.target_word_count && p.target_word_count > 0)
    ? Math.round(((p.word_count ?? 0) / p.target_word_count) * 100)
    : p.chapter_count
      ? Math.round(((p.drafted_count ?? 0) / p.chapter_count) * 100)
      : 0;
  const touched = when(p.updated_at);

  return (
    <article className="glass-card group relative min-h-[168px] text-left">
      <Link
        to={`/projects/${p.id}`}
        className="flex min-h-[168px] flex-col p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <span
            className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-[18px] text-[22px] font-bold ${TILE_CLASS[tile]}`}
          >
            {p.title.charAt(0).toUpperCase()}
          </span>
          <div className="mr-8 flex flex-col items-end gap-1.5">
            <StatusPill status={p.status} />
            {p.content_rating === "mature" && (
              <span className="rounded-full border border-[rgba(200,81,119,0.25)] bg-[#ffeaf1] px-2 py-0.5 text-[10px] font-medium text-[#c85177]">
                成人内容
              </span>
            )}
          </div>
        </div>

        <div className="mt-4 min-w-0 flex-1">
          <span className="mb-1.5 block line-clamp-2 text-[17px] font-semibold leading-snug tracking-[-0.02em] text-ink-text">
            {p.title}
          </span>
          <span className="block truncate text-[13px] text-ink-muted">
            {p.genre || "未设置类型"}
            {p.author ? ` · ${p.author}` : ""}
          </span>
        </div>

        <div className="mt-4">
          <div className="mb-2 h-1 overflow-hidden rounded-full bg-[rgba(74,91,133,0.1)]">
            <div
              className="h-full rounded-full bg-[var(--color-violet)] transition-all"
              style={{ width: `${Math.min(100, progress)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[12px] text-ink-muted">
            <span className="nums">
              {(p.word_count ?? 0).toLocaleString("zh-CN")} 字 · 已写 {p.drafted_count ?? 0}/{p.chapter_count} 章
            </span>
            <span className="flex items-center gap-1.5">
              {touched && <span>{touched}</span>}
              <Icon
                name="chevron-right"
                className="h-3.5 w-3.5 text-paper-muted transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-[var(--color-violet)]"
              />
            </span>
          </div>
        </div>
      </Link>
      {onDelete && (
        <details ref={menuRef} className="absolute right-3.5 top-3.5 z-20">
          <summary
            aria-label={`管理作品《${p.title}》`}
            title="更多操作"
            className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-full text-[15px] font-bold tracking-[0.08em] text-ink-muted transition-colors hover:bg-white hover:text-ink-text [&::-webkit-details-marker]:hidden"
          >
            ···
          </summary>
          <div className="absolute right-0 top-10 w-36 rounded-2xl border border-[rgba(74,91,133,0.14)] bg-white/95 p-1.5 shadow-[0_16px_38px_rgba(23,33,63,0.18)] backdrop-blur-xl">
            <button
              type="button"
              className="w-full rounded-xl px-3 py-2 text-left text-[12.5px] font-medium text-[#b84363] transition-colors hover:bg-[#fff0f4]"
              onClick={() => {
                menuRef.current?.removeAttribute("open");
                onDelete(p);
              }}
            >
              永久删除作品
            </button>
          </div>
        </details>
      )}
    </article>
  );
}
