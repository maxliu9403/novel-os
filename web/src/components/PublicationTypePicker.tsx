import { useEffect, useState } from 'react';
import { creativeTypesApi, type CreativeLibrary } from '../api/creativeTypes';
import type { BookClassificationSelection } from '../api/workshop';

const empty: BookClassificationSelection = { catalog_version: 'novel-types.2026-10', primary_genre_id: '', secondary_genre_ids: [], story_type_ids: [], tone_ids: [], setting_ids: [], audience_channel: 'general' };
const groups = [
  ['primary_genres', 'primary_genre_id', '主类型', '必选 1 项'],
  ['story_types', 'story_type_ids', '故事类型', '最多 3 项'],
  ['tones', 'tone_ids', '情绪与风格', '最多 3 项'],
  ['settings', 'setting_ids', '故事背景', '最多 2 项'],
] as const;

export default function PublicationTypePicker({ value, onChange, required }: {
  value?: BookClassificationSelection | null; onChange: (value: BookClassificationSelection) => void; required: boolean;
}) {
  const [library, setLibrary] = useState<CreativeLibrary | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    creativeTypesApi.list().then(l => { if (alive) { setLibrary(l); setError(''); } }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [reload]);
  const selected = value ?? empty;
  const catalog = library?.publication_catalog;
  function toggle(key: 'story_type_ids' | 'tone_ids' | 'setting_ids' | 'secondary_genre_ids', id: string, max: number) {
    const ids = selected[key];
    if (!ids.includes(id) && ids.length >= max) return;
    onChange({ ...selected, [key]: ids.includes(id) ? ids.filter(v => v !== id) : [...ids, id] });
  }
  return <section className="mb-6 rounded-2xl border border-paper-line bg-white/60 p-4 sm:p-5" aria-label="题材与发行分类">
    <h3 className="text-[16px] font-semibold">01 · 题材与发行分类 <span className="ml-2 text-[13px] font-normal text-violet">{required ? '先选择题材' : '旧讨论可保留原分类方式'}</span></h3>
    <p className="mb-4 mt-2 text-[13px] leading-relaxed text-ink-muted">与 H5 共用分类字典。这里只决定本书题材，写作指导在下一步选择。{catalog && ` · ${catalog.catalog_version}`}</p>
    {error ? <p role="alert" className="text-rose-700">分类读取失败：{error} <button type="button" onClick={() => setReload(v => v + 1)}>重试</button></p> : !catalog ? <p role="status">正在读取 H5 分类…</p> : <div className="space-y-4">
      {groups.map(([axis, key, title, hint]) => {
        const maximum = key === 'story_type_ids' ? catalog.combination_rules?.story_type_max ?? 3 : key === 'tone_ids' ? catalog.combination_rules?.tone_max ?? 3 : catalog.combination_rules?.setting_max ?? 2;
        return <fieldset key={axis}><legend className="mb-2 text-[14px] font-medium">{title} <span className="ml-2 text-[12px] font-normal text-ink-muted">{hint}</span></legend><div className="flex flex-wrap gap-2">
          {(catalog.axes[axis] || []).map(item => {
            const chosen = key === 'primary_genre_id' ? selected[key] === item.id : selected[key].includes(item.id);
            const conflict = key === 'story_type_ids' && (catalog.combination_rules?.mutually_exclusive_story_types ?? []).some(pair => pair.includes(item.id) && pair.some(id => id !== item.id && selected.story_type_ids.includes(id)));
            const full = key !== 'primary_genre_id' && selected[key].length >= maximum;
            return <button key={item.id} type="button" aria-pressed={chosen} disabled={!chosen && (conflict || full)}
              title={conflict ? '与已选故事类型互斥，请先取消另一项' : undefined}
              className={`rounded-full border px-3 py-2 text-[14px] transition-colors disabled:opacity-40 ${chosen ? 'border-violet bg-violet text-white' : 'border-paper-line bg-white/75 text-ink-text hover:border-violet/50'}`}
              onClick={() => key === 'primary_genre_id' ? onChange({ ...selected, primary_genre_id: item.id, secondary_genre_ids: selected.secondary_genre_ids.filter(id => id !== item.id) }) : toggle(key, item.id, maximum)}>{item.labels['zh-CN'] || item.id}</button>;
          })}</div></fieldset>;
      })}
      <details className="rounded-xl border border-paper-line p-3 text-[14px]"><summary className="cursor-pointer text-ink-muted">兼容题材与读者频道{selected.secondary_genre_ids.length ? ` · ${selected.secondary_genre_ids.length} 个兼容题材` : ''}</summary>
        <p className="my-3 text-[13px] text-ink-muted">兼容题材最多 2 项；不包含主类型。读者频道用于 H5 分类，具体年龄与市场可在下方补充。</p>
        <div className="flex flex-wrap gap-2">{(catalog.axes.primary_genres || []).filter(i => i.id !== selected.primary_genre_id).map(item => <button type="button" key={item.id} aria-pressed={selected.secondary_genre_ids.includes(item.id)}
          disabled={!selected.secondary_genre_ids.includes(item.id) && selected.secondary_genre_ids.length >= (catalog.combination_rules?.secondary_genre_max ?? 2)}
          className={`rounded-full border px-3 py-2 disabled:opacity-40 ${selected.secondary_genre_ids.includes(item.id) ? 'border-violet bg-violet-soft text-violet' : 'border-paper-line'}`}
          onClick={() => toggle('secondary_genre_ids', item.id, catalog.combination_rules?.secondary_genre_max ?? 2)}>{item.labels['zh-CN']}</button>)}</div>
        <label className="mt-4 flex items-center gap-3">读者频道<select className="rounded-lg border border-paper-line bg-white px-3 py-2" value={selected.audience_channel} onChange={e => onChange({ ...selected, audience_channel: e.target.value as BookClassificationSelection['audience_channel'] })}>
          <option value="general">通用</option><option value="female">女频</option><option value="male">男频</option></select></label>
      </details>
      {!selected.primary_genre_id && required && <p className="text-[13px] text-violet">请选择一个主类型，之后即可选择创作方向。</p>}
    </div>}
  </section>;
}
