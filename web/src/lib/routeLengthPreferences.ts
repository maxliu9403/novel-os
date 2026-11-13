import type { SourceRouteChoice } from '../api/sourceRoutes';

export function validRouteLengthPreferences(choice: Pick<SourceRouteChoice, 'target_total_words' | 'words_per_chapter' | 'chapters'>) {
  return [choice.target_total_words, choice.words_per_chapter, choice.chapters].every(value => value === undefined || (Number.isSafeInteger(value) && value > 0))
    && (choice.chapters === undefined || choice.chapters <= 511);
}
