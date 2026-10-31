import { API_BASE } from "./base";
export interface ReaderIndex {
  title: string; author: string;
  chapters: Array<{ number: number; title: string; status: string; available: boolean }>;
}
export interface ReaderChapter {
  number: number; title: string; status: string;
  version: "final" | "revised" | "draft" | null;
  markdown: string;
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal, cache: "no-store" });
  if (!response.ok) {
    throw new Error(response.status === 409 ? "正文来源校验失败，未展示可能过期的副本。请返回作品检查。" : response.status === 404 ? "未找到这部作品或章节。" : `读取正文失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}
const path = (id: string) => `/api/projects/${encodeURIComponent(id)}/reader`;
export const readerApi = {
  index: (id: string, signal?: AbortSignal) => read<ReaderIndex>(path(id), signal),
  chapter: (id: string, number: number, signal?: AbortSignal) => read<ReaderChapter>(`${path(id)}/chapters/${number}`, signal),
};
