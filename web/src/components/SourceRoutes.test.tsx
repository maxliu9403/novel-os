import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import SourceRoutes from './SourceRoutes';
import { ConfirmProvider } from './Confirm';
import { sourceRoutesApi } from '../api/sourceRoutes';
import { creativeRef, creativeTypesApi } from '../api/creativeTypes';
import { typeLibrary } from '../test/creativeTypesFixture';
import { routeRun, routeSet } from '../test/sourceRoutesFixture';

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(creativeTypesApi, 'list').mockResolvedValue(typeLibrary());
  vi.spyOn(sourceRoutesApi, 'list').mockResolvedValue({ items: [routeSet()], runs: [] });
  vi.spyOn(sourceRoutesApi, 'get').mockResolvedValue(routeSet());
  vi.spyOn(sourceRoutesApi, 'start').mockResolvedValue(routeRun());
});
afterEach(() => vi.restoreAllMocks());
function view(props: Partial<Parameters<typeof SourceRoutes>[0]> = {}) {
  return render(<MemoryRouter><ConfirmProvider><SourceRoutes sourceId="source-1" mode="source_development" {...props} /></ConfirmProvider></MemoryRouter>);
}
test('compares complete stories and exposes concrete emotions without adopting a recommendation', async () => {
  const choice = vi.fn(), proceed = vi.fn(); const user = userEvent.setup();
  view({ onChoice: choice, onContinue: proceed });
  await screen.findByRole('heading', { name: '不再等待道歉' });
  expect(choice).toHaveBeenLastCalledWith(null);
  expect(proceed).not.toHaveBeenCalled();
  expect(screen.getAllByText('她独立开店，与旧爱不复合，获得平静与自由。')).toHaveLength(2);
  await user.click(screen.getByRole('button', { name: '查看完整设计 · 不再等待道歉' }));
  const dialog = await screen.findByRole('dialog', { name: '不再等待道歉' });
  expect(within(dialog).getByText('宁愿失去帮助也不替别人沉默。')).toBeVisible();
  await user.click(within(dialog).getByRole('tab', { name: '完整故事走向' }));
  expect(within(dialog).getByText('她在听证会上承担自己的错误，拒绝交易换取沉默。')).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: '选这条路线，继续调整' }));
  await user.click(await screen.findByRole('button', { name: '带入头脑风暴' }));
  expect(proceed).toHaveBeenCalledWith(expect.objectContaining({ route_id: 'route-a', route_set_sha256: 'a'.repeat(64), creative_types: typeLibrary().items.slice(0, 2).map(creativeRef), author_note: '' }));
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});
test('keeps explicit author types and each route note while switching, applies recommendation only explicitly', async () => {
  const user = userEvent.setup(), choice = vi.fn();
  const library = typeLibrary();
  view({ onChoice: choice, authorContext: { premise: '保持第一人称', audience_channel: 'female', creative_types: [library.items[0], library.items[2]].map(creativeRef), genres: ['Romance', '第二次机会'] } });
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(await screen.findByRole('button', { name: '第二次机会' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: '背叛' })).toHaveAttribute('aria-pressed', 'false');
  await user.type(screen.getByLabelText('我想保留或改变的地方'), '保留她的职业能力');
  await user.click(screen.getByRole('button', { name: '选这条路线 · 把真相一起说完' }));
  expect(screen.getByLabelText('我想保留或改变的地方')).toHaveValue('');
  await user.click(screen.getByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByLabelText('我想保留或改变的地方')).toHaveValue('保留她的职业能力');
  await user.click(screen.getByRole('button', { name: '采用这组推荐' }));
  expect(screen.getByRole('button', { name: '背叛' })).toHaveAttribute('aria-pressed', 'true');
  expect(choice).toHaveBeenLastCalledWith(expect.objectContaining({ author_note: '保留她的职业能力', creative_types: library.items.slice(0, 2).map(creativeRef) }));
});
test('scopes revision separately from adopted direction and retains edits entered during submission', async () => {
  const user = userEvent.setup(); let resolve!: (value: ReturnType<typeof routeRun>) => void;
  vi.mocked(sourceRoutesApi.start).mockImplementation(() => new Promise(done => { resolve = done; }));
  view({ authorContext: { premise: '作者完整构想', constraints: '不使用误会拖延' } });
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.selectOptions(screen.getByLabelText('本次修改范围'), 'route-b');
  await user.type(screen.getByLabelText('我想调整 / 补充的方向'), '加强 B 的事业看点，再给两个方向');
  await user.click(screen.getByRole('button', { name: '按补充完善方案' }));
  expect(sourceRoutesApi.start).toHaveBeenCalledWith('source-1', expect.objectContaining({ operation: 'revise_routes', feedback: '加强 B 的事业看点，再给两个方向', target_route_ids: ['route-b'], author_context: { premise: '作者完整构想', constraints: '不使用误会拖延' } }));
  await user.clear(screen.getByLabelText('我想调整 / 补充的方向'));
  await user.type(screen.getByLabelText('我想调整 / 补充的方向'), '下一轮还想保留母亲线');
  await act(async () => resolve(routeRun()));
  expect(screen.getByLabelText('我想调整 / 补充的方向')).toHaveValue('下一轮还想保留母亲线');
  expect(screen.getByRole('button', { name: '已选此方向' })).toHaveAttribute('aria-pressed', 'true');
});
test('retries an unacknowledged append with the same operation, preserving all old routes', async () => {
  const user = userEvent.setup();
  vi.mocked(sourceRoutesApi.start).mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce(routeRun());
  view(); await screen.findByRole('heading', { name: '不再等待道歉' });
  await user.type(screen.getByLabelText('我想调整 / 补充的方向'), '多给一些不同的结局');
  await user.click(screen.getByRole('button', { name: '保留已有，再给新方案' }));
  await screen.findByRole('alert');
  const request = vi.mocked(sourceRoutesApi.start).mock.calls[0][1];
  await user.click(screen.getByRole('button', { name: '保留已有，再给新方案' }));
  await waitFor(() => expect(sourceRoutesApi.start).toHaveBeenCalledTimes(2));
  expect(vi.mocked(sourceRoutesApi.start).mock.calls[1][1]).toEqual(request);
  expect(request).toMatchObject({ operation: 'expand_routes', requested_count: 2, base_route_set_sha256: 'a'.repeat(64) });
  expect(screen.getByRole('heading', { name: '不再等待道歉' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '把真相一起说完' })).toBeVisible();
});
test('carries pending feedback identity with a chosen saved route and does not silently claim it updated', async () => {
  const user = userEvent.setup(), proceed = vi.fn();
  const run = routeRun({ status: 'failed' });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [routeSet()], runs: [run] });
  view({ onContinue: proceed });
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  expect(screen.getByText(/后续补充尚未全部反映/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: '以此方案为基础，带上新要求进入讨论' }));
  expect(proceed).toHaveBeenCalledWith(expect.objectContaining({ pending_feedback_run_id: run.run_id, route_set_sha256: 'a'.repeat(64) }));
});
test('restores feedback, selected route, and route-specific notes without invoking a model', async () => {
  const user = userEvent.setup(); const first = view();
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  await user.type(screen.getByLabelText('我想保留或改变的地方'), '保留结局');
  await user.type(screen.getByLabelText('我想调整 / 补充的方向'), '还没有提交的分析意见');
  first.unmount(); view();
  expect(await screen.findByLabelText('我想保留或改变的地方')).toHaveValue('保留结局');
  expect(screen.getByLabelText('我想调整 / 补充的方向')).toHaveValue('还没有提交的分析意见');
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});

