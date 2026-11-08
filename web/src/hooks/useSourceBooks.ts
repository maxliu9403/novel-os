import { useCallback, useEffect, useState } from "react";
import {
  sourceBooksApi,
  type SourceBook,
  type SourceDetail,
} from "../api/sourceBooks";

export const sourceActive = (book: SourceBook) =>
  ["running", "queued"].includes(book.study.status) ||
  !!book.study.worker_active;

// A failed poll keeps the last result and schedules another read. Hidden tabs
// stop scheduled requests and reconcile immediately on focus/visibility.
function useSourceQuery<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  interval: (data: T) => number,
) {
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data: T;
    checkedAt: number;
  }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    if (!key) return;
    let disposed = false,
      pending = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    async function read() {
      clearTimeout(timer);
      if (pending || disposed) return;
      if (document.visibilityState === "hidden") return;
      pending = true;
      let delay = 5000;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 15000);
      try {
        const data = await load(controller.signal);
        if (disposed) return;
        setSnapshot({ key, data, checkedAt: Date.now() });
        setFailure(undefined);
        delay = interval(data);
      } catch (e) {
        if (!disposed) setFailure({ key, message: String(e) });
      } finally {
        clearTimeout(timeout);
        pending = false;
        if (!disposed) timer = setTimeout(() => void read(), delay);
      }
    }
    const wake = () => {
      void read();
    };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    void read();
    return () => {
      disposed = true;
      controller?.abort();
      clearTimeout(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [key, load, interval, version]);
  return {
    data: snapshot?.key === key ? snapshot.data : undefined,
    checkedAt: snapshot?.key === key ? snapshot.checkedAt : undefined,
    error: failure?.key === key ? failure.message : "",
    refresh,
  };
}
const listDelay = (data: { items: SourceBook[] }) =>
  data.items.some(sourceActive) ? 3000 : 15000;
const detailDelay = (data: SourceDetail) => (sourceActive(data) ? 3000 : 15000);
const loadList = (signal: AbortSignal) => sourceBooksApi.list(signal);
export function useSourceBooks() {
  return useSourceQuery("sources", loadList, listDelay);
}
export function useSourceBook(id: string, version?: string) {
  const load = useCallback(
    (signal: AbortSignal) => sourceBooksApi.get(id, signal, version),
    [id, version],
  );
  return useSourceQuery(
    id ? `${id}:${version ?? "latest"}` : "",
    load,
    detailDelay,
  );
}
const usageDelay = () => 15000;
export function useSourceUsage(id: string) {
  const load = useCallback(
    (signal: AbortSignal) => sourceBooksApi.usage(id, signal),
    [id],
  );
  return useSourceQuery(id, load, usageDelay);
}
