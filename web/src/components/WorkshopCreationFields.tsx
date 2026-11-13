import type { NarrativeMode, WorkshopInput } from '../api/workshop';
import { WORKSHOP_MODES, languageCountUnit } from '../lib/workshopCreation';
import { Field, fieldClass, textareaClass } from './Modal';
import StoryBriefInput from './StoryBriefInput';

/** Shared by both reference-route entry points and the ordinary workshop. */
export default function WorkshopCreationFields({ value, onChange, section, onPendingChange, disabled = false, required = true, route = false, newCreation = false }: {
  value: WorkshopInput;
  onChange: (change: Partial<WorkshopInput>) => void;
  section: 'story' | 'preferences';
  onPendingChange: (pending: boolean) => void;
  disabled?: boolean;
  required?: boolean;
  route?: boolean;
  newCreation?: boolean;
}) {
  const countUnit = languageCountUnit(value.language);
  if (section === 'story') return <fieldset className="min-w-0" disabled={disabled}>
    <div className="grid gap-x-5 sm:grid-cols-2">
      <Field label="暂定书名"><input className={fieldClass} value={value.title} onChange={e => onChange({ title: e.target.value })} placeholder="选填，骨架确认后再由你决定正式书名" /></Field>
      <Field label="作者"><input className={fieldClass} value={value.author} onChange={e => onChange({ author: e.target.value })} placeholder="留空则在创作时生成笔名" /></Field>
    </div>
    <StoryBriefInput value={value.premise} onChange={premise => onChange({ premise })} onPendingChange={onPendingChange} required={required} disabled={disabled} />
  </fieldset>;

  return <fieldset className="min-w-0" disabled={disabled}>
    <div className="mb-5 rounded-2xl border border-violet/20 bg-violet-soft/40 p-4 sm:p-5">
      <h5 className="mb-4 text-[17px] font-semibold">为谁写，用什么语言写</h5>
      <div className="grid gap-x-5 sm:grid-cols-3">
        <Field label="正文语言"><input className={fieldClass} value={value.language} onChange={e => onChange({ language: e.target.value })} placeholder="例如：English、简体中文" /></Field>
        <Field label="目标读者"><input className={fieldClass} value={value.audience} onChange={e => onChange({ audience: e.target.value })} placeholder="年龄、阅读偏好或期待的情绪" /></Field>
        <Field label="发行地域"><input className={fieldClass} value={value.market} onChange={e => onChange({ market: e.target.value })} placeholder="例如：欧美；不同于故事发生地" /></Field>
      </div>
      <p className="text-[13px] leading-6 text-ink-muted">正文按你选择的语言创作；资料和中文讨论不会改变正文语言。读者定位不限制主角性别。</p>
    </div>
    {value.length_planning === 'story_first' && <p className="mb-4 text-[14px] leading-7 text-ink-muted">先把故事设计完整，再按关键场景推荐篇幅。数字是可修改的初始偏好；生成骨架前，你会确认完整的分章方案。</p>}
    <div className="grid gap-x-5 sm:grid-cols-3">
      <Field label="希望的总篇幅"><input type="number" min={1} step={1} className={fieldClass} value={value.target_total_words ?? ''} onChange={e => onChange({ target_total_words: e.target.value ? Number(e.target.value) : undefined })} placeholder="由模型建议" /></Field>
      <Field label={route ? '希望的每章字数' : value.length_planning === 'story_first' ? '每章规划上限（字／词）' : '每章大约多少字／词'}><input type="number" min={1} step={1} className={fieldClass} value={value.words_per_chapter ?? ''} onChange={e => onChange({ words_per_chapter: e.target.value ? Number(e.target.value) : undefined })} placeholder="未确定可留空" /></Field>
      <Field label={route ? '初步章节数' : value.length_planning ? '偏好章节数（可选）' : '计划章节数'}><input type="number" min={1} max={511} step={1} className={fieldClass} value={value.chapters ?? ''} onChange={e => onChange({ chapters: e.target.value ? Number(e.target.value) : undefined })} placeholder="由模型建议" /></Field>
    </div>
    <p className="mb-5 text-[13px] leading-6 text-ink-muted">{countUnit === 'words' ? '当前英文篇幅按词计数。' : countUnit === 'characters' ? '当前中文篇幅按字计数。' : '中文按字、英文按词计数。'} 每章设置用于规划上限；调整数字不会删去关键情节。更换语言后请重新确认篇幅偏好。</p>
    <Field label="故事组织形式"><select className={fieldClass} value={value.mode ?? ''} onChange={e => onChange({ mode: e.target.value as NarrativeMode | '', length_planning: ['multi_volume', 'series_installment'].includes(e.target.value) ? null : newCreation || value.length_planning ? 'story_first' : null })}>
      <option value="">尚未确定，在讨论中选择</option>{WORKSHOP_MODES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select></Field>
    {value.mode === 'series_installment' && <p className="mb-4 text-[13px] leading-6 text-ink-muted">这里填写当前分册的篇幅。系列总册数、共享人物和跨册伏笔会在讨论中补齐，每册分别启动。</p>}
    {value.mode === 'multi_volume' && <p className="mb-4 text-[13px] text-ink-muted">填写整本书的章节数；卷数、章节范围与卷间承接会在讨论中确定。</p>}
    <Field label="硬性限制"><textarea className={textareaClass} rows={3} value={value.constraints} onChange={e => onChange({ constraints: e.target.value })} placeholder="必须保留的角色、禁止情节、结局或揭示时点；没有限制也可以写明。" /></Field>
  </fieldset>;
}
