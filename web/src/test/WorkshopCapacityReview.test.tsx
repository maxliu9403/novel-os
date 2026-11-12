import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { workshopApi, type SkeletonReview } from '../api/workshop';
import { api } from '../api/client';
import { creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from './creativeTypesFixture';
import { storySession } from './workshopStoryFixture';
import NovelWorkshop from '../components/NovelWorkshop';
import WorkshopCapacity from '../components/WorkshopCapacity';
import WorkshopSkeletonReview from '../components/WorkshopSkeletonReview';
import { choiceRequest, clearChoiceRequest } from '../lib/workshopChoiceRequest';

function capacitySession() {
  const session = storySession();
  session.input.words_per_chapter = 1200;
  session.creative_design!.policy.capacity_version = 'story-capacity.v1';
  session.creative_design!.story_state!.pending = null;
  session.capacity = { schema: 'story-capacity.v1', status: 'proposed', proposal_sha256: 'a'.repeat(64), reviewed_revision: session.revision,
    plan: { language: 'English', count_unit: 'words', rationale: '给母女分别与高潮余波留出完整场景。',
      scenes: [{ id: 'scene_1', title: '母女分别', source_quote: '已确认的母女分别情节', presentation: 'on_page', reader_value: '感受到选择的代价', minimum: 900, maximum: 1300, reason: '需要对话和即时反应' }],
      options: [{ id: 'expanded', title: '保留故事，展开场景', recommended: true, tradeoff: '篇幅更长，让情绪变化有空间', total_words: 1200,
        chapters: [{ number: 1, target_words: 1200, scene_ids: ['scene_1'] }] }] } };
  return session;
}

const review: SkeletonReview = { schema: 'skeleton-review.v1', status: 'proposed', source_revision: 4, source_sha256: 'b'.repeat(64), review_sha256: 'c'.repeat(64),
  summary: '让选择产生可见后果', issues: [{ id: 'issue_1', chapter: 1, category: 'reader_value', problem: '目前只有结果，缺少行动代价',
    quote: '她拒绝了请求。', replacement: '她退回钥匙，对方拿走了她的行李。' }], notes: ['现实程序仍需核实'] };

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); clearChoiceRequest('guided-session', 'turn');
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(api, 'studioModels').mockRejectedValue(new Error('not configured'));
  vi.spyOn(api, 'getJob').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
});
afterEach(() => vi.restoreAllMocks());

test('capacity adoption is one atomic intent and cannot skip a story change in the composer', async () => {
  const data = capacitySession();
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  const user = userEvent.setup();
  const adopt = await screen.findByRole('button', { name: /采用「保留故事，展开场景」并生成骨架/ });
  expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeDisabled();
  const composer = screen.getByLabelText(/补充你的想法/);
  await user.type(composer, '改变最后的选择');
  expect(adopt).toBeDisabled();
  await user.clear(composer);
  await user.dblClick(adopt);
  await waitFor(() => expect(turn).toHaveBeenCalledTimes(1));
  expect(turn).toHaveBeenCalledWith(data.id, expect.objectContaining({ intent: 'deliver', message: '', proposal_ids: [], capacity_choice: { proposal_sha256: 'a'.repeat(64), option_id: 'expanded' } }));
});

test('the next-step card opens saved capacity choices without adopting an option', async () => {
  const data = capacitySession();
  data.next_step = { kind: 'choose_capacity', label: '查看篇幅方案', reason: '篇幅方案已保存，请选择后生成完整骨架。',
    can_continue: true, can_deliver: false, can_plan_capacity: true, delivery_blocked_reason: '请先选择并确认篇幅方案。' };
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  try {
    render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
    await userEvent.click(await screen.findByRole('button', { name: '查看篇幅方案' }));
    expect(scroll).toHaveBeenCalled();
    expect(screen.getByRole('region', { name: '故事容量与篇幅' })).toHaveFocus();
    expect(turn).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /采用「保留故事，展开场景」并生成骨架/ })).toBeEnabled();
  } finally {
    if (original) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', original);
    else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView');
  }
});

