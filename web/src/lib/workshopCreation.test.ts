import { expect, test } from 'vitest';
import type { SourceAuthorContext } from '../api/sourceRoutes';
import { newWorkshopInput } from './workshopCreation';

test('new creations default to English prose for Western female readers', () => {
  expect(newWorkshopInput()).toMatchObject({ language: 'English', audience_channel: 'female', audience: '欧美女性读者', market: '欧美' });
});

test('omitted audience follows the seed channel without overwriting explicit author settings', () => {
  expect(newWorkshopInput({ audience_channel: 'male' })).toMatchObject({ audience_channel: 'male', audience: '', language: 'English' });
  expect(newWorkshopInput({ audience_channel: null })).toMatchObject({ audience_channel: null, audience: '' });
  expect(newWorkshopInput({ audience_channel: 'male', audience: '作者指定的读者', language: '简体中文', market: '' })).toMatchObject({
    audience_channel: 'male', audience: '作者指定的读者', language: '简体中文', market: '',
  });
  expect(newWorkshopInput({ audience: '', language: '' })).toMatchObject({ audience: '', language: '' });
});

test('route context only contributes canonical creative settings to the handoff input', () => {
  const context: SourceAuthorContext = { premise: '完整构想\n  第二行', constraints: '保留关系弧光', display_language: '中文', author_note: '独立交接意见' };
  const input = newWorkshopInput(context);
  expect(input).toMatchObject({ premise: context.premise, constraints: context.constraints, language: 'English' });
  expect(input).not.toHaveProperty('display_language');
  expect(input).not.toHaveProperty('author_note');
});
