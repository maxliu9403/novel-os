import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import NovelWorkshop from '../components/NovelWorkshop';
import SourceBooks from '../routes/SourceBooks';
import ProjectCard from '../components/ProjectCard';
import { ConfirmProvider } from '../components/Confirm';
import { sourceBooksApi } from '../api/sourceBooks';
import { sourceRoutesApi } from '../api/sourceRoutes';
import { workshopApi } from '../api/workshop';
import { api } from '../api/client';
import { creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from './creativeTypesFixture';
import { handedSession, routeSet, sourceBook } from './sourceRoutesFixture';

const seed = { title: '作者自己的标题', author: '作者姓名', premise: '保留母亲留下的信。\n  不要删掉这一条原话。', genres: [] };
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, 'studioModels').mockRejectedValue(new Error('fixture'));
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(sourceBooksApi, 'list').mockResolvedValue({ items: [sourceBook()] });
  vi.spyOn(sourceBooksApi, 'get').mockResolvedValue(sourceBook());
  vi.spyOn(sourceBooksApi, 'usage').mockResolvedValue({ counts: { discussions: 0, projects: 0, completed: 0, in_progress: 0 }, sessions: [], projects: [] });
  vi.spyOn(sourceBooksApi, 'analyze').mockRejectedValue(new Error('legacy analysis must remain untouched'));
  vi.spyOn(sourceRoutesApi, 'list').mockResolvedValue({ items: [routeSet()], runs: [] });
  vi.spyOn(sourceRoutesApi, 'get').mockResolvedValue(routeSet());
  vi.spyOn(sourceRoutesApi, 'start').mockRejectedValue(new Error('no model call during selection'));
  vi.spyOn(sourceRoutesApi, 'handoff').mockResolvedValue({ operation_id: 'handoff-operation', session_id: 'session-route', status: 'ready', session: handedSession() });
  vi.spyOn(workshopApi, 'create').mockRejectedValue(new Error('must use atomic handoff'));
  vi.spyOn(workshopApi, 'turn').mockRejectedValue(new Error('handoff already starts first turn'));
});
afterEach(() => vi.restoreAllMocks());
function Location() { return <output data-testid="url">{useLocation().pathname}{useLocation().search}</output>; }

test('new brainstorm imports inline and directly hands off exact author input with one creation operation', async () => {
  const user = userEvent.setup(), onSessionChange = vi.fn();
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} onSessionChange={onSessionChange} /></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('故事构想或已有框架')).toHaveValue(seed.premise);
  await user.type(screen.getByLabelText('我想保留或改变的地方'), '让高潮靠她自己的专业完成');
  await user.click(screen.getByRole('button', { name: '带入头脑风暴' }));
  await screen.findByText('沿所选路线继续完善人物的选择与代价。');
  expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1);
  expect(sourceRoutesApi.handoff).toHaveBeenCalledWith(expect.objectContaining({
    source_id: 'source-1', route_id: 'route-a', route_set_sha256: 'a'.repeat(64), author_note: '让高潮靠她自己的专业完成',
    draft_id: expect.any(String), operation_id: expect.any(String),
    input: expect.objectContaining({ title: seed.title, author: seed.author, premise: seed.premise, source_book: null, audience_channel: 'female' }),
  }));
  expect(onSessionChange).toHaveBeenCalledWith('session-route');
  expect(workshopApi.create).not.toHaveBeenCalled();
  expect(workshopApi.turn).not.toHaveBeenCalled();
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
});

test('a selected source route can start with an empty original premise and preserves the draft after handoff failure', async () => {
  const user = userEvent.setup();
  vi.mocked(sourceRoutesApi.handoff).mockRejectedValueOnce(new Error('交接响应暂时中断')).mockResolvedValueOnce({ operation_id: 'handoff-operation', session_id: 'session-route', status: 'ready', session: handedSession() });
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={{ ...seed, premise: '' }} onClose={vi.fn()} onBack={vi.fn()} /></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.click(screen.getByRole('button', { name: '带入头脑风暴' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  const body = vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0];
  expect(body.input.premise).toBe('');
  await waitFor(() => expect(screen.getByRole('button', { name: '带入头脑风暴' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '带入头脑风暴' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(2));
  expect(vi.mocked(sourceRoutesApi.handoff).mock.calls[1][0]).toEqual(body);
  expect(workshopApi.create).not.toHaveBeenCalled();
});

test('source library uses the same complete routes and direct handoff, with editable type selection', async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks /><Location /></ConfirmProvider></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.click(await screen.findByRole('button', { name: '背叛' }));
  await user.click(screen.getByRole('button', { name: '第二次机会' }));
  await user.click(screen.getByRole('button', { name: '带入头脑风暴' }));
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/workshop?session=session-route'));
  const body = vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0];
  expect(body.input.creative_types?.map(ref => 'id' in ref ? ref.id : '')).toEqual(['genre.romance', 'tag.second-chance']);
  expect(body.input.premise).toBe('');
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  expect(localStorage.getItem('novelos-library-route-handoff:source-1:source_development:draft')).toBeNull();
  expect(localStorage.getItem('novelos-library-route-handoff:source-1:source_development')).toBeNull();
  await user.click(screen.getByRole('button', { name: '选这条路线 · 把真相一起说完' }));
  await user.click(screen.getByRole('button', { name: '带入头脑风暴' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(2));
  const next = vi.mocked(sourceRoutesApi.handoff).mock.calls[1][0];
  expect(next.route_id).toBe('route-b');
  expect(next.draft_id).not.toBe(body.draft_id);
  expect(next.operation_id).not.toBe(body.operation_id);
});

test('linked new route discussions and projects do not require a legacy analysis hash', async () => {
  const source = { source_id: 'source-1', title: '原资料', mode: 'source_development', schema: 'source-route-context.v1', analysis_sha256: null, route_set_sha256: 'a'.repeat(64) };
  vi.mocked(sourceBooksApi.usage).mockResolvedValue({ counts: { discussions: 1, projects: 1, completed: 0, in_progress: 1 }, sessions: [{ id: 'session-route', title: '路线讨论', status: 'ready', source }], projects: [{ id: 'project-route', title: '路线作品', status: 'running', source }] });
  render(<MemoryRouter><ConfirmProvider><SourceBooks /><ProjectCard p={{ id: 'project-route', title: '路线作品', genre: 'Romance', chapter_count: 60, status: 'running', source }} /></ConfirmProvider></MemoryRouter>);
  await screen.findByText('查看关联讨论与作品');
  expect(screen.getByRole('link', { name: '参考资料 · 原资料' })).toHaveAttribute('href', '/source-books?source=source-1');
  expect(screen.getAllByText(/整书路线/).length).toBeGreaterThan(0);
  expect(screen.getByText('路线讨论')).toHaveAttribute('href', '/workshop?session=session-route');
});
