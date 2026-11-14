import { useCallback, useSyncExternalStore } from "react";
import type { LibraryQuery } from "../api/client";
import { libraryResource } from "./libraryResource";

export function useLibrary(query: LibraryQuery) {
  const key = JSON.stringify(query);
  const subscribe = useCallback((listener: () => void) => libraryResource.subscribe(JSON.parse(key) as LibraryQuery, listener), [key]);
  const snapshot = useCallback(() => libraryResource.snapshot(JSON.parse(key) as LibraryQuery), [key]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
