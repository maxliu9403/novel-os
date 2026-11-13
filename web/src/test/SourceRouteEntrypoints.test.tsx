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
import * as documentImportApi from '../api/documentImport';
import { creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from './creativeTypesFixture';
import { handedSession, routeRun, routeSet, sourceBook } from './sourceRoutesFixture';

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

test.each(['library', 'new-workshop'])('%s carries editable route length preferences and core adoption into the same handoff', async entry => {
  const data = routeSet();
  data.routes[0].length_recommendation = { total_words: 80000, words_per_chapter: 4000, chapters: 20, language: 'English', count_unit: 'words', reason: '为关键选择与高潮余波留出空间。' };
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [data], runs: [] });
  vi.mocked(sourceRoutesApi.get).mockResolvedValue(data);
  const user = userEvent.setup();
  if (entry === 'library') render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/><Location/></ConfirmProvider></MemoryRouter>);
  else {
    render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()}/></ConfirmProvider></MemoryRouter>);
    await user.click(screen.getByRole('radio', { name: '原故事推演' }));
    await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  }
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  const total = screen.getByRole('spinbutton', { name: '希望的总篇幅' });
  const length = screen.getByRole('spinbutton', { name: '希望的每章字数' });
  await user.clear(total); await user.type(total, '100000');
  await user.clear(length); await user.type(length, '2500');
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  expect(sourceRoutesApi.handoff).toHaveBeenCalledWith(expect.objectContaining({ adoption_scope: 'core_and_payoffs', route_id: 'route-a',
    input: expect.objectContaining({ target_total_words: 100000, words_per_chapter: 2500, chapters: 20, language: 'English', audience: '欧美女性读者', market: '欧美', premise: entry === 'library' ? '' : seed.premise }) }));
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});

test('new brainstorm imports inline and directly hands off exact author input with one creation operation', async () => {
  const user = userEvent.setup(), onSessionChange = vi.fn();
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} onSessionChange={onSessionChange} /></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('故事构想或已有框架')).toHaveValue(seed.premise);
  await user.type(screen.getByLabelText('我想保留或改变的地方'), '让高潮靠她自己的专业完成');
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
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

