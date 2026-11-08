import { useEffect, useRef, useState } from "react";
import {
  creativeTypesApi,
  creativeRef,
  type SessionTypeContext,
} from "../api/creativeTypes";
import type { WorkshopSession } from "../api/workshop";

type Props = {
  session: WorkshopSession;
  disabled: boolean;
  onUpdate: (session: WorkshopSession) => void;
};

export default function WorkshopTypeSnapshot(props: Props) {
  const snapshot = props.session.creative_design?.policy.type_snapshot;
  if (snapshot && !["selection-snapshot.v1", "selection-snapshot.v2"].includes(snapshot.schema)) {
    return <details className="knowledge-snapshot">
      <summary>本书的历史创作资料与固定版本</summary>
      <p>这是旧版资料快照，仅供回看，未转换为新的 Genre 与 Tags。</p>
      <p>{props.session.input.genres.join(" · ")}</p>
      <details><summary>查看已保存的完整快照</summary><pre>{JSON.stringify(snapshot, null, 2)}</pre></details>
    </details>;
  }
  return <CurrentTypeSnapshot key={props.session.id} {...props} />;
}

function CurrentTypeSnapshot({
  session,
  disabled,
  onUpdate,
}: Props) {
  const [open, setOpen] = useState(false),
    [data, setData] = useState<SessionTypeContext | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [compare, setCompare] = useState<Array<{
    id: string;
    before: string;
    after: string;
  }> | null>(null);
  useEffect(() => {
    if (!open) return;
    let live = true;
    creativeTypesApi
      .sessionContext(session.id)
      .then((d) => {
        if (live) {
          setData(d);
          setCompare(null);
        }
      })
      .catch((e) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, [open, session.id, session.revision]);
  const operation = useRef<{ key: string; id: string } | null>(null);
  const updates =
    data?.latest.filter(
      (i) =>
        i.status === "enabled" &&
        data.snapshot?.documents.some(
          (r) => r.id === i.id && r.version !== i.active_version,
        ),
    ) ?? [];
  async function inspect() {
    setBusy(true);
    setError("");
    try {
      setCompare(
        await Promise.all(
          updates.map(async (item) => {
            const latest = await creativeTypesApi.get(item.id);
            const version = latest.versions.find(
              (v) => v.version === item.active_version,
            );
            if (!version || version.sha256 !== item.source_sha256)
              throw new Error("资料又有变化，请重新打开核对");
            return {
              id: item.id,
              before: data!.snapshot!.documents.find((r) => r.id === item.id)!
                .markdown,
              after: version.markdown,
            };
          }),
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function adopt() {
    if (!data?.snapshot || data.revision !== session.revision) return;
    setBusy(true);
    setError("");
    try {
      const refs = data.snapshot.documents.map((r) => {
        const latest = data.latest.find((i) => i.id === r.id);
        return latest?.status === "enabled"
          ? creativeRef(latest)
          : { id: r.id, version: r.version, source_sha256: r.source_sha256 };
      });
      const key = JSON.stringify([session.id, session.revision, refs]);
      if (operation.current?.key !== key)
        operation.current = { key, id: crypto.randomUUID() };
      onUpdate(
        await creativeTypesApi.adoptSession(
          session.id,
          session.revision,
          refs,
          operation.current.id,
        ),
      );
      operation.current = null;
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details
      className="knowledge-snapshot"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>本书的频道、Genre / Tags 与固定版本</summary>
      {error && <p role="alert">{error}</p>}
      {data?.snapshot && (
        <>
          <div className="knowledge-choices">
            {data.snapshot.audience_channel && <span>{data.snapshot.audience_channel === 'female' ? '女频' : '男频'}</span>}
            {data.snapshot.documents.map((r) => (
              <span key={r.id}>
                {data.latest.find((i) => i.id === r.id)?.name ?? r.id} · v
                {r.version}
              </span>
            ))}
          </div>
          <p>资料库更新不会改变本书。新版本需核对后采用。</p>
          {!!updates.length && (
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || disabled}
              onClick={() => void inspect()}
            >
              核对 {updates.length} 份新版
            </button>
          )}
          {compare && (
            <div>
              {compare.map((c) => (
                <details key={c.id}>
                  <summary>{c.id} · 对比全文</summary>
                  <div className="knowledge-compare">
                    <pre>{c.before}</pre>
                    <pre>{c.after}</pre>
                  </div>
                </details>
              ))}
              <button
                type="button"
                className="btn-primary"
                disabled={busy || disabled}
                onClick={() => void adopt()}
              >
                采用新版，下一轮讨论生效
              </button>
            </div>
          )}
          <details>
            <summary>查看完整模型输入</summary>
            <pre>{JSON.stringify(data.contexts, null, 2)}</pre>
          </details>
        </>
      )}
    </details>
  );
}
