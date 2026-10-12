import type { ReactNode } from "react";
import type { WorkshopDeliveryProgress, WorkshopDeliveryStage, WorkshopRuntimeProgress } from "../api/workshop";
import { DELIVERY_STAGE_LABELS } from "../lib/workshopProgress";

const DELIVERY_STAGES: WorkshopDeliveryStage[] = ["story_design", "chapter_plan", "assembly"];
const STAGE_CARD_LABELS: Record<WorkshopDeliveryStage, string> = { story_design: "全局设计", chapter_plan: "章节规划", assembly: "交付整合" };

function timestamp(value?: string | null) {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function activityAge(value: string | null | undefined, now: number, missing: string) {
  const time = timestamp(value);
  if (time === null) return missing;
  if (time > now) return "时间待同步";
  return `${Math.floor((now - time) / 1000)} 秒前`;
}

const eventCount = (count: number | undefined) => Number.isFinite(count) ? Math.max(0, Math.floor(count!)) : 0;

export function WorkshopDeliveryCheckpoints({ progress, running, renderContent }: {
  progress: WorkshopDeliveryProgress; running: boolean; renderContent: (markdown: string) => ReactNode;
}) {
  return <section aria-label="骨架交付阶段" className="rounded-xl border border-paper-line bg-white/55 px-3 py-2.5">
    <div className="flex flex-wrap items-center justify-between gap-2 text-[12px]">
      <h3 className="font-semibold text-ink-text">骨架交付阶段</h3>
      <span className="text-ink-muted">已保存 {progress.completed_stages.length}/3 个阶段</span>
    </div>
    <ol className="mt-2 grid grid-cols-3 gap-2">
      {DELIVERY_STAGES.map((stage, index) => {
        const complete = progress.completed_stages.includes(stage);
        const active = progress.active_stage === stage && !complete;
        const status = complete ? "已保存" : active ? running ? "进行中" : "未完成" : "待开始";
        return <li key={stage} aria-current={active ? "step" : undefined}
          className={`min-w-0 rounded-lg px-2 py-1.5 text-[12px] ${complete ? "bg-violet-soft/45 text-violet" : active ? "bg-violet-soft text-violet" : "bg-white/60 text-ink-muted"}`}>
          <span className="mr-1.5 tabular-nums opacity-70">{index + 1}</span><span className="font-medium">{STAGE_CARD_LABELS[stage]}</span>
          <p className="mt-1 text-[11px]">{status}</p>
        </li>;
      })}
    </ol>
    <p className="mt-2 text-[11px] leading-relaxed text-ink-muted">阶段成果保存后才标记完成；最终整合与校验通过后才更新正式骨架。</p>
    {progress.stages?.filter((stage) => stage.status === "saved" && stage.content_md && progress.completed_stages.includes(stage.stage)).map((stage) =>
      <details key={stage.stage} className="mt-2 border-t border-paper-line pt-2">
        <summary className="w-fit cursor-pointer text-[12px] text-violet">查看已保存的{DELIVERY_STAGE_LABELS[stage.stage]}</summary>
        <div className="mt-3 max-h-[28dvh] overflow-auto pr-2" aria-label={`${DELIVERY_STAGE_LABELS[stage.stage]}阶段成果`}>{renderContent(stage.content_md!)}</div>
      </details>)}
    {progress.invalid_reason && <p className="mt-2 text-[12px] text-ink-muted">阶段恢复暂不可用：{progress.invalid_reason}</p>}
  </section>;
}

export function WorkshopRuntimeStatus({ progress, label, now, startedAt }: {
  progress: WorkshopRuntimeProgress | null; label: string; now: number; startedAt: number | null;
}) {
  const turnContentAt = progress?.last_turn_content_at ?? progress?.last_content_at;
  const contentAt = timestamp(progress?.last_content_at);
  const reasoningAt = timestamp(progress?.last_reasoning_observed_at);
  const textAt = timestamp(progress?.last_text_observed_at);
  const requestStart = timestamp(progress?.request_started_at);
  const currentContentTimes = [contentAt, reasoningAt, textAt].filter((value): value is number => value !== null);
  // Once per-request timestamps are available, previous attempts must not
  // suppress the warning for a newer request. Legacy records retain their fallback.
  const silenceSince = currentContentTimes.length ? Math.max(...currentContentTimes)
    : requestStart ?? timestamp(turnContentAt) ?? startedAt;
  const waiting = !progress || progress.phase === "waiting" || progress.phase === "receiving";
  const silentMinutes = waiting && silenceSince !== null && now - silenceSince >= 120_000
    ? Math.floor((now - silenceSince) / 60_000) : null;
  const deadline = timestamp(progress?.deadline_at);
  const roundStart = timestamp(progress?.started_at);
  const timeoutMinutes = deadline !== null && roundStart !== null && deadline > roundStart
    ? Math.round((deadline - roundStart) / 60_000 * 10) / 10 : null;
  const chars = Math.max(0, Math.floor(progress?.content_chars ?? 0));
  const totalChars = Math.max(chars, Math.floor(progress?.cumulative_content_chars ?? chars));
  const nativeActivity = progress?.activity_time_basis === "batch_observed";
  const observedContent = reasoningAt !== null || textAt !== null;
  const processingReply = progress && ["parsing", "validating", "repairing", "completed"].includes(progress.phase);
  const endpointLabels = { models: "模型列表查询", responses: "响应连接", usage: "用量查询", instructions: "指令查询", other: "其他请求" };
  return <div aria-label="本轮运行状态">
    <p role="status" className="text-[14px] font-medium text-violet">{label}</p>
    <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
      {nativeActivity ? <>
        <div className="flex flex-wrap gap-x-2"><dt>最近观测到推理摘要</dt><dd>{activityAge(progress.last_reasoning_observed_at, now, "尚未观测到")}</dd></div>
        <div className="flex flex-wrap gap-x-2"><dt>最近观测到正文增量</dt><dd>{activityAge(progress.last_text_observed_at, now, "尚未观测到")}</dd></div>
      </> : <div className="flex gap-2"><dt>后台最近检查</dt><dd>{activityAge(progress?.last_worker_check_at, now, "尚无检查记录")}</dd></div>}
      <div className="flex flex-wrap gap-x-2"><dt>{nativeActivity ? "最近收到回复文本" : "最近收到模型内容"}</dt><dd>{activityAge(turnContentAt, now, totalChars > 0 ? "时间未记录" : "尚未收到")}{totalChars > 0 && ` · 本轮已收到 ${totalChars} 字符`}</dd></div>
    </dl>
    {nativeActivity && !processingReply && <p className="mt-1.5 text-[12px]">{observedContent
      ? "已观测到模型内容进展；完整回复仍在等待中。"
      : progress.last_transport_observed_at ? "连接有消息，尚未观测到新的推理摘要或正文。" : "尚未观测到模型内容进展，正在等待回执。"}摘要可能不返回，未观测到不代表模型已停止。</p>}
    {progress?.repairing_response && <p className="mt-1.5 text-[13px]">{progress.last_completed_attempt ? `第 ${progress.last_completed_attempt} 次请求已返回完整内容，` : "已收到的内容仍保留，"}正在修复格式；校验通过后才会更新骨架。</p>}
    {silentMinutes !== null && <p className="mt-1.5 text-[13px]">已有 {silentMinutes} 分钟未收到新内容，尚不能确认模型是否正在生成。你可以继续等待或暂停本轮。</p>}
    <p className="mt-1.5">关闭窗口后后台请求会继续，返回此讨论可恢复查看。</p>
    <details className="mt-2">
      <summary className="w-fit cursor-pointer text-[12px] text-violet">运行详情</summary>
      <div className="mt-2 max-h-36 space-y-1 overflow-y-auto break-words rounded-lg bg-white/60 p-3 text-[12px]">
        <p>本轮最长等待：{timeoutMinutes !== null ? `${timeoutMinutes} 分钟` : "暂不可用"}{deadline !== null && progress?.deadline_at && <> · 预计截止时间 <time dateTime={progress.deadline_at}>{new Date(deadline).toLocaleString()}</time></>}</p>
        <p>请求尝试：{progress?.attempt ? `第 ${progress.attempt} 次` : progress ? "尚未开始" : "暂不可用"}{progress?.request_id && <> · 请求 ID <code>{progress.request_id}</code></>}</p>
        <p>本次请求内容：{chars > 0 ? `已收到 ${chars} 字符 · ${activityAge(progress?.last_content_at, now, "时间未记录")}` : "尚未收到"}</p>
        {nativeActivity && <>
          <p>本次请求观测计数：公开摘要 {eventCount(progress.reasoning_event_count)} 次 · 正文增量 {eventCount(progress.text_event_count)} 次 · 连接消息 {eventCount(progress.transport_event_count)} 次</p>
          <p>最近观测到连接消息：{activityAge(progress.last_transport_observed_at, now, "尚未观测到")}</p>
          <p>以上时间是原生指标批次的接收时间，不是逐条内容产生的精确时刻；计数不等于 Token 或字符数。</p>
          <p>后台最近检查：{activityAge(progress.last_worker_check_at, now, "尚无检查记录")}</p>
        </>}
        {progress?.last_completed_attempt && <p>最近完整返回：第 {progress.last_completed_attempt} 次请求。返回完成不代表骨架已通过校验。</p>}
        <p>最近调用事件：{activityAge(progress?.last_cli_event_at, now, "尚未收到")}</p>
        <p>最近上游事件：{activityAge(progress?.last_upstream_event_at, now, "尚未收到")}{progress?.upstream_endpoint_category && <> · {endpointLabels[progress.upstream_endpoint_category]}</>}{progress?.upstream_event && <> · <code>{progress.upstream_event}</code></>}</p>
        <p>上游事件和后台检查只表示收到信号，不代表模型持续思考或已生成正文。</p>
        <p>输出预算：{progress?.configured_max_tokens ? `${progress.configured_max_tokens} tokens（配置值）` : "未记录"}
          {progress?.output_limit_supported === false ? "；当前调用方式不支持应用侧输出上限，此值未生效。"
            : progress?.output_limit_supported === true && progress.effective_max_tokens !== null ? `；实际发送上限 ${progress.effective_max_tokens} tokens。`
              : "；实际输出上限尚未确认。"}</p>
      </div>
    </details>
  </div>;
}