test('later basic-setting edits override route length suggestions at handoff', async () => {
  const user = userEvent.setup();
  const data = routeSet();
  data.routes[0].length_recommendation = { total_words: 80000, words_per_chapter: 4000, chapters: 20,
    language: 'English', count_unit: 'words', reason: '为关键选择与高潮余波留出空间。' };
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [data], runs: [] });
  vi.mocked(sourceRoutesApi.get).mockResolvedValue(data);
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} /></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await user.click(await screen.findByRole('button', { name: '下一步' }));
  const count = screen.getByLabelText('偏好章节数（可选）');
  const length = screen.getByLabelText('每章规划上限（字／词）');
  await user.clear(count); await user.type(count, '30');
  await user.clear(length); await user.type(length, '3000');
  await user.clear(screen.getByLabelText('目标读者')); await user.type(screen.getByLabelText('目标读者'), '成年女性');
  await user.type(screen.getByLabelText('硬性限制'), '必须保留原始结局');
  await user.clear(screen.getByLabelText('正文语言')); await user.type(screen.getByLabelText('正文语言'), 'English');
  await user.click(screen.getByRole('button', { name: '1 故事与素材' }));
  await screen.findByRole('button', { name: '采用此路线并展开' });
  await waitFor(() => expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await user.click(await screen.findByRole('button', { name: '下一步' }));
  expect(screen.getByLabelText('偏好章节数（可选）')).toHaveValue(30);
  expect(screen.getByLabelText('每章规划上限（字／词）')).toHaveValue(3000);
  expect(screen.getByLabelText('正文语言')).toHaveValue('English');
  await user.click(screen.getByRole('button', { name: '带入所选路线，开始头脑风暴' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  expect(sourceRoutesApi.handoff).toHaveBeenCalledWith(expect.objectContaining({
    input: expect.objectContaining({ chapters: 30, words_per_chapter: 3000, target_total_words: 80000, language: 'English', audience: '成年女性', constraints: '必须保留原始结局' }),
  }));
});

test('an existing source-route session with an empty premise can save length settings', async () => {
  const user = userEvent.setup();
  const current = handedSession();
  current.source_context = { selected_proposal: routeSet().routes[0] };
  current.input.creative_types = typeLibrary().items.slice(0, 2).map(item => ({ id: item.id, version: item.active_version!, source_sha256: item.source_sha256! }));
  current.input.genres = typeLibrary().items.slice(0, 2).map(item => item.name);
  current.input.chapters = 20;
  vi.spyOn(workshopApi, 'get').mockResolvedValue(current);
  const update = vi.spyOn(workshopApi, 'updateInput').mockImplementation(async (_id, _revision, input) => ({
    ...current, input, revision: 1, restart_required: true, input_needs_revision: true,
  }));
  const restart = vi.spyOn(workshopApi, 'restart');
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open initialSessionId={current.id} seed={seed} onClose={vi.fn()} onBack={vi.fn()} /></ConfirmProvider></MemoryRouter>);
  await screen.findByText('沿所选路线继续完善人物的选择与代价。');
  await user.click(screen.getByRole('button', { name: /1\s*基础设定/ }));
  await user.click(screen.getByRole('button', { name: '修改基础设定' }));
  const chapters = screen.getByLabelText('计划章节数');
  await user.clear(chapters); await user.type(chapters, '30');
  expect(screen.getByRole('button', { name: '保存基础设定' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: '保存基础设定' }));
  await waitFor(() => expect(update).toHaveBeenCalledWith(current.id, 0, expect.objectContaining({ premise: '', chapters: 30 })));
  expect(restart).not.toHaveBeenCalled();
  expect(workshopApi.turn).not.toHaveBeenCalled();
});

test('a selected source route can start with an empty original premise and preserves the draft after handoff failure', async () => {
  const user = userEvent.setup();
  vi.mocked(sourceRoutesApi.handoff).mockRejectedValueOnce(new Error('交接响应暂时中断')).mockResolvedValueOnce({ operation_id: 'handoff-operation', session_id: 'session-route', status: 'ready', session: handedSession() });
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={{ ...seed, premise: '' }} onClose={vi.fn()} onBack={vi.fn()} /></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  const body = vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0];
  expect(body.input.premise).toBe('');
  await waitFor(() => expect(screen.getByRole('button', { name: '采用此路线并展开' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
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
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/workshop?session=session-route'));
  const body = vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0];
  expect(body.input.creative_types?.map(ref => 'id' in ref ? ref.id : '')).toEqual(['genre.romance', 'tag.second-chance']);
  expect(body.input.premise).toBe('');
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  expect(localStorage.getItem('novelos-library-route-handoff:source-1:source_development:draft')).toBeNull();
  expect(localStorage.getItem('novelos-library-route-handoff:source-1:source_development')).toBeNull();
  await user.click(screen.getByRole('button', { name: '选这条路线 · 把真相一起说完' }));
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
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

test.each(['library', 'workshop'])('%s recovers an unknown design on the same run without changing author material', async entrance => {
  const user = userEvent.setup();
  const run = routeRun({ status: 'pending_unknown', worker_active: false, recovery: {
    can_resume: false, can_retry_unknown: true, reason: 'request_timeout',
    message: '模型请求等待超时，资料和进度已保存。', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64),
  } });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [], runs: [run] });
  const resume = vi.spyOn(sourceRoutesApi, 'resume');
  const retry = vi.spyOn(sourceRoutesApi, 'retryUnknown').mockResolvedValue(routeRun());
  if (entrance === 'library') {
    render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks /></ConfirmProvider></MemoryRouter>);
  } else {
    render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()} /></ConfirmProvider></MemoryRouter>);
    await user.click(screen.getByRole('radio', { name: '原故事推演' }));
    await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  }
  const button = await screen.findByRole('button', { name: '重新执行未完成步骤' });
  expect(screen.queryByRole('button', { name: '继续未完成的设计' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '生成首批整书路线' })).toBeDisabled();
  await user.click(button);
  expect(retry).toHaveBeenCalledWith('source-1', run.run_id, expect.objectContaining({ action: 'retry_unknown', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64) }));
  expect(resume).not.toHaveBeenCalled();
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  if (entrance === 'workshop') expect(screen.getByLabelText('故事构想或已有框架')).toHaveValue(seed.premise);
});

