import type { ReactNode } from "react";
import type { WorkshopAssemblyProgress, WorkshopDeliveryProgress, WorkshopDeliveryStage, WorkshopRuntimeProgress } from "../api/workshop";
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

export function WorkshopRequestDiagnostic({ progress }: { progress: WorkshopRuntimeProgress | null }) {
  if (!progress) return null;
  const codes: Record<string, string> = {
    authentication: '认证失败', rate_limit: '请求受到限流', context_length: '上下文超过模型限制',
    invalid_request: '请求参数被拒绝', model_unavailable: '模型不可用', upstream_timeout: '等待上游超时',
    transport_error: '连接异常', server_error: '上游服务异常', config_error: '配置读取失败',
    sandbox_error: '运行环境异常', unclassified_error: '未分类诊断',
  };
  const components: Record<string, string> = { model_catalog: '模型列表查询', model_transport: '模型连接',
    telemetry: '诊断上报', configuration: '配置', sandbox: '运行环境', cli: 'Codex' };
  const code = progress.last_diagnostic_code && codes[progress.last_diagnostic_code];
  const failure = progress.failure_summary;
  const repeated = failure?.kind === 'no_content_timeout' && failure.consecutive_count > 1;
  if (!failure && !code && !progress.last_upstream_failure_kind) return null;
  return <div aria-label="模型请求诊断" className="mt-2 space-y-1 text-[13px] leading-relaxed text-ink-muted">
    {failure?.kind === 'no_content_timeout' && <p className="font-medium text-ink-text">{repeated
      ? `已连续 ${failure.consecutive_count} 次等待超时，均未观测到公开摘要或正文。`
      : '本轮等待超时，未观测到公开摘要或正文。'}</p>}
    {failure?.kind === 'timeout' && (progress.text_event_count ?? 0) > 0 && <p>本次请求曾收到文本增量，但未等到完整回复完成。</p>}
    {code && <p>{components[progress.last_diagnostic_component ?? ''] ?? '上游事件'}：{code}。{progress.last_diagnostic_component === 'model_catalog' && '此记录不代表生成请求失败。'}</p>}
    {progress.last_upstream_failure_kind && <p>观测到上游异常事件；不能据连接消息判断模型仍在生成。</p>}
    {repeated && <p>基础设定与对话已保留。建议先检查模型连接和运行详情，再手动重试；系统不会自动追加调用。</p>}
    {failure && <p>任务已结束，刷新页面不会重新发起请求。</p>}
  </div>;
}

