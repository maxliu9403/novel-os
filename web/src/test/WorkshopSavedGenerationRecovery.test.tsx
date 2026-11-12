import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { api } from '../api/client';
import { workshopApi } from '../api/workshop';
import { creativeTypesApi } from '../api/creativeTypes';
import NovelWorkshop from '../components/NovelWorkshop';
import { handedSession } from './sourceRoutesFixture';
import { typeLibrary } from './creativeTypesFixture';

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(api, 'studioModels').mockRejectedValue(new Error('fixture'));
});
afterEach(() => vi.restoreAllMocks());

test('a saved discussion response resumes its review without submitting a new turn', async () => {
  const current = handedSession();
  current.status = 'error'; current.revision = 3; current.job_id = 'review-failed';
  current.input.premise = 'Keep the protagonist independent.';
  current.last_error = '核验连接暂时不可用。';
  current.recovery = { kind: 'saved_generation', can_resume: true, label: '继续已保存的设计核验',
    detail: '本轮完整回复已保存，将继续核验与修订，不重新生成这轮故事。' };
  vi.spyOn(workshopApi, 'get').mockResolvedValue(current);
  const resume = vi.spyOn(workshopApi, 'resume').mockResolvedValue({ job_id: 'review-resumed', kind: 'workshop.turn', status: 'running', error: null });
  const turn = vi.spyOn(workshopApi, 'turn');
  const guided = vi.spyOn(workshopApi, 'guidedTurn');
  vi.spyOn(api, 'getJob').mockResolvedValue({ job_id: 'review-resumed', kind: 'workshop.turn', status: 'running', error: null });
  render(<MemoryRouter><NovelWorkshop open initialSessionId={current.id} seed={{ title: '', author: '', genres: [], premise: '' }} onClose={vi.fn()} onBack={vi.fn()} /></MemoryRouter>);
  const recover = await screen.findByRole('button', { name: '继续已保存的设计核验' });
  expect(screen.queryByRole('button', { name: '重试本轮' })).not.toBeInTheDocument();
  expect(screen.getByText(/不重新生成这轮故事/)).toBeVisible();
  await userEvent.click(recover);
  await waitFor(() => expect(resume).toHaveBeenCalledWith(current.id, 3));
  expect(turn).not.toHaveBeenCalled();
  expect(guided).not.toHaveBeenCalled();
});

test('an error without a verified saved response does not claim review-only recovery', async () => {
  const current = handedSession();
  current.status = 'error'; current.last_error = '模型请求结果尚未确认。';
  current.input.premise = 'Keep the protagonist independent.';
  vi.spyOn(workshopApi, 'get').mockResolvedValue(current);
  const resume = vi.spyOn(workshopApi, 'resume');
  render(<MemoryRouter><NovelWorkshop open initialSessionId={current.id} seed={{ title: '', author: '', genres: [], premise: '' }} onClose={vi.fn()} onBack={vi.fn()} /></MemoryRouter>);
  await screen.findByText('模型请求结果尚未确认。');
  expect(screen.queryByRole('button', { name: '继续已保存的设计核验' })).not.toBeInTheDocument();
  expect(screen.queryByText(/不重新生成这轮故事/)).not.toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled();
});