test.each(['library', 'new-workshop'])('%s hands off the complete editable creative input without resetting it when adopting genres', async entry => {
  const user = userEvent.setup();
  if (entry === 'library') render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/></ConfirmProvider></MemoryRouter>);
  else {
    render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()}/></ConfirmProvider></MemoryRouter>);
    await user.click(screen.getByRole('radio', { name: '原故事推演' }));
    await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  }
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('正文语言')).toHaveValue('English');
  expect(screen.getByLabelText('目标读者')).toHaveValue('欧美女性读者');
  expect(screen.getByLabelText('发行地域')).toHaveValue('欧美');
  const edits = { '暂定书名': 'The Last Letter', '作者': 'N. Author', '故事构想或已有框架': '保留完整的路线。\n  补充：她依靠专业破局。',
    '正文语言': 'English (US)', '目标读者': '成年英语女性读者', '发行地域': '美国、英国', '硬性限制': '保留母女线，不复合；避免露骨描写。',
    '希望的总篇幅': '90000', '希望的每章字数': '3000', '初步章节数': '30' };
  for (const [label, value] of Object.entries(edits)) {
    await user.clear(screen.getByLabelText(label)); await user.type(screen.getByLabelText(label), value);
  }
  await user.selectOptions(screen.getByLabelText('故事组织形式'), 'series_installment');
  await user.click(screen.getByRole('button', { name: '采用这组推荐' }));
  for (const [label, value] of Object.entries(edits)) expect(screen.getByLabelText(label)).toHaveValue(/篇幅|字数|章节数/.test(label) ? Number(value) : value);
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  expect(vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0].input).toMatchObject({
    title: 'The Last Letter', author: 'N. Author', premise: edits['故事构想或已有框架'], language: 'English (US)',
    audience: '成年英语女性读者', market: '美国、英国', constraints: edits['硬性限制'], mode: 'series_installment',
    length_planning: null, target_total_words: 90000, words_per_chapter: 3000, chapters: 30,
    audience_channel: 'female', source_book: null,
  });
  expect(vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0].input).not.toHaveProperty('author_note');
  expect(workshopApi.create).not.toHaveBeenCalled();
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});

test('library restores explicit legacy route language and current edited creation settings after remount', async () => {
  const saved = routeSet(), user = userEvent.setup();
  localStorage.setItem('novelos-route-draft-v1:library:source-1:source_development', JSON.stringify({ feedback: '', scope: '',
    selected: { sha: saved.sha256, route_id: 'route-a' }, routeDrafts: { [`${saved.sha256}:route-a`]: {
      author_note: '保留这句原话', language: '简体中文', audience_channel: 'female', genres: ['Romance', '背叛'],
      creative_types: typeLibrary().items.slice(0, 2).map(item => ({ id: item.id, version: item.active_version, source_sha256: item.source_sha256 })),
      target_total_words: 80000, words_per_chapter: 4000, chapters: 20,
    } } }));
  const view = () => <MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/></ConfirmProvider></MemoryRouter>;
  const first = render(view());
  expect(await screen.findByLabelText('正文语言')).toHaveValue('简体中文');
  expect(screen.getByLabelText('希望的总篇幅')).toHaveValue(80000);
  await user.clear(screen.getByLabelText('目标读者'));
  await user.clear(screen.getByLabelText('发行地域')); await user.type(screen.getByLabelText('发行地域'), '中国大陆');
  await user.type(screen.getByLabelText('硬性限制'), '必须保留母亲的信');
  first.unmount();
  render(view());
  expect(await screen.findByLabelText('正文语言')).toHaveValue('简体中文');
  expect(screen.getByLabelText('目标读者')).toHaveValue('');
  expect(screen.getByLabelText('发行地域')).toHaveValue('中国大陆');
  expect(screen.getByLabelText('硬性限制')).toHaveValue('必须保留母亲的信');
  expect(screen.getByLabelText('我想保留或改变的地方')).toHaveValue('保留这句原话');
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  expect(vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0].input).toMatchObject({ language: '简体中文', audience: '', market: '中国大陆', constraints: '必须保留母亲的信', target_total_words: 80000 });
});

