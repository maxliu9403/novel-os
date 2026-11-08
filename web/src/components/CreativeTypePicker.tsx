import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { knowledgeExcerpt } from "../lib/knowledgeExcerpt";
import { AUDIENCE_CHANNELS, AUDIENCE_LABELS, supportsAudience } from "../lib/audience";
import { useConfirm } from "./confirmContext";
import { creativeTypesApi, creativeRef, isCreativeTypeRef, type AudienceChannel, type CreativeSummary, type CreativeTypeRef } from "../api/creativeTypes";
import "../styles/creative-types.css";

export default function CreativeTypePicker({ refs, channel, legacy = false, legacyNames = [], onChange }: {
  refs?: unknown[] | null;
  channel?: AudienceChannel | null;
  legacy?: boolean;
  legacyNames?: string[];
  onChange: (names: string[], refs: CreativeTypeRef[], channel?: AudienceChannel | null) => void;
}) {
  const confirm = useConfirm();
  const [items, setItems] = useState<CreativeSummary[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [search, setSearch] = useState("");
  const [switching, setSwitching] = useState(false);
  useEffect(() => {
    let live = true;
    creativeTypesApi.list().then(data => { if (live) { setItems(data.items); setLoaded(true); } })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [loadAttempt]);
  const selected = (refs ?? []).filter(isCreativeTypeRef);
  const historical = (refs?.length ?? 0) !== selected.length;
  const genre = selected.find(r => r.id.startsWith("genre."));
  const tagCount = selected.filter(r => r.id.startsWith("tag.")).length;
  const maximum = 2;
  const available = items.filter(i => !!channel && supportsAudience(i, channel));
  const genres = available.filter(i => i.kind === "genre");
  const tags = available.filter(i => i.kind === "tag");
  function update(next: CreativeTypeRef[], nextChannel = channel) {
    onChange(next.map(r => items.find(i => i.id === r.id)?.name ?? r.id), next, nextChannel);
  }
  async function chooseChannel(next: AudienceChannel) {
    if (next === channel || switching || !loaded) return;
    const incompatible = selected.filter(ref => !items.some(item => item.id === ref.id && supportsAudience(item, next)));
    setSwitching(true);
    try {
      if (historical && !(await confirm({
        title: "使用新版频道与题材？",
        message: "本讨论使用旧版创作资料。升级后请重新选择一个 Genre 与一至两个 Tags；不会猜测旧资料的对应关系。故事构想、来源资料和其他设定会保留，保存前可取消修改。",
        confirmLabel: "选择新版题材",
      }))) return;
      if (incompatible.length && !(await confirm({
        title: `切换到${AUDIENCE_LABELS[next]}？`,
        message: `将移除当前频道不可用的资料：${incompatible.map(ref => items.find(item => item.id === ref.id)?.name ?? ref.id).join("、")}。故事构想、来源资料和其他设定会保留。`,
        confirmLabel: "切换频道",
      }))) return;
      const compatible = selected.filter(ref => !incompatible.some(item => item.id === ref.id));
      // Changing channel selects the currently enabled guide for that channel;
      // a restored reference may predate its availability.
      update(compatible.map(ref => creativeRef(items.find(item => item.id === ref.id)!)), next);
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally { setSwitching(false); }
  }
  function choose(item: CreativeSummary) {
    if (switching) return;
    setError("");
    if (item.kind === "genre") {
      update([creativeRef(item), ...selected.filter(ref => ref.id.startsWith("tag."))]);
      return;
    }
    if (selected.some(ref => ref.id === item.id)) update(selected.filter(ref => ref.id !== item.id));
    else if (tagCount < maximum) update([...selected, creativeRef(item)]);
  }
  return <section className="knowledge-picker">
    <>
      <div className="knowledge-heading"><h3>读者频道 <small>必选一项</small></h3><Link to="/creative-types" target="_blank">维护写作资料 ↗</Link></div>
      <div className="knowledge-choices knowledge-channel-choices" role="radiogroup" aria-label="读者频道">
        {AUDIENCE_CHANNELS.map(value => <button key={value} type="button" role="radio" aria-checked={channel === value}
          disabled={!loaded || switching} onClick={() => void chooseChannel(value)} className={channel === value ? "selected" : ""}>{AUDIENCE_LABELS[value]}</button>)}
      </div>
      <p className="knowledge-muted">选择本书的读者定位；人物性别、视角与关系由你的故事决定。</p>
    </>
    {legacy && !channel && <p className="knowledge-muted">本讨论保留原有资料选择。要修改 Genre 或 Tags，请先选择频道，再确认一个 Genre 与一至两个 Tags。</p>}
    {historical && <div className="knowledge-promises"><strong>历史创作资料 · 只读</strong>
      <p>{legacyNames.length ? legacyNames.join(" · ") : "此讨论使用旧版创作资料。"}</p>
      <details><summary>查看已保存的版本记录</summary><pre>{JSON.stringify(refs, null, 2)}</pre></details>
    </div>}
    {error && <div role="alert" className="knowledge-error"><p>{error}</p>{!loaded && <button type="button" className="btn-secondary" onClick={() => { setError(""); setLoadAttempt(value => value + 1); }}>重新读取资料</button>}</div>}
    {!loaded && !error && <p role="status">正在读取写作资料…</p>}
    {loaded && !channel && <p className="knowledge-picker-empty">先选择频道，再选择 Genre 与 Tags。</p>}
    {channel && <>
      <div className="knowledge-heading"><h3>Genre <small>选择一个主类型</small></h3></div>
      <div className="knowledge-choices" role="radiogroup" aria-label="Genre 主类型">
        {genres.map(item => <button type="button" role="radio" aria-checked={genre?.id === item.id} key={item.id}
          disabled={switching} onClick={() => choose(item)} className={genre?.id === item.id ? "selected" : ""}>{item.name}</button>)}
      </div>
      {loaded && !genres.length && <p className="knowledge-muted">当前频道没有已启用且指导完整的 Genre，请先维护写作资料。</p>}
      <div className="knowledge-heading"><h3>Tags <small>选择一至两个</small></h3><span aria-live="polite">已选 {tagCount} / {maximum}</span></div>
      <input aria-label="搜索标签" className="knowledge-search" placeholder="搜索标签或别名" value={search} onChange={e => setSearch(e.target.value)} />
      <div className="knowledge-choices" aria-label="创作标签">
        {tags.filter(item => [item.name, ...item.aliases].some(value => value.toLowerCase().includes(search.toLowerCase()))).map(item => {
          const checked = selected.some(ref => ref.id === item.id);
          return <button type="button" key={item.id} aria-pressed={checked} className={checked ? "selected" : ""}
            disabled={switching || (!checked && tagCount >= maximum)} onClick={() => choose(item)}>{item.name}</button>;
        })}
      </div>
      {tagCount >= maximum && <p className="knowledge-muted">{tagCount > maximum ? `保留了 ${tagCount} 个原有 Tags，请取消至一至两个后继续。` : `最多选择 ${maximum} 个 Tags，可先取消一项。`}</p>}
      {loaded && !tags.length && <p className="knowledge-muted">当前频道没有可用 Tags，请先补充并启用频道指导。</p>}
      {tags.length > 0 && !tags.some(item => [item.name, ...item.aliases].some(value => value.toLowerCase().includes(search.toLowerCase()))) && <p className="knowledge-muted">没有匹配的标签。</p>}
    </>}
    <div className="knowledge-promises">{selected.map(ref => {
      const item = items.find(i => i.id === ref.id);
      const currentVersion = item?.active_version === ref.version && item.source_sha256 === ref.source_sha256;
      const unavailable = loaded && channel && (!item || !supportsAudience(item, channel));
      return <details key={ref.id}><summary>{item?.name ?? ref.id}<span>如何影响故事</span></summary>
        <p>{item && currentVersion ? knowledgeExcerpt(item.promise) : `使用本讨论已保存的资料版本 v${ref.version}，当前资料说明可能已更新。`}</p>
        {unavailable && <><p>此资料当前不可选；已保存的讨论仍保留原版本。</p><button type="button" className="btn-secondary" disabled={switching} onClick={() => update(selected.filter(value => value.id !== ref.id))}>移除{item?.name ?? ref.id}</button></>}
      </details>;
    })}</div>
  </section>;
}
