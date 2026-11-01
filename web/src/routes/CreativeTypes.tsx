import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  creativeTypesApi,
  CreativeApiError,
  type CreativeProfile,
  type CreativeRecord,
  type CreativeLibrary,
  type CreativePreview,
  type CreativeStage,
  type Guidance,
  type Mechanism,
} from "../api/creativeTypes";
import CreativeProfileDiff from "../components/CreativeProfileDiff";
import Scene from "../components/Scene";
import "../styles/creative-types.css";

const stageNames: Record<CreativeStage, string> = {
  brainstorm: "头脑风暴",
  story_design: "完整骨架",
  chapter_plan: "章节规划",
  draft: "正文写作",
  review: "评议",
};
const stages = Object.keys(stageNames) as CreativeStage[];
const priorities = {
  essential: "优先讨论",
  recommended: "建议采用",
  optional: "可选灵感",
};
const stateNames = { enabled: "已启用", draft: "草稿", disabled: "已停用" };
const tabs = {
  core: "定位与分类",
  mechanisms: "故事看点",
  checks: "文风与检查",
  preview: "模型预览与版本",
};
type Tab = keyof typeof tabs;
type GuidanceKey =
  | "reader_promises"
  | "character_dynamics"
  | "voice_guidance"
  | "pitfalls"
  | "diagnostics";