test('an interrupted persisted running state offers resume without pretending a worker is active', async () => {
  const user = userEvent.setup();
  const run = routeRun({ status: 'running', worker_active: false, interrupted: true });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [routeSet()], runs: [run] });
  const resume = vi.spyOn(sourceRoutesApi, 'resume').mockResolvedValue(routeRun({ status: 'pending_unknown' }));
  view();
  await user.click(await screen.findByRole('button', { name: '继续未完成的设计' }));
  expect(resume).toHaveBeenCalledWith('source-1', run.run_id);
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});

test('publishes a visible new-version entry without silently adopting it', async () => {
  const user = userEvent.setup(), choice = vi.fn();
  view({ onChoice: choice });
  await user.click(await screen.findByRole('button', { name: '选这条路线 · 不再等待道歉' }));
  const next = routeSet({ sha256: 'c'.repeat(64), created_at: '2026-11-11T12:00:00Z', recommendation_reason: '新版调整了事业线。' });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [next, routeSet()], runs: [routeRun({ status: 'ready', route_set_sha256: next.sha256 })] });
  vi.mocked(sourceRoutesApi.get).mockImplementation(async (_id, sha) => sha === next.sha256 ? next : routeSet());
  await user.click(screen.getByRole('button', { name: '刷新路线' }));
  await screen.findByRole('button', { name: '查看新方案' });
  expect(choice).toHaveBeenLastCalledWith(expect.objectContaining({ route_set_sha256: 'a'.repeat(64), latest_route_set_sha256: next.sha256 }));
  await user.click(screen.getByRole('button', { name: '查看新方案' }));
  await screen.findByText('新版调整了事业线。', { exact: false });
  expect(choice).toHaveBeenLastCalledWith(null);
});

test('unknown response shows stopped worker and explicit retry instead of ineffective resume or a new initial run', async () => {
  const user = userEvent.setup();
  const run = routeRun({ status: 'pending_unknown', worker_active: false, recovery: {
    can_resume: false, can_retry_unknown: true, reason: 'request_timeout',
    message: '模型请求等待超时，未保存完整回复。', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64),
  }, execution: { timeout_scope: 'request', timeout_seconds: 900, elapsed_seconds: 900 } });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [], runs: [run] });
  const resume = vi.spyOn(sourceRoutesApi, 'resume');
  const retry = vi.spyOn(sourceRoutesApi, 'retryUnknown').mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce(routeRun());
  view();
  await screen.findByText('模型请求等待超时，未保存完整回复。');
  expect(screen.getByText('本次设计已停止')).toBeVisible();
  expect(screen.getByText(/原请求结果无法查询，重新执行可能产生额外模型费用/)).toBeVisible();
  expect(screen.getByText(/已等待 15 分钟/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '继续未完成的设计' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '生成首批整书路线' })).toBeDisabled();
  expect(retry).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '重新执行未完成步骤' }));
  await screen.findByRole('alert');
  const request = retry.mock.calls[0][2];
  expect(request).toEqual({ action: 'retry_unknown', operation_id: expect.any(String), request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64) });
  await user.click(screen.getByRole('button', { name: '重新执行未完成步骤' }));
  expect(retry).toHaveBeenCalledTimes(2);
  expect(retry.mock.calls[1][2]).toEqual(request);
  expect(resume).not.toHaveBeenCalled();
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
});

test('legacy unknown without capabilities never offers unsafe plain resume and refresh remains available', async () => {
  const run = routeRun({ status: 'pending_unknown', worker_active: false });
  vi.mocked(sourceRoutesApi.list).mockResolvedValue({ items: [], runs: [run] });
  view();
  await screen.findByText('请求结果尚未确认，进度已保存');
  expect(screen.queryByRole('button', { name: '继续未完成的设计' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重新执行未完成步骤' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '刷新路线' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '生成首批整书路线' })).toBeDisabled();
});
