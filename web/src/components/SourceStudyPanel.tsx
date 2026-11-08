import { useEffect, useState } from "react";
import { sourceBooksApi, type SourceStudyState } from "../api/sourceBooks";
import { useConfirm } from "./confirmContext";
import "../styles/source-library.css";

import { sourceStatusLabels } from "../lib/sourceStatus";

function duration(seconds: number) {
  const n = Math.max(0, Math.floor(seconds));
  return n < 60 ? `${n} 秒` : `${Math.floor(n / 60)} 分 ${n % 60} 秒`;
}
function secondsSince(value: string | null | undefined, clock: number) {
  return value && Number.isFinite(Date.parse(value))
    ? Math.max(0, (clock - Date.parse(value)) / 1000)
    : null;
}
export default function SourceStudyPanel({
  id,
  study,
  onRefresh,
  hasPlan = true,
}: {
  id: string;
  study: SourceStudyState;
  onRefresh: () => void;
  hasPlan?: boolean;
}) {
  const confirm = useConfirm();
  const [clock, setClock] = useState(Date.now),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const active =
    ["running", "queued"].includes(study.status) || !!study.worker_active;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const p = study.progress ?? {};
  const end = !active && (study.finished_at || study.updated_at);
  const elapsed = secondsSince(study.started_at, end ? Date.parse(end) : clock);
  const quiet = secondsSince(
    p.last_content_at || p.request_started_at || study.started_at,
    clock,
  );
  const checked = secondsSince(study.checked_at, clock);
  const heartbeat = secondsSince(p.heartbeat_at, clock);
  const title =
    study.cancel_requested && active
      ? "正在暂停，保存已完成结果"
      : sourceStatusLabels[study.status];
  const stage =
    study.stage === "planning"
      ? "设计续写规划"
      : study.stage === "synthesis"
        ? "整合分析与续写规划"
        : "阅读原文与 Synopsis";
  const model = study.binding?.model;
  async function action(kind: "analyze" | "cancel" | "restart") {
    if (
      kind === "restart" &&
      !(await confirm({
        title: "重新读取参考资料？",
        message:
          "将使用当前模型从头读取，并生成新的分析版本。已有分析会保留在版本记录中，本次会产生新的模型用量。",
        confirmLabel: "重新读取",
      }))
    )
      return;
    setBusy(true);
    setError("");
    try {
      if (kind === "cancel") await sourceBooksApi.cancel(id);
      else await sourceBooksApi.analyze(id, kind === "restart");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      onRefresh();
    }
  }
  return (
    <section
      className={`source-study-panel ${active ? "is-active" : ""}`}
      aria-label="参考资料分析进度"
    >
      <div className="source-study-heading">
        <div>
          <span className={`source-study-dot ${active ? "is-active" : ""}`} />
          <strong role="status">{title}</strong>
        </div>
        {elapsed !== null && (
          <span className="source-study-time">
            本次用时 {duration(elapsed)}
          </span>
        )}
      </div>
      {active && (
        <p className="source-study-stage">
          {stage}
          {study.current
            ? ` · 第 ${study.current} / ${study.total} 步`
            : " · 准备请求"}
        </p>
      )}
      {study.total > 0 && (
        <>
          <progress
            aria-label="已保存分析步骤"
            value={Math.min(study.completed, study.total)}
            max={study.total}
          />
          <div className="source-study-metrics">
            <span>
              已保存 {study.completed} / {study.total} 个步骤
            </span>
            {checked !== null && <span>状态同步于 {duration(checked)}前</span>}
          </div>
        </>
      )}
      {active && (
        <>
          <div className="source-study-signal">
            {p.phase === "validating"
              ? "已收到完整回复，正在检查并保存。"
              : p.received_chars
                ? `本次已收到 ${p.received_chars.toLocaleString()} 字符；最近内容在 ${duration(quiet ?? 0)}前。`
                : "正在等待模型返回分析内容。"}
          </div>
          {quiet !== null && quiet >= 120 && (
            <p className="source-study-wait">
              已 {duration(quiet)} 未收到新内容。
              {study.worker_active
                ? "后台任务仍在等待；"
                : "可刷新核对任务状态；"}
              这不能证明模型持续推理。可以继续等待或暂停。
            </p>
          )}
          <p className="source-study-note">
            关闭窗口后任务继续。可从导航栏「参考资料」随时返回。
          </p>
        </>
      )}
      {study.status === "complete" && (
        <p className="source-study-note">
          分析已保存，可直接用于创作；再次选择不会重复读取。
        </p>
      )}
      {study.error && (
        <details className="source-study-error">
          <summary>查看失败原因</summary>
          <p>{study.error}</p>
        </details>
      )}
      <div className="source-study-actions">
        {!active && study.status === "complete" && !hasPlan && (
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void action("analyze")}
          >
            生成续写规划
          </button>
        )}
        {active ? (
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || study.cancel_requested}
            onClick={() => void action("cancel")}
          >
            {study.cancel_requested ? "暂停处理中…" : "暂停阅读"}
          </button>
        ) : (
          study.status !== "complete" && (
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => void action("analyze")}
            >
              {study.status === "pending" ? "读取全部资料" : "继续未完成部分"}
            </button>
          )
        )}
        <button type="button" className="btn-secondary" onClick={onRefresh}>
          刷新状态
        </button>
        {!active &&
          ["complete", "failed", "cancelled", "interrupted"].includes(
            study.status,
          ) && (
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => void action("restart")}
            >
              重新读取
            </button>
          )}
      </div>
      {error && <p role="alert">{error}</p>}
      <details className="source-study-details">
        <summary>运行详情</summary>
        <dl>
          <div>
            <dt>分析模型</dt>
            <dd>
              {model?.model ?? "开始后记录"}
              {model?.reasoning_effort ? ` · ${model.reasoning_effort}` : ""}
            </dd>
          </div>
          {model?.timeout_seconds && (
            <div>
              <dt>单次时限</dt>
              <dd>{duration(model.timeout_seconds)}</dd>
            </div>
          )}
          {p.input_bytes !== undefined && (
            <div>
              <dt>本次输入</dt>
              <dd>
                {Math.ceil(p.input_bytes / 1024)} KB
                {p.source_files
                  ? ` · ${p.source_files} 个资料文件（按容量分批）`
                  : " · 综合前序结果"}
              </dd>
            </div>
          )}
          {active && heartbeat !== null && (
            <div>
              <dt>后台心跳</dt>
              <dd>{duration(heartbeat)}前（仅代表执行器存活）</dd>
            </div>
          )}
          {p.last_activity_at && (
            <div>
              <dt>模型动态</dt>
              <dd>
                {p.activity_kind === "text"
                  ? "观察到文本事件"
                  : "观察到推理摘要事件"}{" "}
                · {duration(secondsSince(p.last_activity_at, clock) ?? 0)}前
              </dd>
            </div>
          )}
          {p.last_transport_at && (
            <div>
              <dt>上游连接事件</dt>
              <dd>
                {duration(secondsSince(p.last_transport_at, clock) ?? 0)}
                前（不代表新内容）
              </dd>
            </div>
          )}
        </dl>
        <p>
          进度只计算已保存结果。一次请求可能阅读多章，等待时不会虚增百分比。
        </p>
      </details>
    </section>
  );
}
