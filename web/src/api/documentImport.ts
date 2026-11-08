import { API_BASE } from "./base";

export interface ImportedDocument {
  filename: string;
  text: string;
  characters: number;
  pages: number | null;
  warnings: string[];
}

export async function importDocument(
  file: File,
  signal: AbortSignal,
): Promise<ImportedDocument> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE}/api/document-import`, {
    method: "POST",
    body,
    signal,
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : `文件导入失败（${response.status}）`,
    );
  }
  return response.json();
}
