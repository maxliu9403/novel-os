import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, vi } from 'vitest';
import PublicationTypePicker from '../components/PublicationTypePicker';
import { creativeTypesApi } from '../api/creativeTypes';
import type { BookClassificationSelection } from '../api/workshop';
import { typeLibrary } from './creativeTypesFixture';

afterEach(() => vi.restoreAllMocks());

function Form() {
  const [value, setValue] = useState<BookClassificationSelection | null>(null);
  return <><PublicationTypePicker value={value} onChange={setValue} required /><output data-testid="selection">{JSON.stringify(value)}</output></>;
}

test('uses H5 IDs, enforces caps and exclusions, and keeps primary out of secondary', async () => {
  const library = typeLibrary();
  library.publication_catalog.axes.story_types = ['werewolf', 'sweet_romance', 'dark_romance', 'revenge', 'pregnancy'].map(id => ({id, labels: {'zh-CN': id}}));
  library.publication_catalog.combination_rules = {primary_genre_count:1,secondary_genre_max:2,story_type_max:3,tone_max:3,setting_max:2,mutually_exclusive_story_types:[['sweet_romance','dark_romance']]};
  vi.spyOn(creativeTypesApi,'list').mockResolvedValue(library);
  render(<Form />);
  const user=userEvent.setup();
  const primary=await screen.findByRole('group',{name:'主类型 必选 1 项'});
  await user.click(within(primary).getByRole('button',{name:'奇幻'}));
  await user.click(screen.getByRole('button',{name:'sweet_romance'}));
  expect(screen.getByRole('button',{name:'dark_romance'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'werewolf'}));
  await user.click(screen.getByRole('button',{name:'revenge'}));
  expect(screen.getByRole('button',{name:'pregnancy'})).toBeDisabled();
  await user.click(screen.getByText('兼容题材与读者频道'));
  const romance=screen.getAllByRole('button',{name:'浪漫'});
  await user.click(romance.at(-1)!);
  await user.click(within(primary).getByRole('button',{name:'浪漫'}));
  const value=JSON.parse(screen.getByTestId('selection').textContent!);
  expect(value.primary_genre_id).toBe('romance');
  expect(value.secondary_genre_ids).toEqual([]);
  expect(value.story_type_ids).toEqual(['sweet_romance','werewolf','revenge']);
});

test('does not substitute a static list when the canonical catalog fails', async () => {
  vi.spyOn(creativeTypesApi,'list').mockRejectedValueOnce(new Error('catalog offline')).mockResolvedValue(typeLibrary());
  render(<Form />);
  expect(await screen.findByRole('alert')).toHaveTextContent('catalog offline');
  expect(screen.queryByRole('button',{name:'奇幻'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'重试'}));
  expect(await screen.findByRole('group',{name:'主类型 必选 1 项'})).toBeInTheDocument();
});