test('confirmed allocation enables delivery while a missing confirmed record remains blocked', async () => {
  const data = capacitySession();
  data.capacity!.status = 'confirmed';
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const screenView = render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  await screen.findByRole('heading', { name: '当前没有需要你决定的重大分歧' });
  expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeDisabled();
  screenView.unmount();
  data.capacity!.confirmed = { option: data.capacity!.plan!.options[0], language: 'English', count_unit: 'words' };
  data.next_step = { kind: 'deliver', label: '生成完整骨架', reason: '篇幅已确认。', can_continue: true, can_deliver: true, can_plan_capacity: false, delivery_blocked_reason: null };
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  await screen.findByRole('heading', { name: '可以生成完整骨架了' });
  expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeEnabled();
});

test('stale recommendations remain non-executable and confirmed targets survive refresh', () => {
  const data = capacitySession();
  data.capacity!.status = 'stale';
  const view = render(<WorkshopCapacity session={data} disabled={false} draft="" onAdopt={vi.fn()} onPlan={vi.fn()}/>);
  expect(screen.queryByRole('button', { name: /并生成骨架/ })).not.toBeInTheDocument();
  data.capacity!.status = 'confirmed';
  data.capacity!.confirmed = { option: data.capacity!.plan!.options[0], language: 'English', count_unit: 'words' };
  view.rerender(<WorkshopCapacity session={data} disabled={false} draft="" onAdopt={vi.fn()} onPlan={vi.fn()}/>);
  expect(screen.getByText('篇幅已确认')).toBeVisible();
  expect(screen.getByText(/1 章 · 约 1,200 词/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /并生成骨架/ })).not.toBeInTheDocument();
});

test('failed delivery retries the confirmed allocation without asking for another capacity choice', async () => {
  const data = capacitySession();
  data.status = 'error'; data.turn_intent = 'deliver'; data.last_error = 'Temporary model failure';
  data.capacity!.status = 'confirmed';
  data.capacity!.confirmed = { option: data.capacity!.plan!.options[0], language: 'English', count_unit: 'words' };
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  await userEvent.setup().click(await screen.findByRole('button', { name: '重试本轮' }));
  await waitFor(() => expect(turn).toHaveBeenCalledWith(data.id, expect.objectContaining({ message: '请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。', revision: data.revision, intent: 'deliver' })));
});

test('edit comparison lets author apply suggestions or keep original without a reason', async () => {
  const resolve = vi.fn();
  render(<WorkshopSkeletonReview review={review} disabled={false} onResolve={resolve}/>);
  const user = userEvent.setup();
  expect(screen.getByText('她拒绝了请求。')).toBeVisible();
  expect(screen.getByText('她退回钥匙，对方拿走了她的行李。')).toBeVisible();
  await user.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: '采用 0 项修订并保存' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '保留当前骨架' }));
  expect(resolve).toHaveBeenLastCalledWith([]);
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: '采用 1 项修订并保存' }));
  expect(resolve).toHaveBeenLastCalledWith(['issue_1']);
});

test('ready skeleton exposes revision decision before naming and persists it without a model turn', async () => {
  const data = capacitySession();
  data.prompt_md = '# Skeleton\nTitle: Story'; data.ready_for_confirmation = true; data.status = 'ready'; data.questions = [];
  data.skeleton_review = review;
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const revised = { ...data, revision: 5, skeleton_review: { ...review, status: 'resolved' as const, decision: 'apply_selected' as const, selected_ids: ['issue_1'], resolved_revision: 5 } };
  const save = vi.spyOn(workshopApi, 'reviseSkeleton').mockResolvedValue(revised);
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  const launch = vi.spyOn(workshopApi, 'launch');
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: /骨架与启动/ }));
  expect(screen.getByRole('button', { name: '确认骨架，推荐书名' })).toBeDisabled();
  await user.dblClick(screen.getByRole('button', { name: '采用 1 项修订并保存' }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(await screen.findByText(/已采用 1 项修订，保存为第 5 版/)).toBeVisible();
  expect(screen.getByRole('button', { name: '确认骨架，推荐书名' })).toBeEnabled();
  expect(turn).not.toHaveBeenCalled(); expect(launch).not.toHaveBeenCalled();
});

test('lost adoption response reuses identity but another option never reuses it', () => {
  const choice = { proposal_sha256: 'a'.repeat(64), option_id: 'A' };
  const first = choiceRequest('guided-session', 'turn', 2, '', 'deliver', [], undefined, choice);
  expect(choiceRequest('guided-session', 'turn', 3, '', 'deliver', [], undefined, choice)).toEqual(first);
  expect(choiceRequest('guided-session', 'turn', 3, '', 'deliver', [], undefined, { ...choice, option_id: 'B' }).operation_id).not.toBe(first.operation_id);
});

