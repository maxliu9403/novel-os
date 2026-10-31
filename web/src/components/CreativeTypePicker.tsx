import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  creativeTypesApi,
  creativeRef,
  matchesCreativeLabel,
  type CreativeLibrary,
  type CreativeTypeRef,
  type CreativeSummary,
} from "../api/creativeTypes";

export default function CreativeTypePicker({
  selected,
  refs,
  onChange,
  other,
  onOtherChange,
}: {
  selected: string[];
  refs?: CreativeTypeRef[] | null;
  onChange: (genres: string[], refs: CreativeTypeRef[]) => void;
  other: string;
  onOtherChange: (v: string) => void;
}) {
  const [library, setLibrary] = useState<CreativeLibrary | null>(null),
    [error, setError] = useState(""),
    [refresh, setRefresh] = useState(0),
    [search, setSearch] = useState("");
  useEffect(() => {
    let live = true;
    creativeTypesApi
      .list()
      .then((l) => {
        if (live) {
          setLibrary(l);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [refresh]);
  const available = library?.items.filter((i) => i.status === "enabled") || [];
  const inferred =
    refs ??
    available
      .filter((i) =>
        selected.some(
          (s) =>
            matchesCreativeLabel(i, s) &&
            available.filter((c) => matchesCreativeLabel(c, s)).length === 1,
        ),
      )
      .map((i) => creativeRef(i));
  const chosen = new Set(inferred.map((r) => r.profile_id));
  function toggle(item: CreativeSummary) {
    const next = chosen.has(item.id)
      ? inferred.filter((r) => r.profile_id !== item.id)
      : [...inferred, creativeRef(item)];
    const labels = selected.filter(
      (label) => !matchesCreativeLabel(item, label),
    );
    onChange(chosen.has(item.id) ? labels : [...labels, item.name], next);
  }
  const unmatched = selected.filter(
    (label) => !available.some((i) => matchesCreativeLabel(i, label)),
  );
  const shown = available.filter((i) =>
    [i.name, i.summary, ...i.aliases]
      .join(" ")
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-ink-muted">
        <span>读取已启用的写作说明；创建讨论时冻结版本。</span>
        <Link
          to="/creative-types"
          target="_blank"
          rel="noopener"
          className="text-violet"
        >
          维护作品类型 ↗
        </Link>
      </div>
      {error ? (
        <p
          role="alert"
          className="rounded-xl bg-rose-50 p-3 text-[12px] text-rose-700"
        >
          类型资料暂时无法读取：{error}{" "}
          <button type="button" onClick={() => setRefresh((v) => v + 1)}>
            重试
          </button>
        </p>
      ) : !library ? (
        <p className="text-[12px] text-ink-muted">正在读取类型资料…</p>
      ) : (
        <>
          <input
            className="w-full rounded-xl border border-paper-line bg-white/70 px-3 py-2 text-[12px]"
            aria-label="搜索可用作品类型"
            placeholder="搜索类型、主题或别名"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="max-h-72 space-y-3 overflow-y-auto pr-1">
            {Object.entries(library.dimensions).map(([key, label]) => {
              const items = shown.filter((i) => i.dimension === key);
              return (
                items.length > 0 && (
                  <fieldset key={key}>
                    <legend className="mb-2 text-[11.5px] text-ink-muted">
                      {label}
                    </legend>
                    <div className="flex flex-wrap gap-1.5">
                      {items.map((i) => (
                        <button
                          key={i.id}
                          type="button"
                          title={`${i.summary} · v${i.active_version}`}
                          aria-pressed={chosen.has(i.id)}
                          onClick={() => toggle(i)}
                          className={`rounded-full px-3 py-1.5 text-[12px] ${chosen.has(i.id) ? "bg-violet text-white shadow-sm" : "border border-paper-line bg-white/60 text-ink-muted"}`}
                        >
                          {i.name}
                        </button>
                      ))}
                    </div>
                  </fieldset>
                )
              );
            })}
            {!shown.length && (
              <p className="text-[12px] text-ink-muted">
                没有匹配的已启用类型。
              </p>
            )}
          </div>
        </>
      )}
      {inferred.length > 0 && (
        <details className="rounded-xl border border-paper-line bg-white/50 p-3 text-[12px]">
          <summary className="cursor-pointer text-violet">
            已选类型与版本 · {inferred.length}
          </summary>
          <div className="mt-3 space-y-2">
            {inferred.map((ref) => {
              const item = library?.items.find((i) => i.id === ref.profile_id);
              return (
                <div
                  className="flex flex-wrap items-center justify-between gap-2"
                  key={ref.profile_id}
                >
                  <span>
                    {item?.name || ref.profile_id} · v{ref.version}
                    {item?.status === "disabled" ? " · 已停用" : ""}
                  </span>
                  <select
                    className="rounded-lg border border-paper-line bg-white px-2 py-1"
                    aria-label={`${item?.name || ref.profile_id}在本书的作用`}
                    value={ref.role}
                    onChange={(e) =>
                      onChange(
                        selected,
                        inferred.map((r) =>
                          r.profile_id === ref.profile_id
                            ? {
                                ...r,
                                role: e.target.value as CreativeTypeRef["role"],
                              }
                            : r,
                        ),
                      )
                    }
                  >
                    <option value="unspecified">尚未指定作用</option>
                    <option value="primary">主导故事</option>
                    <option value="supporting">辅助情节</option>
                    <option value="atmosphere">氛围与表达</option>
                  </select>
                  <button
                    type="button"
                    className="text-ink-muted"
                    aria-label={`移除已选类型 ${item?.name || ref.profile_id}`}
                    onClick={() =>
                      onChange(
                        item
                          ? selected.filter(
                              (label) => !matchesCreativeLabel(item, label),
                            )
                          : selected,
                        inferred.filter((r) => r.profile_id !== ref.profile_id),
                      )
                    }
                  >
                    移除
                  </button>
                </div>
              );
            })}
          </div>
        </details>
      )}
      {unmatched.length > 0 && (
        <div className="flex flex-wrap gap-2 text-[12px] text-ink-muted">
          <span>已带入的自定义类型：</span>
          {unmatched.map((g) => (
            <button
              key={g}
              type="button"
              aria-label={`移除类型 ${g}`}
              className="rounded-full bg-violet-soft px-3 py-1 text-violet"
              onClick={() =>
                onChange(
                  selected.filter((v) => v !== g),
                  inferred,
                )
              }
            >
              {g} ×
            </button>
          ))}
        </div>
      )}
      <label className="block text-[12px] text-ink-muted">
        其他类型
        <input
          aria-label="其他类型"
          className="mt-2 w-full rounded-xl border border-paper-line bg-white/60 px-3 py-2"
          value={other}
          onChange={(e) => onOtherChange(e.target.value)}
          placeholder="尚未建立说明的类型，可先用文字补充"
        />
      </label>
    </div>
  );
}
