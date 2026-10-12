import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ProjectSummary, type ProjectCoverSummary } from "../api/client";
import StatusPill from "./StatusPill";
import Icon from "./Icon";
import { formatProjectTaskTime, projectTaskStatusLabel } from "../lib/projectTasks";
import { COVER_STATUS_LABELS } from "../lib/projectCovers";
import "../styles/library.css";
const COVER_ACTIONS: Record<ProjectCoverSummary["status"], string> = {
  not_started: "设计封面", processing: "查看进度", awaiting_review: "确认封面",
  needs_attention: "处理封面", selected: "查看封面",
};
const JACKET_TONES = ["sage", "clay", "slate", "sand"] as const;
function jacketFor(id: string) {
  return JACKET_TONES[Array.from(id).reduce((sum, char) => sum + char.charCodeAt(0), 0) % JACKET_TONES.length];
}

export default function ProjectCard({ p, onDelete, onResume }: {
  p: ProjectSummary;
  onDelete?: (project: ProjectSummary) => void;
  onResume?: (project: ProjectSummary) => Promise<void>;
}) {
  const menuRef = useRef<HTMLDetailsElement>(null);
  const resumeLock = useRef(false);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const started = formatProjectTaskTime(p.task_started_at);
  const coverStatus = p.cover?.status ?? "not_started";
  const imageUrl = api.assetUrl(p.cover?.image_url);
  const showImage = imageUrl && imageUrl !== failedImage;

  async function resume() {
    if (!onResume || !p.task_id || !p.task_can_resume || resumeLock.current) return;
    resumeLock.current = true;
    setResuming(true);
    setResumeError(null);
    try { await onResume(p); }
    catch (e) { setResumeError(e instanceof Error ? e.message : String(e)); }
    finally { resumeLock.current = false; setResuming(false); }
  }

  return (
    <article className="glass-card library-book" aria-label={p.title}>
      <div className={`library-book__jacket library-book__jacket--${jacketFor(p.id)}`}>
        {showImage ? <img src={imageUrl} alt={`${p.title}的${p.cover?.image_kind === "selected" ? "选定封面" : "候选封面"}`}
          loading="lazy" onError={() => setFailedImage(imageUrl)} /> : (
          <div className="library-book__typeset" aria-hidden="true">
            <span>NOVEL OS<br />MANUSCRIPT</span>
            <b><span>{p.title.charAt(0)}</span><span>{p.title.slice(1)}</span></b>
            <i>{p.genre.split(" · ")[0] || "原创小说"}</i>
            <span>WORK IN PROGRESS</span>
          </div>
        )}
        <span className="library-book__jacket-caption">{showImage
          ? p.cover?.image_kind === "selected" ? "选定封面" : "候选预览"
          : "文稿书衣"}</span>
      </div>
      <div className="library-book__body">
        <div className="library-book__byline">{p.genre || "未设置类型"}{p.author ? ` / ${p.author}` : ""}</div>
        <h2 className="library-book__title" title={p.title}>{p.title}</h2>
        <p className="library-book__words">{(p.word_count ?? 0).toLocaleString("zh-CN")} 字 <span>·</span> 已写 {p.drafted_count ?? 0}/{p.chapter_count} 章
          {p.content_rating === "mature" && <span className="library-book__rating">成人内容</span>}
        </p>
        <dl className="library-book__statuses">
          <div><dt>正文</dt><dd><StatusPill status={p.status} label={projectTaskStatusLabel(p.status)} /></dd></div>
          <div><dt>封面</dt><dd><span className="library-cover-status" data-status={coverStatus} title={p.cover?.detail}>
            {COVER_STATUS_LABELS[coverStatus]}</span></dd></div>
        </dl>
        {p.cover?.detail && <p className="library-book__cover-note">{p.cover.detail}</p>}
        <div className="library-book__actions">
          {/* This native link covers the card in CSS; sibling controls stay above it. */}
          <Link to={`/projects/${p.id}`} className="library-book__open">打开作品 <Icon name="chevron-right" className="h-3.5 w-3.5" /></Link>
          <Link to={`/projects/${p.id}/covers`} className="library-book__cover-link">{COVER_ACTIONS[coverStatus]}</Link>
        </div>
        {onResume && p.task_id && (p.task_can_resume || resuming || resumeError) && (
          <div className="library-book__recovery">
            {(p.task_can_resume || resuming) && <button type="button" onClick={() => void resume()} disabled={resuming} className="library-inline-button">
              {resuming ? "恢复中…" : "恢复重试"}</button>}
            {resumeError && <p role="alert" className="library-book__error">{resumeError}</p>}
          </div>
        )}
        <details className="library-book__task">
          <summary>任务信息</summary>
          <p><span>任务 ID：</span><span className="break-all font-mono" title={p.task_id ?? undefined}>{p.task_id || "—"}</span></p>
          <p>{started ? <>启动 <time dateTime={p.task_started_at ?? undefined}>{started}</time></> : "启动时间未知"}</p>
        </details>
      </div>
      {onDelete && <details ref={menuRef} className="library-book__menu">
        <summary aria-label={`管理作品《${p.title}》`} title="更多操作">···</summary>
        <div><button type="button" onClick={() => { menuRef.current?.removeAttribute("open"); onDelete(p); }}>永久删除作品</button></div>
      </details>}
    </article>
  );
}