test('expansion offers a chapter count under the author limit instead of accepting long chapters', async () => {
  const data = capacitySession();
  data.input.words_per_chapter = 1100;
  const option = data.capacity!.plan!.options[0];
  option.total_words = 16800;
  option.chapters = Array.from({ length: 12 }, (_, i) => ({ number: i + 1, target_words: 1400, scene_ids: ['scene_1'] }));
  const adopt = vi.fn(), replan = vi.fn();
  render(<WorkshopCapacity session={data} disabled={false} draft="" onAdopt={adopt} onPlan={vi.fn()} onRepartition={replan}/>);
  const card = screen.getByRole('article', { name: option.title });
  const input = within(card).getByRole('spinbutton');
  expect(input).toHaveValue(16);
  expect(screen.getByText(/调整后平均约 1,050 词/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /并生成骨架/ })).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.clear(input); await user.type(input, '15');
  expect(screen.getByRole('button', { name: /重新安排$/ })).toBeDisabled();
  await user.clear(input); await user.type(input, '18');
  await user.click(screen.getByRole('button', { name: '按 18 章重新安排' }));
  expect(replan).toHaveBeenCalledWith({ proposal_sha256: 'a'.repeat(64), option_id: 'expanded', chapters: 18 });
  expect(adopt).not.toHaveBeenCalled();
});

test('average alone cannot approve an overlong individual chapter; changing count is model-free until submitted', async () => {
  const data = capacitySession();
  data.input.words_per_chapter = 1100;
  data.capacity!.plan!.options[0].total_words = 2200;
  data.capacity!.plan!.options[0].chapters = [
    { number: 1, target_words: 1300, scene_ids: ['scene_1'] },
    { number: 2, target_words: 900, scene_ids: ['scene_1'] },
  ];
  const replan = vi.fn();
  render(<WorkshopCapacity session={data} disabled={false} draft="" onAdopt={vi.fn()} onPlan={vi.fn()} onRepartition={replan}/>);
  expect(screen.getByText(/部分章节超过你设置的/)).toBeVisible();
  expect(screen.getByRole('button', { name: '按 2 章重新安排' })).toBeEnabled();
  await userEvent.setup().click(screen.getByRole('button', { name: '建议 2 章起' }));
  expect(replan).not.toHaveBeenCalled();
});

test('count selection submits a durable structured request without generating a skeleton', async () => {
  const data = capacitySession();
  data.input.words_per_chapter = 800;
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  await userEvent.setup().dblClick(await screen.findByRole('button', { name: '按 2 章重新安排' }));
  await waitFor(() => expect(turn).toHaveBeenCalledTimes(1));
  expect(turn).toHaveBeenCalledWith(data.id, expect.objectContaining({ intent: 'discuss', message: '',
    capacity_replan: { proposal_sha256: 'a'.repeat(64), option_id: 'expanded', chapters: 2 } }));
});

test('repartition identity survives uncertain response but cannot be reused for a different count', () => {
  const replan = { proposal_sha256: 'a'.repeat(64), option_id: 'A', chapters: 16 };
  const first = choiceRequest('guided-session', 'turn', 2, '', 'discuss', [], undefined, undefined, replan);
  expect(choiceRequest('guided-session', 'turn', 3, '', 'discuss', [], undefined, undefined, replan)).toEqual(first);
  expect(choiceRequest('guided-session', 'turn', 3, '', 'discuss', [], undefined, undefined, { ...replan, chapters: 18 }).operation_id).not.toBe(first.operation_id);
});

test('refreshing a failed repartition exposes retry with the original constraints', async () => {
  const data = capacitySession();
  data.status = 'error'; data.capacity!.status = 'stale';
  data.capacity_replan = { request: { proposal_sha256: 'a'.repeat(64), option_id: 'expanded', chapters: 2 }, chapter_words_limit: 800, total_words: 1200 };
  const replan = vi.fn();
  render(<WorkshopCapacity session={data} disabled={false} draft="" onAdopt={vi.fn()} onPlan={vi.fn()} onRepartition={replan}/>);
  expect(screen.getByText(/等待重新安排为 2 章/)).toBeVisible();
  await userEvent.setup().click(screen.getByRole('button', { name: '重新安排分章' }));
  expect(replan).toHaveBeenCalledWith(data.capacity_replan.request);
});
