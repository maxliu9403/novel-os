import { useState } from "react";
import { api, type CodexEntry } from "../api/client";
import Modal, { Field, fieldClass } from "./Modal";
import { useToast } from "./toastContext";

function identityText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function identityAliases(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((alias): alias is string => typeof alias === "string") : [];
}

function parseAliases(value: string): string[] {
  return [...new Set(value.split(/[,，\n]+/).map((alias) => alias.trim()).filter(Boolean))];
}

/**
 * Edit an existing Codex entry (issue #2).
 *
 * Sends only what changed. The endpoint applies fields individually, so a form
 * that edits a summary can never blank the notes — which matters because this
 * is a world model the Guardian validates prose against, and silently losing a
 * character's fear is worse than never having recorded it.
 *
 * Engine-owned facts — last appearance, arc progress, relationships — are
 * deliberately absent. They are derived from the manuscript, and a form that
 * overwrote them would put the world model at odds with the prose.
 */
export default function EditCodexModal({
  projectId,
  entry,
  open,
  onClose,
  onSaved,
}: {
  projectId: string;
  entry: CodexEntry | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [notes, setNotes] = useState("");
  const [role, setRole] = useState("");
  const [gender, setGender] = useState("");
  const [pronouns, setPronouns] = useState("");
  const [aliases, setAliases] = useState("");
  const [busy, setBusy] = useState(false);

  // Reload the form whenever a different entry is opened. Adjusted during
  // render so the first paint already shows the right entry, never the last one.
  const session = open && entry ? entry.id : null;
  const [lastSession, setLastSession] = useState<string | null>(null);
  if (session !== lastSession) {
    setLastSession(session);
    if (open && entry) {
      setName(entry.name ?? "");
      setSummary(entry.summary ?? "");
      setNotes(entry.notes ?? "");
      setRole(entry.role ?? "");
      setGender(identityText(entry.fields?.gender));
      setPronouns(identityText(entry.fields?.pronouns));
      setAliases(identityAliases(entry.fields?.aliases).join("，"));
    }
  }

  const isPerson = entry?.entry_type === "character";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!entry || busy) return;
    if (!name.trim()) {
      toast("名称不能为空。", "error");
      return;
    }

    // Only what the writer actually touched.
    const changes: Parameters<typeof api.updateCodexEntry>[2] = {};
    if (name !== entry.name) changes.name = name.trim();
    if (summary !== (entry.summary ?? "")) changes.summary = summary;
    if (notes !== (entry.notes ?? "")) changes.notes = notes;
    if (isPerson && role !== (entry.role ?? "")) changes.role = role;
    if (isPerson) {
      if (gender.trim() !== identityText(entry.fields?.gender)) changes.gender = gender.trim();
      if (pronouns.trim() !== identityText(entry.fields?.pronouns)) changes.pronouns = pronouns.trim();
      const currentAliases = identityAliases(entry.fields?.aliases);
      if (aliases !== currentAliases.join("，")) {
        const parsedAliases = parseAliases(aliases);
        if (JSON.stringify(parsedAliases) !== JSON.stringify(currentAliases)) {
          changes.aliases = parsedAliases;
        }
      }
    }

    if (Object.keys(changes).length === 0) {
      onClose();
      return;
    }

    setBusy(true);
    try {
      await api.updateCodexEntry(projectId, entry.id, changes);
      toast("已保存", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open && entry != null} onClose={onClose} title={`编辑「${entry?.name ?? ""}」`}>
      <form onSubmit={save}>
        <Field label="名称">
          <input
            className={fieldClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="名称"
          />
        </Field>

        {isPerson && (
          <>
            <Field label="角色定位">
              <input
                className={fieldClass}
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="例如：主角、反派、配角…"
                aria-label="角色定位"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="性别">
                <input
                  className={fieldClass}
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                  placeholder="例如：女性、男性、未知"
                  aria-label="性别"
                />
              </Field>
              <Field label="代词">
                <input
                  className={fieldClass}
                  value={pronouns}
                  onChange={(e) => setPronouns(e.target.value)}
                  placeholder="例如：她 / she、他 / he、未知"
                  aria-label="代词"
                />
              </Field>
            </div>
            <Field label="别名">
              <textarea
                className={fieldClass}
                rows={2}
                value={aliases}
                onChange={(e) => setAliases(e.target.value)}
                placeholder="用逗号或换行分隔；没有别名可留空"
                aria-label="别名"
              />
            </Field>
          </>
        )}

        <Field label="摘要">
          <textarea
            className={fieldClass}
            rows={2}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            aria-label="摘要"
          />
        </Field>

        <Field label="备注">
          <textarea
            className={fieldClass}
            rows={4}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            aria-label="备注"
          />
        </Field>

        <div className="mt-5 flex justify-end gap-3">
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn-primary disabled:opacity-40" disabled={busy}>
            {busy ? "正在保存…" : "保存"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
