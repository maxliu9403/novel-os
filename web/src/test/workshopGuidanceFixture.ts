import type { WorkshopSession } from '../api/workshop';
import { workshopDesign } from './workshopDesignFixture';

export function guidedSession(ready = false): WorkshopSession {
  const design = workshopDesign();
  design.policy.guided_choices_version = 'guided-choices.v1';
  const round = design.rounds[0];
  round.proposals.push({ ...round.proposals[0], id: 'r1:p2', title: '独立重建', story: 'Rin接受失去庇护的代价，和愿意同行的人建立新家。', recommended: false });
  round.guidance = { status: 'assessed', reviewed_revision: 2,
    groups: ready ? [] : [{ id: 'r1:g1', topic: '她将如何争取庇护', why: '选择会改变联盟和结局', option_ids: ['r1:p1', 'r1:p2'], recommended_id: 'r1:p1' }],
    coverage: (['premise', 'characters', 'plot', 'ending'] as const).map(area => ({ area, status: ready ? 'settled' : 'proposed', summary: '主角通过选择承担代价，改变与族群的关系。', evidence: [] })),
    next_action: ready ? 'generate' : 'choose', reason: ready ? '主线、转折和结局已有明确依据。' : '先确定主角将为自由承担怎样的代价。', missing: [] };
  return { id: 'guided-session', revision: 2,
    input: { title: '月下归途', author: '', genres: ['Werewolf'], premise: '医师救助流亡者，试图改变默认的照护责任。', language: 'English', mode: 'short_novel', chapters: 12, words_per_chapter: 600, audience: '成年读者', market: '美国', constraints: '' },
    messages: [{ role: 'assistant', content: '完整推演：行动使两人的关系发生变化。' }], prompt_md: '', questions: [], ready_for_confirmation: false,
    status: 'draft', last_error: null, job_id: null, skill_version: 'test', prepared: null, project_id: null, logs: [], creative_design: design };
}
