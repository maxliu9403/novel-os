import { useEffect, useState } from "react";
import { api, type CodexEntry } from "../api/client";
import Modal, { Field, fieldClass } from "./Modal";
import ChoiceGroup from "./ChoiceGroup";
import EntityPicker from "./EntityPicker";
import { useToast } from "./toastContext";
import { BOND_OPTIONS } from "../lib/bonds";
import { displayLabel } from "../lib/displayLabels";


/** Shared Add Relationship form (chart + Codex Connections). */
export default function AddRelationshipModal({
  open,
  onClose,
  characters,
  projectId,
  onAdded,
  prefill,
}: {
  open: boolean;
  onClose: () => void;
  characters: CodexEntry[];
  projectId: string;
  onAdded: () => void;
  prefill?: { source?: string; target?: string } | null;
}) {
  const toast = useToast();
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("ally");
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);

  // Seed the form from the prefill each time the dialog opens. Adjusted during
  // render, not in an effect, so the first paint already shows the right pair.
  const session = open ? `${prefill?.source ?? ""}>${prefill?.target ?? ""}` : null;
  const [lastSession, setLastSession] = useState<string | null>(null);
  if (session !== lastSession) {
    setLastSession(session);
    if (open) {
      const first = prefill?.source ?? characters[0]?.id ?? "";
      const others = characters.filter((c) => c.id !== prefill?.source);
      setSource(first);
      setTarget(
        prefill?.target
        ?? others[0]?.id
        ?? characters.find((c) => c.id !== first)?.id
        ?? "",
      );
      setLabel("ally");
      setOther("");
    }
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const entityOptions = characters.map((c) => ({
    id: c.id,
    name: c.name,
    meta: c.role ? displayLabel(c.role) : undefined,
  }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!source || !target || source === target) return;
    setBusy(true);
    try {
      await api.addRelationship(projectId, {
        source_id: source,
        target_id: target,
        label: label === "other" ? (other.trim() || "unknown") : label,
      });
      toast("关系已添加", "success");
      onAdded();
      onClose();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="添加关系">
      <form onSubmit={submit}>
        <Field label="关系起点">
          <EntityPicker
            label="关系起点"
            value={source}
            onChange={setSource}
            options={entityOptions}
            excludeId={target}
            placeholder="查找关系起点人物…"
          />
        </Field>
        <Field label="关系终点">
          <EntityPicker
            label="关系终点"
            value={target}
            onChange={setTarget}
            options={entityOptions}
            excludeId={source}
            placeholder="查找与其关联的人物…"
          />
        </Field>
        <Field label="关系类型">
          <ChoiceGroup
            label="关系类型"
            variant="chips"
            size="sm"
            value={label}
            onChange={setLabel}
            options={BOND_OPTIONS}
          />
        </Field>
        {label === "other" && (
          <Field label="自定义关系">
            <input
              className={fieldClass}
              value={other}
              onChange={(e) => setOther(e.target.value)}
              placeholder="例如：儿时好友"
            />
          </Field>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="btn-ghost">取消</button>
          <button
            type="submit"
            disabled={busy || !source || !target || source === target}
            className="btn-primary disabled:opacity-40"
          >
            {busy ? "正在保存…" : "添加"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
