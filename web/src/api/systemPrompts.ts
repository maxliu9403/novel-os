import { API_BASE } from "./base";

export interface PromptVersion {
  version: number;
  markdown: string;
  sha256: string;
  created_at: string | null;
}
export interface SystemPromptRecord {
  id: string;
  revision: number;
  current: PromptVersion;
  versions: PromptVersion[];
  default_markdown: string;
  max_bytes: number;
}

async function request(options?: RequestInit): Promise<SystemPromptRecord> {
  const response = await fetch(`${API_BASE}/api/system-prompts/title-design`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "提示词请求失败，请重试。");
  return data;
}

export const systemPromptsApi = {
  get: () => request(),
  save: (markdown: string, revision: number, operationId: string) => request({
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown, revision, operation_id: operationId }),
  }),
};
