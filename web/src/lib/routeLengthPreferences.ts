import type { SourceRouteChoice } from '../api/sourceRoutes';
import type { WorkshopInput } from '../api/workshop';

export function validRouteLengthPreferences(choice: Pick<SourceRouteChoice, 'target_total_words' | 'words_per_chapter' | 'chapters'>) {
  return [choice.target_total_words, choice.words_per_chapter, choice.chapters].every(value => value === undefined || (Number.isSafeInteger(value) && value > 0))
    && (choice.chapters === undefined || choice.chapters <= 511);
}

/** Only author-visible preferences cross the handoff; estimates remain editable. */
export function routeLengthPreferences(choice: SourceRouteChoice): Partial<WorkshopInput> {
  return {
    target_total_words: choice.target_total_words,
    words_per_chapter: choice.words_per_chapter,
    chapters: choice.chapters,
    ...(choice.language?.trim() ? { language: choice.language } : {}),
  };
}