function AssemblyCheckpoints({ progress, running, complete, resumable, renderContent }: {
  progress: WorkshopAssemblyProgress; running: boolean; complete: boolean; resumable: boolean;
  renderContent: (markdown: string) => ReactNode;
}) {
  const active = progress.parts.find((part) => part.part === progress.active_part && part.status !== "saved");
  const allSaved = progress.total_parts > 0 && progress.completed_parts === progress.total_parts;
  return <section aria-label="交付整合进度" className="mt-2 rounded-lg border border-violet/15 bg-white/55 px-2.5 py-2 text-[12px]">
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <p className="font-medium text-ink-text">分步补全交付内容</p>
      <p className="tabular-nums text-violet">已保存 {progress.completed_parts}/{progress.total_parts} 个部分</p>
    </div>
    {active && <p className="mt-1 break-words text-violet">{running ? "正在补全" : "待继续"}：{active.label}</p>}
    {allSaved && !complete && <p className="mt-1 text-ink-muted">{running ? "各部分已保存，正在整合与校验。" : "各部分已保存，尚待完成整合与校验。"}</p>}
    {!running && resumable && !allSaved && <p className="mt-1 text-ink-muted">继续时会复用已保存部分，只补全尚未完成的内容；不会自动重试。</p>}
    <details className="mt-1.5">
      <summary className="w-fit cursor-pointer text-violet">查看补全部分</summary>
      <ol className="mt-2 space-y-2">
        {progress.parts.map((part) => {
          const current = active?.part === part.part;
          return <li key={part.part} aria-current={current ? "step" : undefined} className="rounded-lg bg-white/65 px-2.5 py-2">
            <div className="flex items-start justify-between gap-3"><span className="min-w-0 break-words text-ink-text">{part.label}</span><span className="shrink-0 text-ink-muted">{part.status === "saved" ? "已保存" : current ? running ? "进行中" : "未完成" : "待开始"}</span></div>
            {part.status === "saved" && part.content_md && <details className="mt-1.5">
              <summary className="w-fit cursor-pointer text-violet">查看已保存内容</summary>
              <div className="mt-2 max-h-[24dvh] overflow-auto break-words pr-2" aria-label={`${part.label}已保存内容`}>{renderContent(part.content_md)}</div>
            </details>}
          </li>;
        })}
      </ol>
    </details>
  </section>;
}

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
    {progress.chapter_windows && <p className="mt-2 text-[12px] tabular-nums text-violet">
      已保存 {progress.chapter_windows.saved_chapters}/{progress.chapter_windows.total_chapters} 章规划
      {progress.active_stage === "chapter_plan" && !running && "，继续时从下一章接上。"}
    </p>}
    {progress.assembly && <AssemblyCheckpoints progress={progress.assembly} running={running}
      complete={progress.completed_stages.includes("assembly")} resumable={progress.resumable} renderContent={renderContent} />}
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
  const taskMinutes = progress?.task_budget_seconds && progress.task_budget_seconds > 0
    ? Math.round(progress.task_budget_seconds / 60 * 10) / 10 : null;
  const requestMinutes = progress?.request_timeout_seconds && progress.request_timeout_seconds > 0
    ? Math.round(progress.request_timeout_seconds / 60 * 10) / 10 : null;
  const timeoutMinutes = taskMinutes ?? (deadline !== null && roundStart !== null && deadline > roundStart
    ? Math.round((deadline - roundStart) / 60_000 * 10) / 10 : null);
  const chars = Math.max(0, Math.floor(progress?.content_chars ?? 0));
  const totalChars = Math.max(chars, Math.floor(progress?.cumulative_content_chars ?? chars));
  const nativeActivity = progress?.activity_time_basis === "batch_observed";
  const observedContent = reasoningAt !== null || textAt !== null;
  const processingReply = progress && ["parsing", "validating", "repairing", "completed"].includes(progress.phase);
  const endpointLabels = { models: "模型列表查询", responses: "响应连接", usage: "用量查询", instructions: "指令查询", other: "其他请求" };
  return <div aria-label="本轮运行状态">
    <p role="status" className="text-[14px] font-medium text-violet">{label}</p>
    {!!progress?.completion_grace_seconds && <p className="mt-2 text-[13px] font-medium text-violet">已进入回复收尾窗口：截止前仍在收到文本，本次额外等待最多 {progress.completion_grace_seconds} 秒。不会重新发起请求。</p>}
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
    <WorkshopRequestDiagnostic progress={progress} />
    <p className="mt-1.5">关闭窗口后后台请求会继续，返回此讨论可恢复查看。</p>
    <details className="mt-2">
      <summary className="w-fit cursor-pointer text-[12px] text-violet">运行详情</summary>
      <div className="mt-2 max-h-36 space-y-1 overflow-y-auto break-words rounded-lg bg-white/60 p-3 text-[12px]">
        <p>{taskMinutes !== null ? "本轮总时限" : "本轮最长等待"}：{timeoutMinutes !== null ? `${timeoutMinutes} 分钟` : "暂不可用"}{deadline !== null && progress?.deadline_at && <> · 预计截止时间 <time dateTime={progress.deadline_at}>{new Date(deadline).toLocaleString()}</time></>}</p>
        {requestMinutes !== null && <p>单次模型请求上限：{requestMinutes} 分钟；同时受本轮总时限约束。{progress?.completion_grace_limit
          ? `截止前仍有文本增量时，可获得一次最多 ${progress.completion_grace_limit} 秒的收尾窗口；无内容或仅有连接消息不会延长。`
          : '内容进展不会无限延长截止时间。'}</p>}
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
