/** Curated genre chips for New Manuscript - keep the list short and hybrid-friendly. */
export const GENRE_OPTIONS = [
  "Fantasy",
  "Romance",
  "Thriller",
  "Mystery",
  "Sci-Fi",
  "Literary",
  "Horror",
  "Historical",
  "YA",
  "Adult",
  "Dark",
  "Humor",
  "Adventure",
  "Contemporary",
] as const;

const GENRE_LABELS: Record<string, string> = {
  Fantasy: "奇幻",
  Romance: "言情",
  Thriller: "惊悚",
  Mystery: "悬疑",
  "Sci-Fi": "科幻",
  Literary: "文学",
  Horror: "恐怖",
  Historical: "历史",
  YA: "青少年",
  Adult: "成人",
  Dark: "暗黑",
  Humor: "幽默",
  Adventure: "冒险",
  Contemporary: "当代",
};

export function genreLabel(value: string): string {
  return GENRE_LABELS[value] ?? value;
}

/** Merge chip selections + Other into the genres array sent to the API. */
export function mergeGenres(selected: string[], other: string): string[] {
  const out = [...selected];
  const t = other.trim();
  if (t && !out.some((s) => s.toLowerCase() === t.toLowerCase())) out.push(t);
  return out;
}
