import { useRef, useState } from "react";
import { creativeTypesApi, type AudienceChannel, type CreativeRecord } from "../api/creativeTypes";
import { AUDIENCE_CHANNELS, AUDIENCE_LABELS } from "../lib/audience";
import Modal from "./Modal";
import { useConfirm } from "./confirmContext";

export default function AudienceGuidanceEditor({ record, onClose, onSaved }: {
  record: CreativeRecord; onClose: () => void; onSaved: (record: CreativeRecord) => void;
}) {
  const initial = record.document?.audience_guidance;
  const hasSavedDraft = record.active_version != null && record.versions.find(version => version.version === record.active_version)?.markdown !== record.draft;
  const [channels, setChannels] = useState<AudienceChannel[]>(initial?.audience_channels ?? []);
  const [guides, setGuides] = useState<Partial<Record<AudienceChannel, string>>>(initial?.guides ?? {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const confirm = useConfirm();
  const changed = AUDIENCE_CHANNELS.some(channel =>
    channels.includes(channel) !== (initial?.audience_channels.includes(channel) ?? false) ||
    (guides[channel] ?? "") !== (initial?.guides[channel] ?? ""));
  const valid = channels.length > 0 && channels.every(channel => !!guides[channel]?.trim());
  async function close() {
    if (busy) return;
    if (!changed || await confirm({ title: "频道指导尚未保存", message: "关闭会放弃本次频道指导修改，原说明不受影响。", confirmLabel: "放弃修改" })) onClose();
  }
  async function save() {
    if (busy || !valid || !changed) return;
    const signature = JSON.stringify([record.id, record.revision, channels, guides]);
    if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const next = await creativeTypesApi.audience(record.id, record.revision, operation.current.id, channels, guides);
      onSaved(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }
  return <Modal open onClose={() => void close()} title="适用频道与写作指导">
    <div className="knowledge-audience-editor">
      <p className="knowledge-muted">仅更新文末频道指导，原说明保持原样。新版本用于之后创建的故事。</p>
      {hasSavedDraft && <p className="knowledge-muted">保存并启用时，也会启用当前已保存的正文草稿。</p>}
      <fieldset disabled={busy}><legend>适用频道</legend>
        <div className="knowledge-choices">{AUDIENCE_CHANNELS.map(channel => <label key={channel} className={channels.includes(channel) ? "selected" : ""}>
          <input type="checkbox" checked={channels.includes(channel)} onChange={e => setChannels(current => e.target.checked ? AUDIENCE_CHANNELS.filter(item => current.includes(item) || item === channel) : current.filter(item => item !== channel))} />
          {AUDIENCE_LABELS[channel]}
        </label>)}</div>
        {AUDIENCE_CHANNELS.map(channel => <label className="knowledge-audience-guide" key={channel}>
          <span>{AUDIENCE_LABELS[channel]}写作指导{channels.includes(channel) ? " · 必填" : " · 暂不启用"}</span>
          <textarea rows={6} value={guides[channel] ?? ""} maxLength={8192} onChange={e => setGuides(current => ({ ...current, [channel]: e.target.value }))}
            placeholder="写明代入人物、冲突方法、人物选择、代价与回报；用方法指导创作，不固定剧情与章序。" />
        </label>)}
      </fieldset>
      {error && <p role="alert" className="knowledge-error">{error}</p>}
      <div className="knowledge-actions"><button type="button" className="btn-secondary" disabled={busy} onClick={() => void close()}>取消</button>
        {changed && <button type="button" className="btn-primary" disabled={busy || !valid} onClick={() => void save()}>{busy ? "正在保存…" : "保存并启用"}</button>}
      </div>
    </div>
  </Modal>;
}
