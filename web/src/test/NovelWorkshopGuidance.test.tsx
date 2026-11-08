import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { workshopApi } from '../api/workshop';
import { api, ApiError } from '../api/client';
import { creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from './creativeTypesFixture';
import { guidedSession } from './workshopGuidanceFixture';
import NovelWorkshop from '../components/NovelWorkshop';
import { choiceRequest, clearChoiceRequest } from '../lib/workshopChoiceRequest';

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  clearChoiceRequest('guided-session', 'turn'); clearChoiceRequest('guided-session', 'save');
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(api, 'studioModels').mockRejectedValue(new Error('No configured route'));
  vi.spyOn(api, 'getJob').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
});
afterEach(() => vi.restoreAllMocks());

function view(ready = false) {
  const current = guidedSession(ready);
  vi.spyOn(workshopApi, 'get').mockResolvedValue(current);
  render(<MemoryRouter><NovelWorkshop open seed={current.input} initialSessionId={current.id} onClose={vi.fn()} onBack={vi.fn()} /></MemoryRouter>);
  return current;
}

test('submits choice plus open addition once without a separate save request', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  const save = vi.spyOn(workshopApi, 'saveChoices');
  view();
  const user = userEvent.setup();
  await user.click(await screen.findByRole('radio',{name:/方案 A/}));
  expect(turn).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText(/补充你的想法/), '保留姐妹之间的关心');
  await user.dblClick(screen.getByRole('button', { name: '按所选继续' }));
  expect(turn).toHaveBeenCalledTimes(1);
  expect(turn).toHaveBeenCalledWith('guided-session', expect.objectContaining({ revision: 2, message: '保留姐妹之间的关心', intent: 'discuss', proposal_ids: ['r1:p1'], operation_id: expect.any(String) }));
  expect(save).not.toHaveBeenCalled();
});

test('save only stores choice and addition without inference', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn');
  const current = guidedSession();
  const save = vi.spyOn(workshopApi, 'saveChoices').mockResolvedValue({ ...current, revision: 3 });
  view();
  const user = userEvent.setup();
  await user.click(await screen.findByRole('radio',{name:/方案 B/}));
  await user.type(screen.getByLabelText(/补充你的想法/), '增加温柔的回报');
  await user.click(screen.getByRole('button', { name: '仅保存选择与补充' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(current.id, expect.objectContaining({ proposal_ids: ['r1:p2'], message: '增加温柔的回报' })));
  expect(turn).not.toHaveBeenCalled();
});

test('delivery recommendation includes unsent additions and remains explicitly deliver', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn').mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  view(true);
  const user = userEvent.setup();
  await screen.findByRole('button', { name: '生成完整骨架（推荐）' });
  expect(turn).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText(/补充你的想法/), '结局保留重逢的余地');
  await user.click(screen.getByRole('button', { name: '生成完整骨架（推荐）' }));
  expect(turn).toHaveBeenCalledWith('guided-session', expect.objectContaining({ intent: 'deliver', message: expect.stringContaining('结局保留重逢的余地') }));
});

test('lost responses retain request identity and original revision after refresh', () => {
  const first = choiceRequest('guided-session', 'turn', 2, '继续', 'discuss', ['r1:p1']);
  const retry = choiceRequest('guided-session', 'turn', 3, '继续', 'discuss', ['r1:p1']);
  expect(retry).toEqual(first);
  const changed = choiceRequest('guided-session', 'turn', 3, '换路线', 'discuss', ['r1:p2']);
  expect(changed.operation_id).not.toBe(first.operation_id);
});

test('definite revision conflicts allow explicit retry with fresh revision and preserved input', async () => {
  const turn = vi.spyOn(workshopApi, 'guidedTurn')
    .mockRejectedValueOnce(new ApiError('版本已变化', 409))
    .mockResolvedValue({ job_id: 'next', kind: 'workshop', status: 'running', error: null });
  const current = view();
  const user = userEvent.setup();
  await user.click(await screen.findByRole('radio',{name:/方案 A/}));
  await user.type(screen.getByLabelText(/补充你的想法/), '保留温柔');
  vi.mocked(workshopApi.get).mockResolvedValue({ ...current, revision: 3 });
  await user.click(screen.getByRole('button', { name: '按所选继续' }));
  await screen.findByText('版本已变化');
  await waitFor(() => expect(screen.getByRole('button', { name: '按所选继续' })).toBeEnabled());
  expect(screen.getByLabelText(/补充你的想法/)).toHaveValue('保留温柔');
  await user.click(screen.getByRole('button', { name: '按所选继续' }));
  expect(turn).toHaveBeenCalledTimes(2);
  expect(turn.mock.calls[1][1].revision).toBe(3);
  expect(turn.mock.calls[1][1].operation_id).not.toBe(turn.mock.calls[0][1].operation_id);
});
