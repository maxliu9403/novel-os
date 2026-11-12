import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WorkshopCreativeBasis from '../components/WorkshopCreativeBasis';
import { storySession } from './workshopStoryFixture';
import { storyRoute } from './sourceRoutesFixture';

test('original exploration protects only known requirements without claiming an adopted route', async () => {
  const session = storySession();
  session.development_view = { phase: 'exploring', title: '女性创业故事', commitments: [{ id: 'author-1', category: 'author_requirement', text: '不靠婚恋解决经济困境', adoption: 'author' }], emotion_designs: [] };
  render(<WorkshopCreativeBasis session={session}/>);
  expect(screen.getByText('正在探索故事方向')).toBeVisible();
  await userEvent.click(screen.getByText('查看已明确的要求'));
  expect(screen.getByText('不靠婚恋解决经济困境')).toBeVisible();
  expect(screen.queryByText('沿当前方向深化')).not.toBeInTheDocument();
});

test('adopted emotional commitments retain concrete choice, cost, change and payoff', async () => {
  const session = storySession(), route = storyRoute();
  session.development_view = { phase: 'developing', title: route.title, commitments: [{ id: 'ending-1', category: 'ending', text: route.causal_arc.ending, adoption: 'route_selection' }], emotion_designs: route.emotional_design };
  render(<WorkshopCreativeBasis session={session}/>);
  await userEvent.click(screen.getByText('查看当前核心与情绪看点'));
  const panel = screen.getByRole('region', { name: '当前创作依据' });
  for (const text of Object.values(route.emotional_design[0])) expect(within(panel).getByText(text)).toBeVisible();
  expect(within(panel).getByText(/实际情绪效果仍需在正文中落实/)).toBeVisible();
});

test('legacy sessions do not fabricate a baseline or claim reviewed commitments', () => {
  render(<WorkshopCreativeBasis session={storySession()}/>);
  expect(screen.queryByRole('region', { name: '当前创作依据' })).not.toBeInTheDocument();
});
