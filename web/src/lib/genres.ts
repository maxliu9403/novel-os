/** Genre and story-theme choices; values are preserved in the author's brief. */
export const GENRE_GROUPS = [
  {
    label: "类型与世界",
    options: ["Fantasy", "Xuanhuan", "Romance", "Thriller", "Mystery", "Sci-Fi", "Literary", "Horror", "Historical", "YA", "Adult", "Dark", "Humor", "Adventure", "Contemporary", "Werewolf", "Apocalypse"],
  },
  {
    label: "冲突与成长",
    options: ["Revenge", "Ethical Drama", "Female Growth", "Male Growth", "Domestic Betrayal", "Abuse Survival", "Workplace Comedy"],
  },
  {
    label: "人物与情感",
    options: ["CEO", "Boss Romance", "Mafia", "Celebrity", "Queen / Empress", "Love at First Sight", "Office Romance", "Same-Sex Romance", "Single Parent", "Billionaire", "Pregnancy"],
  },
] as const;

export const GENRE_OPTIONS = GENRE_GROUPS.flatMap((group) => [...group.options]);

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
  Xuanhuan: "玄幻",
  Revenge: "复仇",
  "Ethical Drama": "伦理",
  "Female Growth": "女性成长",
  "Male Growth": "男性成长",
  CEO: "CEO",
  "Boss Romance": "霸总",
  Mafia: "黑手党",
  Werewolf: "狼人",
  "Domestic Betrayal": "家庭背叛",
  Celebrity: "名人明星",
  "Abuse Survival": "受到虐待",
  "Queen / Empress": "皇后女王",
  "Love at First Sight": "一见钟情",
  "Office Romance": "办公室恋情",
  "Workplace Comedy": "职场闹剧",
  "Same-Sex Romance": "同性恋",
  "Single Parent": "单身父母亲",
  Billionaire: "富豪",
  Pregnancy: "怀孕",
  Apocalypse: "末日降临",
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
