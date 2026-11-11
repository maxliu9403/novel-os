export function readLocal<T>(key: string): T | undefined {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? undefined; } catch { return undefined; }
}
export function writeLocal(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Server receipts remain authoritative. */ }
}
export function newOperationId() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `route-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
// Repeated delivery of one payload resumes its durable server operation. A new
// author submission clears this receipt only after an acknowledged response.
export function operationFor<T extends object>(key: string, payload: T): T & { operation_id: string } {
  const prior = readLocal<{ signature: string; operation_id: string }>(key);
  const signature = JSON.stringify(payload);
  const operation_id = prior?.signature === signature ? prior.operation_id : newOperationId();
  writeLocal(key, { signature, operation_id });
  return { ...payload, operation_id };
}
export function clearLocal(key: string) {
  try { localStorage.removeItem(key); } catch { /* Optional browser storage. */ }
}