test('Chinese route estimates do not silently become English words or override the English default', async () => {
  const data = routeSet(), user = userEvent.setup();
  data.routes[0].length_recommendation = { total_words: 80000, words_per_chapter: 4000, chapters: 20, language: '简体中文', count_unit: 'characters', reason: '保留故事完整性。' };
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [data], runs: [] });
  vi.mocked(sourceRoutesApi.get).mockResolvedValue(data);
  render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/></ConfirmProvider></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('正文语言')).toHaveValue('English');
  expect(screen.getByLabelText('希望的总篇幅')).toHaveValue(null);
  expect(screen.getByLabelText('希望的每章字数')).toHaveValue(null);
  expect(screen.getByLabelText('初步章节数')).toHaveValue(20);
  expect(screen.getByText(/总篇幅与每章数字未自动套用/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '使用本路线的篇幅建议' })).not.toBeInTheDocument();
  await user.clear(screen.getByLabelText('正文语言')); await user.type(screen.getByLabelText('正文语言'), '简体中文');
  await user.click(screen.getByRole('button', { name: '使用本路线的篇幅建议' }));
  expect(screen.getByLabelText('希望的总篇幅')).toHaveValue(80000);
  expect(screen.getByLabelText('希望的每章字数')).toHaveValue(4000);
});

test('inline route document import must be accepted before either direct handoff or the next step', async () => {
  const user = userEvent.setup();
  vi.spyOn(documentImportApi, 'importDocument').mockResolvedValue({ filename: 'notes.md', text: '保留母女和事业的双线，不删减结局。', characters: 18, pages: null, warnings: [] });
  render(<MemoryRouter><ConfirmProvider><NovelWorkshop open seed={seed} onClose={vi.fn()} onBack={vi.fn()}/></ConfirmProvider></MemoryRouter>);
  await user.click(screen.getByRole('radio', { name: '原故事推演' }));
  await user.selectOptions(await screen.findByLabelText('选择参考小说'), 'source-1');
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.upload(screen.getByLabelText('选择故事构想文件'), new File(['notes'], 'notes.md', { type: 'text/markdown' }));
  await screen.findByLabelText('导入文字预览');
  expect(screen.getByRole('button', { name: '采用此路线并展开' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '确认导入' }));
  expect(screen.getByLabelText('故事构想或已有框架')).toHaveValue(`${seed.premise}\n\n保留母女和事业的双线，不删减结局。`);
  expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: '采用此路线并展开' }));
  await waitFor(() => expect(sourceRoutesApi.handoff).toHaveBeenCalledTimes(1));
  expect(vi.mocked(sourceRoutesApi.handoff).mock.calls[0][0].input.premise).toContain('保留母女和事业的双线，不删减结局。');
});

test('saved route author settings take precedence over defaults for a new library draft', async () => {
  const data = routeSet({ author_context: { language: '简体中文', audience: '中国成年女性读者', market: '中国', constraints: '保留事业线', title: '作者已给出的名字', target_total_words: 80000 } });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [data], runs: [] });
  vi.mocked(sourceRoutesApi.get).mockResolvedValue(data);
  render(<MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/></ConfirmProvider></MemoryRouter>);
  await userEvent.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('正文语言')).toHaveValue('简体中文');
  expect(screen.getByLabelText('目标读者')).toHaveValue('中国成年女性读者');
  expect(screen.getByLabelText('发行地域')).toHaveValue('中国');
  expect(screen.getByLabelText('硬性限制')).toHaveValue('保留事业线');
  expect(screen.getByLabelText('暂定书名')).toHaveValue('作者已给出的名字');
});

test('clearing route length preferences persists instead of restoring historical author-context numbers', async () => {
  const data = routeSet({ author_context: { target_total_words: 80000, chapters: 20, words_per_chapter: 4000 } });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [data], runs: [] });
  vi.mocked(sourceRoutesApi.get).mockResolvedValue(data);
  const view = () => <MemoryRouter initialEntries={['/source-books?source=source-1']}><ConfirmProvider><SourceBooks/></ConfirmProvider></MemoryRouter>;
  const first = render(view());
  await userEvent.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('希望的总篇幅')).toHaveValue(80000);
  await userEvent.clear(screen.getByLabelText('希望的总篇幅'));
  await userEvent.clear(screen.getByLabelText('初步章节数'));
  await userEvent.clear(screen.getByLabelText('希望的每章字数'));
  first.unmount(); render(view());
  expect(await screen.findByLabelText('希望的总篇幅')).toHaveValue(null);
  expect(screen.getByLabelText('初步章节数')).toHaveValue(null);
  expect(screen.getByLabelText('希望的每章字数')).toHaveValue(null);
});
