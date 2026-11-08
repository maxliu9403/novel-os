import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import WorkshopGuidance from './WorkshopGuidance';
import { guidedSession } from '../test/workshopGuidanceFixture';

function props() {
  return { session: guidedSession(), selected: [] as string[], disabled: false,
    onSelect: vi.fn(), onUpgrade: vi.fn(), onGenerate: vi.fn(), onRefine: vi.fn(), onAlternatives: vi.fn() };
}

test('exposes options immediately without preselecting the recommendation or starting work', async () => {
  const actions = props();
  render(<WorkshopGuidance {...actions} />);
  const radios = screen.getAllByRole('radio');
  expect(radios).toHaveLength(2);
  expect(radios.every(radio => !(radio as HTMLInputElement).checked)).toBe(true);
  await userEvent.click(radios[1]);
  expect(actions.onSelect).toHaveBeenCalledWith('r1:g1', 'r1:p2');
  expect(actions.onGenerate).not.toHaveBeenCalled();
});

test('recommends delivery only for a fresh assessment and only starts on explicit click', async () => {
  const actions = props();
  actions.session = guidedSession(true);
  const view = render(<WorkshopGuidance {...actions} />);
  expect(actions.onGenerate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '生成完整骨架（推荐）' }));
  expect(actions.onGenerate).toHaveBeenCalledTimes(1);
  view.rerender(<WorkshopGuidance {...actions} session={{ ...actions.session, revision: 3 }} />);
  expect(screen.queryByRole('button', { name: '生成完整骨架（推荐）' })).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('上轮判断');
});

test('legacy sessions upgrade explicitly and background operations disable choices', async () => {
  const actions = props();
  const view = render(<WorkshopGuidance {...actions} disabled />);
  expect(screen.getAllByRole('radio').every(r => r.matches(':disabled'))).toBe(true);
  actions.session.creative_design!.policy.guided_choices_version = undefined;
  view.rerender(<WorkshopGuidance {...actions} />);
  await userEvent.click(screen.getByRole('button', { name: '启用选项式共创' }));
  expect(actions.onUpgrade).toHaveBeenCalledTimes(1);
  expect(actions.onGenerate).not.toHaveBeenCalled();
});
