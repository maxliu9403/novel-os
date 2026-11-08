import { guidedSession } from './workshopGuidanceFixture';
import type { StoryMap } from '../api/workshop';

export function storySession() {
  const session = guidedSession();
  const design = session.creative_design!;
  design.policy.story_workshop_version = 'story-workshop.v1';
  const round = design.rounds[0];
  const map: StoryMap = { synopsis: '她必须在归属与自由之间作出选择，姐妹的关心不会因此消失。结局仍是提案。', focus: 'ending', reviewed_revision: 2, source_revision: 1,
    changes: ['把庇护的代价连到结局'], nodes: (['direction', 'opening', 'middle', 'climax', 'ending'] as const).map(area => ({ area, summary: '当前走线，尚待作者决定', status: 'proposed', evidence: [] })),
    opening: { situation: '救助别人后自己无处可去', anchor: '姐妹之间真实的关心', reader_experience: '担忧、共情与不平', genre_cause: '狼群规则改变庇护条件', first_value: '争取一夜安全的具体代价', next_expectation: '联盟是否值得维持', preview: '门开着，她却不能走进去。' } };
  round.story = map;
  design.story_state = { map, pending: { ...round.guidance!.groups![0], proposals: round.proposals, recommendation_reason: '让自主带来真实代价，也保留温柔的回报。' },
    actions: [], assumptions: [{ id: 'fact1', kind: 'external_fact', text: '现实救助程序仍需核实', status: 'unverified', source_revision: 1 }] };
  return session;
}
