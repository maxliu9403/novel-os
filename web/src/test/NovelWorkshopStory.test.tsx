import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { workshopApi } from '../api/workshop';
import { api } from '../api/client';
import { creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from './creativeTypesFixture';
import { storySession } from './workshopStoryFixture';
import NovelWorkshop from '../components/NovelWorkshop';
import { clearChoiceRequest, choiceRequest } from '../lib/workshopChoiceRequest';

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); clearChoiceRequest('guided-session', 'turn');
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(api, 'studioModels').mockRejectedValue(new Error('not configured'));
  vi.spyOn(api, 'getJob').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
});
afterEach(() => vi.restoreAllMocks());
function view() {
  const data = storySession();
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  const rendered = render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  return { data, ...rendered };
}

test('one choice is required for advance and delivery; atomic choice and additions are sent once', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  const save = vi.spyOn(workshopApi, 'saveChoices');
  view();
  const user = userEvent.setup();
  const next = await screen.findByRole('button', { name: '采用并继续推演' });
  expect(next).toBeDisabled(); expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeDisabled();
  await user.type(screen.getByLabelText(/补充你的想法/), '保留姐妹关心彼此');
  expect(next).toBeDisabled();
  const choices = screen.getAllByRole('radio');
  await user.click(choices[0]); await user.click(choices[1]);
  expect(choices[0]).not.toBeChecked(); expect(choices[1]).toBeChecked();
  await user.dblClick(next);
  expect(turn).toHaveBeenCalledTimes(1); expect(save).not.toHaveBeenCalled();
  expect(turn).toHaveBeenCalledWith('guided-session', expect.objectContaining({ proposal_ids: ['r1:p2'], message: '保留姐妹关心彼此', story_action: { mode: 'select', question_id: 'r1:g1' } }));
});

test('submitted additions stay visible on the decision tab while waiting for the next reply', async () => {
  const data = storySession();
  const note = '保留姐妹关系，并让开篇行动影响结局';
  data.messages.push({ role: 'user', content: note });
  data.messages.push({ role: 'user', content: '继续完成上轮请求，不改变已经确认的决定' });
  data.status = 'running';
  data.job_id = 'next';
  data.creative_design!.story_state!.pending = null;
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  await screen.findByRole('tab', { name: '本轮决定' });
  expect(screen.getByRole('tab', { name: '本轮决定' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getAllByText(note).some(element => !element.closest('[hidden]'))).toBe(true);
  expect(screen.getByText('正在推演你刚才的决定与补充')).toBeVisible();
  const receipt = screen.getByRole('region', { name: '已发送的内容' });
  expect(within(receipt).getByText('继续完成上轮请求，不改变已经确认的决定')).toBeVisible();
  expect(screen.getByText('上一轮设计说明').closest('details')).not.toHaveAttribute('open');
});

test('accepted submission is shown immediately even when the next poll cannot load the session', async () => {
  const data = storySession();
  const note = '补充开篇细节，但保留已确认的结局';
  const get = vi.spyOn(workshopApi, 'get').mockResolvedValueOnce(data).mockRejectedValue(new Error('暂时断网'));
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  const user = userEvent.setup();
  await screen.findByRole('button', { name: '采用并继续推演' });
  await user.click(screen.getAllByRole('radio')[0]);
  await user.type(screen.getByLabelText(/补充你的想法/), note);
  await user.click(screen.getByRole('button', { name: '采用并继续推演' }));
  const receipt = await screen.findByRole('region', { name: '已发送的内容' });
  expect(within(receipt).getByText(new RegExp(note))).toBeVisible();
  expect(within(receipt).getByText(/采用方案/)).toBeVisible();
  expect(screen.getByLabelText(/补充你的想法/)).toHaveValue('');
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(receipt).toBeVisible();
  expect(turn).toHaveBeenCalledTimes(1);
});

test('a rejected submission keeps the draft without showing a successful submission receipt', async () => {
  view();
  vi.spyOn(workshopApi, 'guidedTurn').mockRejectedValue(new Error('请求未接受'));
  const user = userEvent.setup();
  await screen.findByRole('button', { name: '采用并继续推演' });
  await user.click(screen.getAllByRole('radio')[0]);
  await user.type(screen.getByLabelText(/补充你的想法/), '未发送的补充');
  await user.click(screen.getByRole('button', { name: '采用并继续推演' }));
  await screen.findByText('请求未接受');
  expect(screen.getByLabelText(/补充你的想法/)).toHaveValue('未发送的补充');
  expect(screen.queryByRole('region', { name: '已发送的内容' })).not.toBeInTheDocument();
});

test('tabs expose synopsis, opening intent and assumptions while history stays separate', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  view(); const user = userEvent.setup();
  await user.click(await screen.findByRole('tab', { name: '故事全貌' }));
  expect(within(screen.getByRole('heading', { name: '我们的故事，现在是这样' }).parentElement!).getByText(/她必须在归属与自由/)).toBeVisible();
  expect(screen.getByText('开篇：读者为什么在意')).toBeVisible();
  expect(screen.queryByText('完整推演：行动使两人的关系发生变化。')).not.toBeVisible();
  await user.click(screen.getByRole('tab', { name: /待定事项/ }));
  expect(screen.getByText('现实救助程序仍需核实')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: '讨论记录' }));
  expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'story-panel-history');
  expect(turn).not.toHaveBeenCalled();
});