const lines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
const uid = () => `item_${crypto.randomUUID().replaceAll("-", "")}`;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function TextField({
  label,
  value,
  onChange,
  multiline = false,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  hint?: string;
}) {
  return (
    <label className="ct-field">
      <span>{label}</span>
      {multiline ? (
        <textarea
          rows={2}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} />
      )}{" "}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function LinesField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
}) {
  // Keep the editing text (including a trailing newline) local while typing.
  const [text, setText] = useState(value.join("\n"));
  return (
    <TextField
      label={label}
      value={text}
      multiline
      hint="每行一项；留空表示未限定。"
      onChange={(v) => {
        setText(v);
        onChange(lines(v));
      }}
    />
  );
}
function StagePicker({
  value,
  onChange,
}: {
  value: CreativeStage[];
  onChange: (value: CreativeStage[]) => void;
}) {
  return (
    <details className="ct-stages">
      <summary>
        适用阶段 · {value.map((s) => stageNames[s]).join("、") || "尚未选择"}
      </summary>
      <div>
        {stages.map((s) => (
          <label key={s}>
            <input
              type="checkbox"
              checked={value.includes(s)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...value, s]
                    : value.filter((v) => v !== s),
                )
              }
            />
            {stageNames[s]}
          </label>
        ))}
      </div>
    </details>
  );
}
function GuidanceEditor({
  title,
  items,
  onChange,
}: {
  title: string;
  items: Guidance[];
  onChange: (items: Guidance[]) => void;
}) {
  return (
    <section className="ct-block">
      <h3>{title}</h3>
      {items.map((g, i) => (
        <div className="ct-guide" key={g.id}>
          <div className="ct-guide-row">
            <span>{String(i + 1).padStart(2, "0")}</span>
            <textarea
              rows={2}
              aria-label={`${title} ${i + 1}`}
              value={g.text}
              onChange={(e) =>
                onChange(
                  items.map((v, j) =>
                    i === j ? { ...v, text: e.target.value } : v,
                  ),
                )
              }
            />
            <select
              aria-label={`${title} ${i + 1} 重要性`}
              value={g.importance}
              onChange={(e) =>
                onChange(
                  items.map((v, j) =>
                    i === j
                      ? {
                          ...v,
                          importance: e.target.value as Guidance["importance"],
                        }
                      : v,
                  ),
                )
              }
            >
              {Object.entries(priorities).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <button
              type="button"
              aria-label={`删除${title} ${i + 1}`}
              onClick={() => onChange(items.filter((_, j) => i !== j))}
            >
              ×
            </button>
          </div>
          <StagePicker
            value={g.stages}
            onChange={(value) =>
              onChange(
                items.map((v, j) => (i === j ? { ...v, stages: value } : v)),
              )
            }
          />
        </div>
      ))}
      <button
        type="button"
        className="ct-text-button"
        disabled={items.length >= 12}
        onClick={() =>
          onChange([
            ...items,
            {
              id: uid(),
              text: "",
              importance: "recommended",
              stages: [...stages],
            },
          ])
        }
      >
        ＋ 添加{title}
      </button>
    </section>
  );
}

export default function CreativeTypes() {
  const [library, setLibrary] = useState<CreativeLibrary | null>(null),
    [record, setRecord] = useState<CreativeRecord | null>(null),
    [draft, setDraft] = useState<CreativeProfile | null>(null);
  const [tab, setTab] = useState<Tab>("core"),
    [search, setSearch] = useState(""),
    [dimension, setDimension] = useState("all"),
    [status, setStatus] = useState("all");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [conflict, setConflict] = useState(false),
    [showComparison, setShowComparison] = useState(false),
    [mobileDetail, setMobileDetail] = useState(false);
  const [previewResult, setPreviewResult] = useState<{
      key: string;
      value?: CreativePreview;
      error?: string;
    } | null>(null),
    [previewStage, setPreviewStage] = useState<CreativeStage>("brainstorm"),
    [previewMode, setPreviewMode] = useState<"active" | "draft">("active");
  const mounted = useRef(true),
    sequence = useRef(0),
    pending = useRef<{ signature: string; id: string } | null>(null);
  const dirty = Boolean(
    record && draft && JSON.stringify(record.draft) !== JSON.stringify(draft),
  );
  const accept = (r: CreativeRecord) => {
    setShowComparison(false);
    setRecord(r);
    setDraft(structuredClone(r.draft));
    setConflict(false);
    setPreviewResult(null);
    pending.current = null;
  };
  const refresh = useCallback(async () => {
    const l = await creativeTypesApi.list();
    if (mounted.current) setLibrary(l);
    return l;
  }, []);
  useEffect(() => {
    mounted.current = true;
    const generation = ++sequence.current;
    void refresh()
      .then(async (l) => {
        const first =
          l.items.find((i) => i.id === "ct_marriage_betrayal") || l.items[0];
        if (first) {
          const r = await creativeTypesApi.get(first.id);
          if (mounted.current && sequence.current === generation) accept(r);
        }
      })
      .catch((e) => {
        if (mounted.current) setError(message(e));
      });
    return () => {
      mounted.current = false;
    };
  }, [refresh]);
  useEffect(() => {
    if (!dirty) return;
    const leave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const link = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest("a[href]");
      if (
        a &&
        a.getAttribute("target") !== "_blank" &&
        !window.confirm("当前修改尚未保存，仍然离开？")
      ) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", leave);
    document.addEventListener("click", link, true);
    return () => {
      window.removeEventListener("beforeunload", leave);
      document.removeEventListener("click", link, true);
    };
  }, [dirty]);
  const active = record?.versions.find(
    (v) => v.profile.version === record.active_version,
  );
  const candidate = previewMode === "draft" ? draft : active?.profile;
  const previewKey = JSON.stringify([candidate, previewStage]);
  const preview =
    previewResult?.key === previewKey ? previewResult.value : null;
  const previewError = !candidate
    ? "尚无启用版本，请先填写并启用资料。"
    : previewResult?.key === previewKey
      ? previewResult.error
      : "";
  useEffect(() => {
    if (tab !== "preview" || !candidate) return;
    let alive = true;
    const timer = setTimeout(() => {
      void creativeTypesApi
        .preview({ profile: candidate, stage: previewStage })
        .then((value) => {
          if (alive) setPreviewResult({ key: previewKey, value });
        })
        .catch((e) => {
          if (alive) setPreviewResult({ key: previewKey, error: message(e) });
        });
    }, 180);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [candidate, previewStage, previewKey, tab]);

  function update<K extends keyof CreativeProfile>(
    key: K,
    value: CreativeProfile[K],
  ) {
    setDraft((p) => (p ? { ...p, [key]: value } : p));
    setNotice("");
  }
  function group(key: GuidanceKey, title: string) {
    return (
      draft && (
        <GuidanceEditor
          title={title}
          items={draft[key]}
          onChange={(v) => update(key, v)}
        />
      )
    );
  }
  async function choose(id: string) {
    if (
      busy ||
      (dirty && !window.confirm("当前草稿尚未保存，放弃修改并切换？"))
    )
      return;
    const generation = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      const r = await creativeTypesApi.get(id);
      if (mounted.current && generation === sequence.current) {
        accept(r);
        setTab("core");
        setMobileDetail(true);
      }
    } catch (e) {
      if (mounted.current) setError(message(e));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function mutate(
    action: "draft" | "activate" | "disable" | "create" | "copy",
  ) {
    if (busy) return;
    if (
      (action === "create" || action === "copy" || action === "disable") &&
      dirty &&
      !window.confirm("当前有未保存修改，继续此操作？")
    )
      return;
    sequence.current++; // A user action supersedes any pending initial selection.
    const signature = JSON.stringify([
      action,
      record?.id,
      record?.revision,
      draft,
    ]);
    if (pending.current?.signature !== signature)
      pending.current = { signature, id: crypto.randomUUID() };
    const operation = pending.current.id;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let r: CreativeRecord;
      if (action === "create" || action === "copy") {
        r = await creativeTypesApi.create(
          operation,
          action === "copy" && draft
            ? { ...draft, name: `${draft.name} · 副本` }
            : undefined,
        );
      } else {
        if (!record || !draft) return;
        r = await creativeTypesApi.mutate(
          record.id,
          action,
          record.revision,
          operation,
          action === "disable" ? undefined : draft,
        );
      }
      if (!mounted.current) return;
      accept(r);
      setMobileDetail(true);
      if (action === "create" || action === "copy") {
        setTab("core");
        setSearch("");
        setDimension("all");
        setStatus("all");
      }
      setNotice(
        action === "activate"
          ? "已保存并启用。已有讨论保持原快照。"
          : action === "disable"
            ? "已停用；历史版本仍保留。"
            : "草稿已保存。",
      );
      await refresh();
    } catch (e) {
      if (mounted.current) {
        setError(message(e));
        setConflict(e instanceof CreativeApiError && e.status === 409);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function compareLatest() {
    if (!record) return;
    setBusy(true);
    try {
      const latest = await creativeTypesApi.get(record.id);
      if (!mounted.current) return;
      setRecord(latest);
      setShowComparison(true);
      setError("");
      setConflict(false);
      pending.current = null;
      setNotice(
        `已取得服务端修订 ${latest.revision}，保留你的编辑。请对照最新草稿后决定如何保存。`,
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const shown =
    library?.items.filter(
      (i) =>
        (status === "all" || status === i.status) &&
        (dimension === "all" || dimension === i.dimension) &&
        [i.name, i.summary, ...i.aliases]
          .join(" ")
          .toLowerCase()
          .includes(search.trim().toLowerCase()),
    ) || [];
  return (
    <Scene quiet>
    <div className="workspace-page ct-page">
    <div className="glass-shell ct-shell p-3 sm:p-4">
    <div className={`glass-panel ct-surface ${mobileDetail ? "ct-detail" : ""}`}>
      <header className="ct-page-head">
        <div>
          <span className="ct-eyebrow">创作资料库</span>
          <h1>作品类型维护</h1>
          <p>把读者期待，写成可执行的故事规则。</p>
        </div>
        <div className="ct-actions">
          <Link className="btn-secondary" to="/workshop">
            进入头脑风暴 ↗
          </Link>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() => void mutate("create")}
          >
            ＋ 新建类型
          </button>
        </div>
      </header>
      {error && (
        <div className="ct-error" role="alert">
          {error}
          {conflict && (
            <button
              className="btn-secondary"
              disabled={busy}
              onClick={() => void compareLatest()}
            >
              读取最新修订，保留我的编辑
            </button>
          )}
          {!library && (
            <button
              className="btn-secondary"
              onClick={() => window.location.reload()}
            >
              重新加载
            </button>
          )}
        </div>
      )}
      <div className="ct-workspace">
        <aside className="ct-library">
          <div className="ct-library-tools">
            <input
              aria-label="搜索作品类型"
              placeholder="搜索类型、别名或关键词"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="ct-filters">
              <select
                aria-label="筛选创作维度"
                value={dimension}
                onChange={(e) => setDimension(e.target.value)}
              >
                <option value="all">全部维度</option>
                {Object.entries(library?.dimensions || {}).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <select
                aria-label="筛选状态"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="all">全部状态</option>
                {Object.entries(stateNames).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <small>
              {library ? `${shown.length} 个类型` : "正在读取资料库…"}
            </small>
          </div>
          <div className="ct-type-list">
            {Object.entries(library?.dimensions || {}).map(([key, label]) => {
              const list = shown.filter((i) => i.dimension === key);
              return (
                list.length > 0 && (
                  <section key={key}>
                    <h3>
                      {label}
                      <span>{list.length}</span>
                    </h3>
                    {list.map((i) => (
                      <button
                        key={i.id}
                        disabled={busy}
                        className={`ct-type-item ${record?.id === i.id ? "selected" : ""}`}
                        onClick={() => void choose(i.id)}
                        aria-current={record?.id === i.id ? "true" : undefined}
                      >
                        <span className="ct-row">
                          <strong>{i.name || "未命名类型"}</strong>
                          <span className={`ct-state ${i.status}`}>
                            {stateNames[i.status]}
                          </span>
                        </span>
                        <p>{i.summary || "填写阅读体验与写作说明。"}</p>
                        <small>
                          {i.active_version
                            ? `v${i.active_version}`
                            : "尚未启用"}{" "}
                          ·{" "}
                          {i.coverage === "general_guidance"
                            ? "通用起点"
                            : i.origin === "builtin"
                              ? "内置参考"
                              : "自定义"}
                          {i.has_draft_changes ? " · 有草稿" : ""}
                        </small>
                      </button>
                    ))}
                  </section>
                )
              );
            })}
            {library && !shown.length && (
              <p className="ct-empty">没有匹配的类型。</p>
            )}
          </div>
          <div className="ct-library-foot">
            内置参考均可修改。
            <br />
            新增题材不需要修改程序代码。
          </div>
        </aside>
        <section className="ct-editor" aria-label="作品类型编辑">
          {draft && record ? (
            <>
              <header className="ct-editor-head">
                <button
                  className="ct-mobile-back"
                  onClick={() => setMobileDetail(false)}
                >
                  ← 返回类型列表
                </button>
                <div className="ct-editor-title">
                  <span className="ct-mark">
                    {draft.name.slice(0, 1) || "新"}
                  </span>
                  <div>
                    <h2>
                      {draft.name || "未命名类型"}{" "}
                      <span className={`ct-state ${record.status}`}>
                        {stateNames[record.status]}
                      </span>
                    </h2>
                    <p>
                      {library?.dimensions[draft.dimension]} ·{" "}
                      {active
                        ? `启用内容 v${active.profile.version}`
                        : "尚无启用内容"}
                    </p>
                  </div>
                </div>
                <div className="ct-actions">
                  <button
                    className="btn-ghost"
                    disabled={busy}
                    onClick={() => void mutate("copy")}
                  >
                    复制类型
                  </button>
                  {record.status === "enabled" && (
                    <button
                      className="btn-ghost"
                      disabled={busy}
                      onClick={() => void mutate("disable")}
                    >
                      停用
                    </button>
                  )}
                </div>
              </header>
              <nav className="ct-tabs" role="tablist" aria-label="类型说明标签">
                {(Object.entries(tabs) as [Tab, string][]).map(
                  ([key, label], i) => (
                    <button
                      id={`ct-tab-${key}`}
                      key={key}
                      role="tab"
                      aria-selected={tab === key}
                      aria-controls="ct-panel"
                      tabIndex={tab === key ? 0 : -1}
                      onClick={() => setTab(key)}
                      onKeyDown={(e) => {
                        if (
                          ["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                            e.key,
                          )
                        ) {
                          e.preventDefault();
                          const keys = Object.keys(tabs) as Tab[];
                          const at =
                            e.key === "Home"
                              ? 0
                              : e.key === "End"
                                ? keys.length - 1
                                : (i +
                                    (e.key === "ArrowRight" ? 1 : -1) +
                                    keys.length) %
                                  keys.length;
                          setTab(keys[at]);
                          document
                            .getElementById(`ct-tab-${keys[at]}`)
                            ?.focus();
                        }
                      }}
                    >
                      {label}
                    </button>
                  ),
                )}
              </nav>
              <div
                className="ct-body"
                id="ct-panel"
                role="tabpanel"
                aria-labelledby={`ct-tab-${tab}`}
                key={`${record.id}:${tab}`}
              >
                <fieldset disabled={busy} className="ct-form">
                  {showComparison && (
                    <CreativeProfileDiff
                      before={record.draft}
                      after={draft}
                      beforeLabel="服务端最新草稿"
                      afterLabel="你的当前编辑"
                    />
                  )}
                  {tab === "core" && (
                    <>
                      <div className="ct-section-head">
                        <h3>让读者知道，为什么要读这类故事</h3>
                        <p>
                          先定义期待的体验，再让模型结合本书人物提出不同方案。
                        </p>
                      </div>
                      <div className="ct-fields">
                        <TextField
                          label="类型名称"
                          value={draft.name}
                          onChange={(v) => update("name", v)}
                        />
                        <label className="ct-field">
                          <span>创作维度</span>
                          <select
                            value={draft.dimension}
                            onChange={(e) =>
                              update("dimension", e.target.value)
                            }
                          >
                            {Object.entries(library?.dimensions || {}).map(
                              ([k, v]) => (
                                <option key={k} value={k}>
                                  {v}
                                </option>
                              ),
                            )}
                          </select>
                        </label>
                      </div>
                      <TextField
                        label="一句话说明"
                        value={draft.summary}
                        onChange={(v) => update("summary", v)}
                        multiline
                      />

                      <section className="ct-block">
                        <h3>
                          关联 H5 题材 ·{" "}
                          {draft.publication_mapping.catalog_version}
                        </h3>
                        <p>
                          与创建小说使用相同的分类。勾选此资料适用的题材；本书最终分类由作者在创建时选择。
                        </p>
                        <div className="ct-mapping">
                          {Object.entries(
                            library?.publication_catalog.axes || {},
                          ).map(([axis, items]) => <fieldset className="ct-mapping-group" key={axis}><legend>{({ primary_genres: '主类型', story_types: '故事类型', tones: '情绪与风格', settings: '故事背景' } as Record<string, string>)[axis] || axis}</legend><div className="ct-mapping">{items.map((item) => {
                              const key =
                                axis === "primary_genres"
                                  ? "primary_genre_ids"
                                  : "facet_ids";
                              return (
                                <label key={item.id}>
                                  <input
                                    type="checkbox"
                                    checked={draft.publication_mapping[
                                      key
                                    ].includes(item.id)}
                                    onChange={(e) => {
                                      const values = e.target.checked
                                        ? [
                                            ...draft.publication_mapping[key],
                                            item.id,
                                          ]
                                        : draft.publication_mapping[key].filter(
                                            (v) => v !== item.id,
                                          );
                                      const next = {
                                        ...draft.publication_mapping,
                                        [key]: values,
                                      };
                                      update("publication_mapping", {
                                        ...next,
                                        status:
                                          next.primary_genre_ids.length +
                                          next.facet_ids.length
                                            ? "mapped"
                                            : "unmapped",
                                      });
                                    }}
                                  />
                                  {item.labels["zh-CN"] || item.id}
                                </label>
                              );
                            })}</div></fieldset>)}
                        </div>
                        <p className="ct-hint">
                          未映射的自定义类型可以参与创作。创作 ID
                          不会写入发布分类；正式交付仍使用现有字典与校验规则。
                        </p>
                      </section>
                      {group("reader_promises", "读者期待")}
                      <details className="ct-block ct-advanced">
                        <summary>别名、适用范围与读者限制 · 可选</summary>
                      <LinesField
                        label="别名"
                        value={draft.aliases}
                        onChange={(v) => update("aliases", v)}
                      />
                        <div className="ct-fields">
                          {(
                            [
                              ["readers", "目标读者"],
                              ["markets", "发行市场"],
                              ["languages", "作品语言"],
                              ["lengths", "篇幅或形式"],
                            ] as const
                          ).map(([key, label]) => (
                            <LinesField
                              key={`${record.revision}:${key}`}
                              label={label}
                              value={draft.applicability[key]}
                              onChange={(v) =>
                                update("applicability", {
                                  ...draft.applicability,
                                  [key]: v,
                                })
                              }
                            />
                          ))}
                        </div>
                      </details>
                      <p className="ct-hint">
                        类型资料是创作起点。常见机制需要落实为本书人物的具体选择，才能成为故事设定。
                      </p>
                    </>
                  )}
                  {tab === "mechanisms" && (
                    <>
                      <div className="ct-section-head">
                        <h3>让类型真正改变人物的选择</h3>
                        <p>
                          规则、限制、代价、选择与回报互相连接；不只是增加类型关键词。
                        </p>
                      </div>
                      {draft.mechanisms.map((m, i) => (
                        <details
                          className="ct-mechanism"
                          key={m.id}
                          open={undefined}
                        >
                          <summary>
                            {m.name || "新故事机制"}
                            <small>{priorities[m.importance]}</small>
                          </summary>
                          <div className="ct-mechanism-body">
                            <TextField
                              label="机制名称"
                              value={m.name}
                              onChange={(v) =>
                                update(
                                  "mechanisms",
                                  draft.mechanisms.map((x, j) =>
                                    i === j ? { ...x, name: v } : x,
                                  ),
                                )
                              }
                            />
                            <TextField
                              label="核心作用"
                              value={m.principle}
                              onChange={(v) =>
                                update(
                                  "mechanisms",
                                  draft.mechanisms.map((x, j) =>
                                    i === j ? { ...x, principle: v } : x,
                                  ),
                                )
                              }
                              multiline
                            />
                            <label className="ct-field">
                              <span>重要性</span>
                              <select
                                value={m.importance}
                                onChange={(e) =>
                                  update(
                                    "mechanisms",
                                    draft.mechanisms.map((x, j) =>
                                      i === j
                                        ? {
                                            ...x,
                                            importance: e.target
                                              .value as Mechanism["importance"],
                                          }
                                        : x,
                                    ),
                                  )
                                }
                              >
                                {Object.entries(priorities).map(([k, v]) => (
                                  <option key={k} value={k}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <div className="ct-fields">
                              {(
                                [
                                  ["trigger", "触发条件"],
                                  ["limits", "限制"],
                                  ["costs", "代价"],
                                  ["choices", "人物选择"],
                                  ["payoffs", "回报"],
                                ] as const
                              ).map(([key, label]) => (
                                <LinesField
                                  key={key}
                                  label={label}
                                  value={m[key]}
                                  onChange={(v) =>
                                    update(
                                      "mechanisms",
                                      draft.mechanisms.map((x, j) =>
                                        i === j ? { ...x, [key]: v } : x,
                                      ),
                                    )
                                  }
                                />
                              ))}
                            </div>
                            <details className="ct-advanced"><summary>更多边界与适用阶段</summary><div className="ct-fields">
                              {([['scope', '作用范围'], ['exceptions', '例外']] as const).map(([key,label]) => <LinesField key={key} label={label} value={m[key]} onChange={v => update('mechanisms', draft.mechanisms.map((x,j) => i === j ? { ...x, [key]: v } : x))} />)}
                            </div>
                            <StagePicker
                              value={m.stages}
                              onChange={(v) =>
                                update(
                                  "mechanisms",
                                  draft.mechanisms.map((x, j) =>
                                    i === j ? { ...x, stages: v } : x,
                                  ),
                                )
                              }
                            />
                            </details>
                            <button
                              className="btn-ghost"
                              onClick={() =>
                                update(
                                  "mechanisms",
                                  draft.mechanisms.filter((_, j) => i !== j),
                                )
                              }
                            >
                              移除此机制
                            </button>
                          </div>
                        </details>
                      ))}
                      <button
                        className="ct-text-button"
                        disabled={draft.mechanisms.length >= 8}
                        onClick={() =>
                          update("mechanisms", [
                            ...draft.mechanisms,
                            {
                              id: uid(),
                              name: "",
                              principle: "",
                              importance: "recommended",
                              stages: [...stages],
                              trigger: [],
                              scope: [],
                              limits: [],
                              exceptions: [],
                              costs: [],
                              choices: [],
                              payoffs: [],
                            },
                          ])
                        }
                      >
                        ＋ 添加故事机制
                      </button>
                    </>
                  )}
                  {tab === "checks" && (
                    <>
                      <div className="ct-section-head"><h3>让人物与文字服务故事</h3><p>检查提示用于讨论和修订，不是强制剧情或文学评分门。</p></div>
                      {group("character_dynamics", "人物与关系")}
                      {group("voice_guidance", "文风偏好")}
                      {group("pitfalls", "避免的问题")}
                      {group("diagnostics", "设计检查")}
                      <details className="ct-block ct-advanced">
                        <summary>多方向组合说明 · 可选</summary>
                        <p>只有本书同时选择对应类型时才读取这些说明。</p>
                        {draft.combinations.map((c, i) => (
                          <div
                            className="ct-mechanism-body"
                            key={`${i}:${c.with_profile_id}`}
                          >
                            <label className="ct-field">
                              <span>合作类型</span>
                              <select
                                value={c.with_profile_id}
                                onChange={(e) =>
                                  update(
                                    "combinations",
                                    draft.combinations.map((v, j) =>
                                      i === j
                                        ? {
                                            ...v,
                                            with_profile_id: e.target.value,
                                          }
                                        : v,
                                    ),
                                  )
                                }
                              >
                                {library?.items
                                  .filter((v) => v.id !== record.id)
                                  .map((v) => (
                                    <option key={v.id} value={v.id}>
                                      {v.name || "未命名类型"}
                                    </option>
                                  ))}
                              </select>
                            </label>
                            <div className="ct-fields">
                              <LinesField
                                label="可以强化什么"
                                value={c.opportunities}
                                onChange={(v) =>
                                  update(
                                    "combinations",
                                    draft.combinations.map((x, j) =>
                                      i === j ? { ...x, opportunities: v } : x,
                                    ),
                                  )
                                }
                              />
                              <LinesField
                                label="需要协调什么"
                                value={c.tensions}
                                onChange={(v) =>
                                  update(
                                    "combinations",
                                    draft.combinations.map((x, j) =>
                                      i === j ? { ...x, tensions: v } : x,
                                    ),
                                  )
                                }
                              />
                            </div>
                            <button
                              className="btn-ghost"
                              onClick={() =>
                                update(
                                  "combinations",
                                  draft.combinations.filter((_, j) => j !== i),
                                )
                              }
                            >
                              移除组合
                            </button>
                          </div>
                        ))}
                        <button
                          className="ct-text-button"
                          disabled={draft.combinations.length >= 12}
                          onClick={() => {
                            const other = library?.items.find(
                              (i) =>
                                i.id !== record.id &&
                                !draft.combinations.some(
                                  (c) => c.with_profile_id === i.id,
                                ),
                            );
                            if (other)
                              update("combinations", [
                                ...draft.combinations,
                                {
                                  with_profile_id: other.id,
                                  opportunities: [],
                                  tensions: [],
                                },
                              ]);
                          }}
                        >
                          ＋ 添加组合说明
                        </button>
                      </details>

                    </>
                  )}
                  {tab === "preview" && (
                    <>
                      <div className="ct-section-head">
                        <h3>模型会读到什么</h3>
                        <p>
                          这里是本地编译预览，不调用模型。实际讨论保留自己的版本快照。
                        </p>
                      </div>
                      <div className="ct-preview-controls">
                        <div>
                          {(["active", "draft"] as const).map((mode) => (
                            <button
                              className={`btn-secondary ${previewMode === mode ? "selected" : ""}`}
                              key={mode}
                              aria-pressed={previewMode === mode}
                              onClick={() => setPreviewMode(mode)}
                            >
                              {mode === "active"
                                ? "当前启用版本"
                                : "预览当前草稿"}
                            </button>
                          ))}
                        </div>
                        <select
                          aria-label="预览适用阶段"
                          value={previewStage}
                          onChange={(e) =>
                            setPreviewStage(e.target.value as CreativeStage)
                          }
                        >
                          {Object.entries(stageNames).map(([k, v]) => (
                            <option key={k} value={k}>
                              {v}
                            </option>
                          ))}
                        </select>
                      </div>
                      {previewError ? (
                        <p className="ct-error" role="alert">
                          {previewError}
                        </p>
                      ) : preview ? (
                        <>
                          <p className="ct-hint">
                            {previewMode === "draft"
                              ? "草稿尚未启用、未发送给模型。"
                              : record.status === "disabled"
                                ? "此版本已停用；仅供历史查看。"
                                : "新讨论可使用此版本；已有讨论不会自动更新。"}{" "}
                            {preview.bytes.toLocaleString()} 字节（不是 token）
                          </p>
                          {preview.context.profiles.map((p) => (
                            <article
                              className="ct-preview-paper"
                              key={p.profile_id}
                            >
                              <h3>{p.content.name}</h3>
                              <p>{p.content.summary}</p>
                              {(
                                [
                                  "reader_promises",
                                  "character_dynamics",
                                  "voice_guidance",
                                  "pitfalls",
                                  "diagnostics",
                                ] as const
                              ).map((k) => (
                                <ul key={k}>
                                  {p.content[k].map((g) => (
                                    <li key={g.id}>{g.text}</li>
                                  ))}
                                </ul>
                              ))}
                              {p.content.mechanisms.map((m) => (
                                <div key={m.id}>
                                  <h4>{m.name}</h4>
                                  <p>{m.principle}</p>
                                  <ul>
                                    {[
                                      ...m.costs,
                                      ...m.choices,
                                      ...m.payoffs,
                                    ].map((v, i) => (
                                      <li key={i}>{v}</li>
                                    ))}
                                  </ul>
                                </div>
                              ))}
                            </article>
                          ))}
                          <details className="ct-technical">
                            <summary>查看结构数据和哈希</summary>
                            <small>编译内容 SHA-256：{preview.sha256}</small>
                            <pre>
                              {JSON.stringify(preview.context, null, 2)}
                            </pre>
                          </details>
                        </>
                      ) : (
                        <p className="ct-empty">正在编译预览…</p>
                      )}
                      <section className="ct-block">
                        <h3>启用版本历史</h3>
                        <p>恢复只建立草稿，启用时生成新版本，不改写历史。</p>
                        {[...record.versions].reverse().map((v) => (
                          <div className="ct-version" key={v.profile.version}>
                            <div>
                              v{v.profile.version} · {v.profile.name}
                              <small>
                                {new Date(v.created_at).toLocaleString()}
                              </small>
                            </div>
                            <button
                              className="btn-secondary"
                              onClick={() => {
                                if (
                                  dirty &&
                                  !window.confirm(
                                    "用此版本替换当前未保存的草稿？",
                                  )
                                )
                                  return;
                                setDraft(structuredClone(v.profile));
                                setPreviewMode("draft");
                                setNotice("历史内容已复制为草稿，尚未启用。");
                              }}
                            >
                              恢复为草稿
                            </button>
                          </div>
                        ))}
                      </section>
                    </>
                  )}
                </fieldset>
              </div>
              <footer className="ct-savebar">
                <div>
                  <strong>
                    {busy
                      ? "正在保存或读取…"
                      : dirty
                        ? "有未保存的修改"
                        : notice || "资料已保存"}
                  </strong>
                  <p>草稿不影响启用版本；已有讨论保持原快照。</p>
                </div>
                <div className="ct-actions">
                  <button
                    className="btn-secondary"
                    disabled={busy || !dirty || conflict}
                    onClick={() => void mutate("draft")}
                  >
                    保存草稿
                  </button>
                  <button
                    className="btn-primary"
                    disabled={busy || conflict}
                    onClick={() => void mutate("activate")}
                  >
                    保存并启用 ↗
                  </button>
                </div>
              </footer>
            </>
          ) : (
            <p className="ct-empty">
              {library
                ? "选择或新建一个类型，开始维护写作说明。"
                : "正在读取资料…"}
            </p>
          )}
        </section>
      </div>
    </div>
    </div>
    </div>
    </Scene>
  );
}
