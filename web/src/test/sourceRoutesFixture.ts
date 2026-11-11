import type { SourceRouteRun, StoryRoute, StoryRouteSet } from '../api/sourceRoutes';
import type { SourceDetail } from '../api/sourceBooks';
import type { WorkshopSession } from '../api/workshop';
export function storyRoute(id = 'route-a', title = '不再等待道歉'): StoryRoute {
  return { route_id: id, sha256: id.padEnd(64, 'a'), title, reader_promise: '读者期待她拿回决定自己生活的权利。', continuity_basis: '六章共同终点是未寄出的那封信。', source_refs: ['source-1:span-1'],
    causal_arc: { opening: '她把旧钥匙放在桌上，当面拒绝撤回证词。', development: '独立开店的资金取决于公开旧账。', turning_point: '她选择公开账本，也失去了曾经的保护。', climax: '她在听证会上承担自己的错误，拒绝交易换取沉默。', ending: '她独立开店，与旧爱不复合，获得平静与自由。' },
    character_relationship_arcs: ['依附转为平等对话，终局仍保留彼此边界。'],
    emotional_design: [{ expectation: '希望她终于被认真对待', scene: '她归还钥匙并公开自己的证词。', choice: '宁愿失去帮助也不替别人沉默。', cost: '失去开店资金', change: '读者从担心变为支持', payoff: '她凭自己的专业赢得第一位客户。' }],
    highlights: [{ scene: '当面对质旧账，而不是再靠误会拖延。', story_value: '把关系主动权交还给她。' }],
    long_hooks_and_payoffs: [{ question: '信里没说出的真相是什么？', development: '账本与旧证词逐渐拼完整', payoff: '她主动解释自己的选择，秘密不再控制两人。' }],
    type_suggestion: { audience: 'female', genre_id: 'genre.romance', tag_ids: ['tag.betrayal'], reason: '关系信任破裂使她必须选择独立。' },
    proposed_assumptions: ['开店是新的创作设想'], tradeoffs: ['放弃复合的情绪回报'], difference_from_existing: '以事业重建而非复合作为最终回报。', changes_from_parent: '' };
}
export function routeSet(patch: Partial<StoryRouteSet> = {}): StoryRouteSet {
  return { schema: 'source-route-set.v1', sha256: 'a'.repeat(64), source_id: 'source-1', source_sha256: 's'.repeat(64), understanding_sha256: 'u'.repeat(64), mode: 'source_development', author_context: {}, feedback_history: [], routes: [storyRoute(), storyRoute('route-b', '把真相一起说完')], recommended_route_id: 'route-a', recommendation_reason: '延续原文累积的主体性期待。', created_at: '2026-11-11T10:00:00Z', understanding: { narrative_endpoint: '第六章：她拿到了那封信。', opening_analysis: '最早的失约让读者在意承诺是否能兑现。' }, ...patch };
}
export function routeRun(patch: Partial<SourceRouteRun> = {}): SourceRouteRun {
  return { run_id: 'b'.repeat(32), status: 'running', stage: 'routes', completed: 1, total: 3, model_calls: 1,
    request: { operation_id: 'operation-existing', operation: 'expand_routes', mode: 'source_development', author_context: {}, feedback: '保留她不复合的方向' }, ...patch };
}
export function sourceBook(): SourceDetail {
  return { id: 'source-1', title: '被留下的信', book_id: 'book-1', chapter_count: 6, supplement_count: 0, cover_url: null, warnings: [], synopsis: '原作简介', study: { status: 'pending', completed: 0, total: 0 }, result: null };
}
export function handedSession(): WorkshopSession {
  return { id: 'session-route', revision: 0, input: { title: '作者自己的标题', author: '', premise: '', genres: [], language: '', audience: '', market: '', constraints: '' }, messages: [{ role: 'assistant', content: '沿所选路线继续完善人物的选择与代价。' }], prompt_md: '', questions: [], ready_for_confirmation: false, status: 'ready', last_error: null, job_id: null, skill_version: 'test', prepared: null, project_id: null, logs: [] };
}
