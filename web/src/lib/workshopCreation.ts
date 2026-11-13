import type { SourceRouteChoice } from '../api/sourceRoutes';
import type { NarrativeMode, WorkshopInput } from '../api/workshop';

export const WORKSHOP_MODES: Array<[NarrativeMode, string]> = [
  ['short_novel', '短篇 · 连续章节'], ['standalone_long', '长篇 · 连续章节'],
  ['multi_volume', '单本分卷 · 章号连续'], ['series_installment', '多本系列 · 当前分册'],
];

/** Defaults belong to new creations, never to restored sessions or author edits. */
export function newWorkshopInput(seed: Partial<WorkshopInput> = {}): WorkshopInput {
  const fields: Array<keyof WorkshopInput> = ['title', 'author', 'premise', 'genres', 'creative_types', 'length_planning', 'creation_flow',
    'audience_channel', 'language', 'audience', 'market', 'constraints', 'mode', 'source_book', 'chapters', 'words_per_chapter', 'target_total_words', 'method_mode'];
  const supplied = Object.fromEntries(fields.filter(key => seed[key] !== undefined).map(key => [key, seed[key]]));
  const channel = seed.audience_channel === undefined ? 'female' : seed.audience_channel;
  return { title: '', author: '', premise: '', genres: [], creative_types: [],
    length_planning: 'story_first', creation_flow: 'genre_tags.v2', audience_channel: channel,
    language: 'English', audience: channel === 'female' ? '欧美女性读者' : '', market: '欧美', constraints: '', mode: '', ...supplied };
}

/** A route selection carries the same editable input as a creation without references. */
export function routeCreationInput(choice: SourceRouteChoice, fallback: Partial<WorkshopInput> = {}): WorkshopInput {
  return newWorkshopInput({ ...fallback, ...choice.creation_input,
    creative_types: choice.creative_types, genres: choice.genres, audience_channel: choice.audience_channel,
    target_total_words: choice.target_total_words, words_per_chapter: choice.words_per_chapter, chapters: choice.chapters,
    ...(choice.language !== undefined ? { language: choice.language } : {}), source_book: null });
}

export function languageCountUnit(language: string): 'words' | 'characters' | undefined {
  if (/中文|汉语|漢語|chinese|^zh(?:[-_]|$)/i.test(language)) return 'characters';
  if (/英文|英语|英語|english|^en(?:[-_]|$)/i.test(language)) return 'words';
  return undefined;
}
