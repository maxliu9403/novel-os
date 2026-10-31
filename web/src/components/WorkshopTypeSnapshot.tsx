import { useEffect, useRef, useState } from "react";
import {
  creativeTypesApi,
  creativeRef,
  type SessionTypeContext,
  type CreativeProfile,
} from "../api/creativeTypes";
import CreativeProfileDiff from "./CreativeProfileDiff";
import type { WorkshopSession } from "../api/workshop";

type Props = {
  session: WorkshopSession;
  disabled: boolean;
  onUpdate: (session: WorkshopSession) => void;
};
export default function WorkshopTypeSnapshot(props: Props) {
  return (
    <SnapshotPanel
      key={`${props.session.id}:${props.session.revision}`}
      {...props}
    />
  );
}
function SnapshotPanel({ session, disabled, onUpdate }: Props) {
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const [open, setOpen] = useState(false),
    [data, setData] = useState<SessionTypeContext | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [review, setReview] = useState<Array<{
      name: string;
      before: number;
      after: number;
      changes: string[];
      oldProfile: CreativeProfile;
      newProfile: CreativeProfile;
    }> | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!open) return;
    let live = true;
    creativeTypesApi
      .sessionContext(session.id)
      .then((result) => {
        if (live) {
          setData(result);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [open, session.id, session.revision, reload]);
  const updated =
    data?.latest.filter(
      (i) =>
        i.status === "enabled" &&
        data.snapshot?.profiles.some(
          (p) =>
            p.profile.profile_id === i.id &&
            p.profile.version !== i.active_version,
        ),
    ) || [];
  const unavailable = data?.snapshot?.profiles.some(
    (p) =>
      !data.latest.some(
        (i) => i.id === p.profile.profile_id && i.status === "enabled",
      ),
  );
  async function inspect() {
    if (!data?.snapshot) return;
    setBusy(true);
    setError("");
    try {
      const differences = await Promise.all(
        updated.map(async (item) => {
          const current = await creativeTypesApi.get(item.id),
            version = current.versions.find(
              (v) => v.profile.version === item.active_version,
            );
          if (!version || version.sha256 !== item.source_sha256)
            throw new Error("资料又有变化，请重新读取版本。");
          const old = data.snapshot!.profiles.find(
            (p) => p.profile.profile_id === item.id,
          )!;
          const labels = {
            name: "名称",
            aliases: "别名",
            summary: "简介",
            dimension: "归类",
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
          return {
            name: item.name,
            oldProfile: old.profile,
            newProfile: version.profile,
            before: old.profile.version,
            after: version.profile.version,
            changes: (Object.keys(labels) as Array<keyof typeof labels>)
              .filter(
                (k) =>
                  JSON.stringify(old.profile[k]) !==
                  JSON.stringify(version.profile[k]),
              )
              .map((k) => labels[k]),
          };
        }),
      );
      setReview(differences);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function adopt() {
    if (!data?.snapshot || data.revision !== session.revision) return;
    setBusy(true);
    setError("");
    try {
      const refs = data.snapshot.profiles.map((p) => {
        const latest = data.latest.find((i) => i.id === p.profile.profile_id);
        return latest
          ? creativeRef(latest, p.role)
          : {
              profile_id: p.profile.profile_id,
              version: p.profile.version,
              source_sha256: p.source_sha256,
              role: p.role,
            };
      });
      const signature = JSON.stringify([session.id, session.revision, refs]);
      if (pending.current?.signature !== signature)
        pending.current = { signature, id: crypto.randomUUID() };
      const result = await creativeTypesApi.adoptSession(
        session.id,
        session.revision,
        refs,
        pending.current.id,
      );
      onUpdate(result);
      setReview(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details
      className="my-3 rounded-xl border border-paper-line bg-white/70 px-4 py-3 text-[12px] text-ink-muted"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="cursor-pointer">本讨论使用的类型说明与版本</summary>
      {error && (
        <p role="alert" className="mt-3 text-rose-700">
          {error}
          <button
            className="btn-ghost"
            onClick={() => {
              setData(null);
              setReview(null);
              setError("");
              setReload((v) => v + 1);
            }}
          >
            重新读取
          </button>
        </p>
      )}
      {!data && !error && <p className="mt-3">正在读取快照…</p>}
      {data?.snapshot ? (
        <>
          <div className="mt-3 flex flex-wrap gap-2">
            {data.snapshot.profiles.map((row) => (
              <span
                key={row.profile.profile_id}
                className="rounded-full bg-violet-soft px-3 py-1 text-violet"
              >
                {row.profile.name} · v{row.profile.version}
              </span>
            ))}
          </div>
          <p className="mt-3">
            修改资料库不会自动替换这份快照。当前说明用于头脑风暴与骨架设计。
          </p>
          {updated.length > 0 && (
            <div className="mt-3">
              <button
                className="btn-secondary"
                disabled={disabled || busy || unavailable}
                onClick={() => void inspect()}
              >
                有 {updated.length} 份新版说明 · 查看变化
              </button>
              {unavailable && (
                <p className="mt-2">
                  部分类型已停用，请先在基础设定中明确新的选择。
                </p>
              )}
            </div>
          )}
          {review && (
            <div className="mt-3 rounded-xl bg-violet-soft/60 p-3">
              <p className="font-semibold text-ink">采用前请核对变化</p>
              {review.map((r) => (
                <div key={r.oldProfile.profile_id} className="mt-2">
                  <p>
                    {r.name}：v{r.before} → v{r.after} ·{" "}
                    {r.changes.join("、") || "内容不变，仅新版本"}
                  </p>
                  <CreativeProfileDiff
                    before={r.oldProfile}
                    after={r.newProfile}
                  />
                </div>
              ))}
              <p className="mt-3">
                仅更新后续讨论上下文；保留当前骨架和作者决定，不启动模型。后续需明确更新骨架才能重新交付。
              </p>
              <button
                className="btn-primary mt-3"
                disabled={disabled || busy}
                onClick={() => void adopt()}
              >
                采用这些新版说明
              </button>
              <button
                className="btn-ghost ml-2"
                onClick={() => setReview(null)}
              >
                暂不采用
              </button>
            </div>
          )}
          <details className="mt-3">
            <summary className="cursor-pointer">
              查看保存的资料与模型输入
            </summary>
            <p className="my-2">
              以下为当前锁定版本的阶段投影。
              {data.last_request
                ? `最近交给模型适配器的请求：${new Date(data.last_request.submitted_at).toLocaleString()}；不代表上游已成功接收或完成。`
                : "此讨论尚无类型请求提交记录。"}
            </p>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-white p-3 text-[11px]">
              {JSON.stringify(
                {
                  snapshot: data.snapshot,
                  contexts: data.contexts,
                  context_sha256: data.context_sha256,
                  last_request: data.last_request,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      ) : (
        data && (
          <p className="mt-3">
            此讨论使用旧版题材策略，恢复时继续保留原说明。需要采用资料库时，可修改基础设定并明确从头推导。
          </p>
        )
      )}
    </details>
  );
}
