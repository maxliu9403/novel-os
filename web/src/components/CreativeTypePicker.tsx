import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { knowledgeExcerpt } from "../lib/knowledgeExcerpt";
import {
  creativeTypesApi,
  creativeRef,
  type CreativeSummary,
  type CreativeTypeRef,
} from "../api/creativeTypes";
import "../styles/creative-types.css";

export default function CreativeTypePicker({
  refs,
  onChange,
}: {
  refs?: CreativeTypeRef[] | null;
  onChange: (names: string[], refs: CreativeTypeRef[]) => void;
}) {
  const [items, setItems] = useState<CreativeSummary[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const [pendingGenre, setPendingGenre] = useState<CreativeSummary | null>(
    null,
  );
  useEffect(() => {
    let live = true;
    creativeTypesApi
      .list()
      .then((data) => {
        if (live) setItems(data.items.filter((i) => i.status === "enabled"));
      })
      .catch((e) => {
        if (live) setError(String(e));
      })
      .finally(() => {
        if (live) setLoaded(true);
      });
    return () => {
      live = false;
    };
  }, []);
  const selected = refs ?? [];
  const genre = selected.find((r) => r.id.startsWith("genre."));
  const tags = items.filter(
    (i) => i.kind === "tag" && genre && i.genre_ids.includes(genre.id),
  );
  const incompatibleWith = (item: CreativeSummary) =>
    selected.filter(
      (r) =>
        r.id.startsWith("tag.") &&
        !items.some((i) => i.id === r.id && i.genre_ids.includes(item.id)),
    );
  function choose(item: CreativeSummary, confirmed = false) {
    setError("");
    if (item.kind === "genre" && !confirmed && incompatibleWith(item).length) {
      setPendingGenre(item);
      return;
    }
    setPendingGenre(null);
    let next: CreativeTypeRef[];
    if (item.kind === "genre") {
      next = [
        creativeRef(item),
        ...selected.filter(
          (r) =>
            r.id.startsWith("tag.") &&
            items.some((i) => i.id === r.id && i.genre_ids.includes(item.id)),
        ),
      ];
    } else {
      if (
        !selected.some((r) => r.id === item.id) &&
        selected.filter((r) => r.id.startsWith("tag.")).length >= 20
      ) {
        setError("最多选择 20 个 Tag。");
        return;
      }
      next = selected.some((r) => r.id === item.id)
        ? selected.filter((r) => r.id !== item.id)
        : [...selected, creativeRef(item)];
    }
    onChange(
      next.map((r) => items.find((i) => i.id === r.id)?.name ?? r.id),
      next,
    );
  }
  return (
    <section className="knowledge-picker">
      <div className="knowledge-heading">
        <h3>选择 Genre</h3>
        <Link to="/creative-types" target="_blank">
          维护写作资料 ↗
        </Link>
      </div>
      {error && <p role="alert">{error}</p>}
      <div
        className="knowledge-choices"
        role="radiogroup"
        aria-label="Genre 主类型"
      >
        {items
          .filter((i) => i.kind === "genre")
          .map((i) => (
            <button
              type="button"
              role="radio"
              aria-checked={genre?.id === i.id}
              key={i.id}
              onClick={() => choose(i)}
              className={genre?.id === i.id ? "selected" : ""}
            >
              {i.name}
            </button>
          ))}
      </div>
      {pendingGenre && (
        <div
          className="knowledge-switch-confirm"
          role="group"
          aria-label="确认更换主类型"
        >
          <p>
            改为 {pendingGenre.name} 后，将移除不适用的标签：
            {incompatibleWith(pendingGenre)
              .map((r) => items.find((i) => i.id === r.id)?.name ?? r.id)
              .join("、")}
            。
          </p>
          <div className="knowledge-actions">
            <button type="button" onClick={() => choose(pendingGenre, true)}>
              确认更换
            </button>
            <button type="button" onClick={() => setPendingGenre(null)}>
              保留当前选择
            </button>
          </div>
        </div>
      )}
      {!loaded && !error && <p>正在读取资料…</p>}
      {loaded && !items.some((i) => i.kind === "genre") && !error && (
        <p>尚无已启用的 Genre，请先维护写作资料。</p>
      )}
      <div className="knowledge-heading">
        <h3>
          组合 Tags <small>至少选择一个</small>
        </h3>
        <span>
          {selected.filter((r) => r.id.startsWith("tag.")).length} 个已选
        </span>
      </div>
      {genre ? (
        <>
          <input
            aria-label="搜索关联标签"
            className="knowledge-search"
            placeholder="搜索标签或别名"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="knowledge-choices">
            {tags
              .filter((i) =>
                [i.name, ...i.aliases].some((s) =>
                  s.toLowerCase().includes(search.toLowerCase()),
                ),
              )
              .map((i) => (
                <button
                  key={i.id}
                  type="button"
                  aria-pressed={selected.some((r) => r.id === i.id)}
                  className={
                    selected.some((r) => r.id === i.id) ? "selected" : ""
                  }
                  onClick={() => choose(i)}
                >
                  {i.name}
                </button>
              ))}
          </div>
          {!tags.length && (
            <p>此 Genre 尚无已启用的关联 Tag，请先维护写作资料。</p>
          )}
          <div className="knowledge-promises">
            {selected.map((r) => {
              const item = items.find((i) => i.id === r.id);
              return (
                item && (
                  <details
                    key={r.id}
                    open={detail === r.id}
                    onToggle={(e) => {
                      if (e.currentTarget.open) setDetail(r.id);
                    }}
                  >
                    <summary>
                      {item.name} <span>如何影响故事</span>
                    </summary>
                    <p>{knowledgeExcerpt(item.promise)}</p>
                  </details>
                )
              );
            })}
          </div>
        </>
      ) : (
        <p className="knowledge-muted">先选择主类型，再组合相关标签。</p>
      )}
    </section>
  );
}