test.each(['question', 'custom', 'delegate', 'reframe'])('%s is explicit and clears adoption semantics', async mode => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  view(); const user = userEvent.setup();
  await screen.findByRole('button', { name: '采用并继续推演' });
  await user.click(screen.getAllByRole('radio')[0]);
  if (mode === 'custom') await user.click(screen.getByRole('radio', { name: /我来定方向/ }));
  if (mode === 'delegate') await user.click(screen.getByRole('radio', { name: /委托模型决定本题/ }));
  if (mode === 'question') await user.click(screen.getByRole('button', { name: '先问清楚，不作决定' }));
  if (mode === 'reframe') await user.click(screen.getByRole('button', { name: '都不合适，换一组' }));
  const label = mode === 'question' ? '提问，保留本题' : mode === 'reframe' ? '提交并换一组' : mode === 'delegate' ? '委托本题并继续' : '采用并继续推演';
  if (['question', 'custom'].includes(mode)) {
    expect(screen.getByRole('button', { name: label })).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: /写下/ }), '先说明她失去庇护的真实代价');
  }
  if (['question', 'reframe'].includes(mode)) expect(screen.getByRole('button', { name: '生成完整骨架' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: label }));
  expect(turn).toHaveBeenCalledWith('guided-session', expect.objectContaining({ proposal_ids: [], story_action: { mode, question_id: 'r1:g1' } }));
});

test('selected draft survives remount; switching action changes replay identity', async () => {
  const first = view(); const user = userEvent.setup();
  await screen.findByRole('button',{name:'采用并继续推演'});
  await user.click(screen.getAllByRole('radio')[1]);
  first.unmount(); vi.mocked(workshopApi.get).mockRestore();
  view();
  await screen.findByRole('button',{name:'采用并继续推演'});
  await waitFor(() => expect(screen.getAllByRole('radio')[1]).toBeChecked());
  const a = choiceRequest('x', 'turn', 2, '理由', 'discuss', [], { mode: 'question', question_id: 'q' });
  const retry = choiceRequest('x', 'turn', 3, '理由', 'discuss', [], { mode: 'question', question_id: 'q' });
  expect(retry).toEqual(a);
  const b = choiceRequest('x', 'turn', 3, '理由', 'discuss', [], { mode: 'custom', question_id: 'q' });
  expect(b.operation_id).not.toBe(a.operation_id);
});

test('legacy map warning distinguishes saved discussion from stale map without another call', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  const data = storySession();
  data.creative_design!.story_state!.notice = '本轮故事地图未完整返回，保留上次地图与未决问题；完整回复可在讨论记录查看。不会自动重试。';
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  expect(await screen.findByText(/本次历史记录没有保存具体校验原因/)).toBeVisible();
  expect(screen.queryByText(/保留上次地图与未决问题/)).not.toBeInTheDocument();
  expect(turn).not.toHaveBeenCalled();
});

test('partial map update exposes failed component while fresh map and choices remain usable', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  const data = storySession(); const state = data.creative_design!.story_state!;
  state.notice = '讨论与故事地图已更新；部分辅助信息未采用，详见处理详情。不会自动重试。';
  state.validation = { schema: 'story-validation.v1', source_revision: 1, reviewed_revision: 2,
    status: 'partial', map_status: 'updated', opening_status: 'updated',
    issues: [{ component: 'assumptions', path: 'assumptions[0].option_index', code: 'major_premise_requires_option', message: '重大前提必须关联本轮可选方案，未自动采用。' }] };
  vi.spyOn(workshopApi, 'get').mockResolvedValue(data);
  render(<MemoryRouter><NovelWorkshop open seed={data.input} initialSessionId={data.id} onClose={vi.fn()} onBack={vi.fn()}/></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(await screen.findByText('查看处理详情 · 1 项'));
  expect(screen.getByText('重大前提必须关联本轮可选方案，未自动采用。')).toBeVisible();
  expect(screen.getByText(/故事地图基于第 1 版输入 · 已结合本轮整理/)).toBeVisible();
  await user.click(screen.getAllByRole('radio')[0]);
  expect(screen.getByRole('button', { name: '采用并继续推演' })).toBeEnabled();
  expect(turn).not.toHaveBeenCalled();
});
