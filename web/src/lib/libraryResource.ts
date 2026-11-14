import { api, ApiError, type LibraryChanges, type LibraryPage, type LibraryQuery, type ProjectSummary } from "../api/client";
import { API_BASE } from "../api/base";
import { isProjectTaskRunning } from "./projectTasks";

export const LIBRARY_ACTIVE_INTERVAL = 2_000;
export const LIBRARY_IDLE_INTERVAL = 15_000;
const MAX_CACHED_QUERIES = 24;

type Query = Required<Pick<LibraryQuery, "q" | "status" | "cover" | "sort" | "limit">>;
export interface LibraryView {
  items: ProjectSummary[] | null;
  total: number;
  filteredTotal: number;
  nextCursor: string | null;
  loadingMore: boolean;
  refreshing: boolean;
  error: string | null;
  refreshError: string | null;
  catalogStatus: string;
  observedAt: string | null;
  scope: string;
}
interface Entry {
  query: Query;
  view: LibraryView;
  generation: number;
  request?: Promise<void>;
  controller?: AbortController;
  etag?: string;
  needsRefresh: boolean;
  touched: number;
  scrollTop: number;
}
const emptyView = (): LibraryView => ({ items: null, total: 0, filteredTotal: 0, nextCursor: null,
  loadingMore: false, refreshing: false, error: null, refreshError: null, catalogStatus: "rebuilding", observedAt: "", scope: "" });
const normalize = (query: LibraryQuery): Query => ({ q: query.q?.trim() ?? "", status: query.status ?? "all",
  cover: query.cover ?? "all", sort: query.sort ?? "recent", limit: query.limit ?? 20 });
const queryKey = (query: LibraryQuery) => JSON.stringify(normalize(query));
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** A bounded, scoped read model shared by list routes and navigation. No prose
 * or authoritative recovery state enters this cache. All mutations retain the
 * existing server validation and then invalidate their associated projection. */
export class LibraryResource {
  private scope = "";
  private entries = new Map<string, Entry>();
  private subscribers = new Map<string, Set<() => void>>();
  private cursor: string | null = null;
  private contextRequest?: Promise<void>;
  private syncRequest?: Promise<void>;
  private syncController?: AbortController;
  private generation = 0;
  private deleted = new Map<string, number>();
  private timer?: number;
  private failures = 0;
  private listening = false;
  private moreChanges = false;
  private contextEpoch = 0;

  private key(query: LibraryQuery) { return `${API_BASE}\n${this.scope}\n${queryKey(query)}`; }
  private entry(query: LibraryQuery): Entry {
    const key = this.key(query);
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { query: normalize(query), view: emptyView(), generation: 0, needsRefresh: false, touched: Date.now(), scrollTop: 0 };
      this.entries.set(key, entry);
      this.evict();
    }
    entry.touched = Date.now();
    return entry;
  }
  private evict() {
    if (this.entries.size <= MAX_CACHED_QUERIES) return;
    for (const [key, value] of [...this.entries].sort((a, b) => a[1].touched - b[1].touched)) {
      if (this.subscribers.get(queryKey(value.query))?.size) continue;
      value.controller?.abort();
      this.entries.delete(key);
      if (this.entries.size <= MAX_CACHED_QUERIES) break;
    }
  }
  snapshot = (query: LibraryQuery): LibraryView => this.entry(query).view;
  scrollPosition(query: LibraryQuery) { return this.entry(query).scrollTop; }
  saveScroll(query: LibraryQuery, top: number) { this.entry(query).scrollTop = top; }
  private emit() { for (const listeners of this.subscribers.values()) for (const listener of listeners) listener(); }
  private set(entry: Entry, patch: Partial<LibraryView>) { entry.view = { ...entry.view, ...patch }; this.emit(); }
  private activeEntries() {
    return [...this.subscribers].filter(([, listeners]) => listeners.size).map(([key]) => this.entry(JSON.parse(key) as Query));
  }
  private active() { return [...this.subscribers.values()].some((listeners) => listeners.size > 0); }
  private visible() { return typeof document === "undefined" || document.visibilityState !== "hidden"; }

  subscribe(query: LibraryQuery, listener: () => void) {
    const key = queryKey(query);
    let listeners = this.subscribers.get(key);
    if (!listeners) { listeners = new Set(); this.subscribers.set(key, listeners); }
    listeners.add(listener);
    this.listen();
    void this.context().then(async () => {
      if (!this.subscribers.get(key)?.size) return;
      const entry = this.entry(query);
      if (!entry.view.items || entry.needsRefresh) await this.load(query);
      else await this.synchronize();
      this.schedule();
    }).catch((error: unknown) => {
      const entry = this.entry(query);
      this.set(entry, entry.view.items ? { refreshError: message(error) } : { error: message(error) });
      this.schedule();
    });
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.subscribers.delete(key);
      if (!this.active()) this.unlisten();
    };
  }
  private activateScope(scope: string) {
    if (this.scope === scope) return;
    ++this.generation;
    for (const entry of this.entries.values()) entry.controller?.abort();
    this.syncController?.abort();
    this.syncRequest = undefined;
    this.syncController = undefined;
    this.entries.clear();
    this.deleted.clear();
    this.cursor = null;
    this.scope = scope;
    this.emit();
  }
  context(): Promise<void> {
    if (this.contextRequest) return this.contextRequest;
    const epoch = this.contextEpoch;
    this.contextRequest = api.libraryContext().then(({ scope }) => {
      if (epoch === this.contextEpoch) this.activateScope(scope);
    }).finally(() => { if (epoch === this.contextEpoch) this.contextRequest = undefined; });
    return this.contextRequest;
  }
  private listen() {
    if (this.listening) return;
    this.listening = true;
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("focus", this.onFocus);
  }
  private unlisten() {
    if (this.timer !== undefined) window.clearTimeout(this.timer);
    this.timer = undefined;
    this.syncController?.abort();
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("focus", this.onFocus);
    this.listening = false;
  }
  private onVisibility = () => {
    if (!this.visible()) {
      if (this.timer !== undefined) window.clearTimeout(this.timer);
      this.timer = undefined;
      this.syncController?.abort();
    } else this.onFocus();
  };
  private onFocus = () => {
    if (!this.visible() || !this.active()) return;
    void this.context().then(() => this.synchronize()).finally(() => this.schedule()).catch(() => undefined);
  };
  private schedule(quick = false) {
    if (this.timer !== undefined) window.clearTimeout(this.timer);
    if (!this.active() || !this.visible()) return;
    const busy = this.activeEntries().some(({ view }) => view.catalogStatus === "rebuilding"
      || view.items?.some((p) => isProjectTaskRunning(p.status) || p.cover?.status === "processing" || p.delivery?.status === "pending"));
    const interval = quick || this.moreChanges ? 100 : Math.min(60_000, (busy ? LIBRARY_ACTIVE_INTERVAL : LIBRARY_IDLE_INTERVAL) * 2 ** this.failures);
    this.timer = window.setTimeout(() => {
      this.timer = undefined;
      void this.synchronize().finally(() => this.schedule()).catch(() => undefined);
    }, interval);
  }
  private allowed(item: ProjectSummary) { return !this.deleted.has(item.id) || (item.epoch ?? 0) > this.deleted.get(item.id)!; }
  private incomparable(current: ProjectSummary | undefined, incoming: ProjectSummary) {
    return !!current && current.epoch === incoming.epoch && current.live_boot_epoch === incoming.live_boot_epoch
      && ((incoming.library_revision ?? 0) - (current.library_revision ?? 0))
        * ((incoming.live_revision ?? 0) - (current.live_revision ?? 0)) < 0;
  }
  private merge(current: ProjectSummary | undefined, incoming: ProjectSummary) {
    if (!current || (incoming.epoch ?? 0) > (current.epoch ?? 0)) return incoming;
    if ((incoming.epoch ?? 0) < (current.epoch ?? 0)) return current;
    if ((incoming.library_revision ?? 0) < (current.library_revision ?? 0)) return current;
    if (incoming.live_boot_epoch === current.live_boot_epoch && (incoming.live_revision ?? 0) < (current.live_revision ?? 0)) return current;
    return incoming;
  }
  private acceptPage(entry: Entry, page: LibraryPage, append: boolean) {
    const current = new Map(entry.view.items?.map((item) => [item.id, item]) ?? []);
    const received = page.items.filter((item) => this.allowed(item)).map((item) => this.merge(current.get(item.id), item));
    const receivedById = new Map(received.map((item) => [item.id, item]));
    const items = append ? [
      ...(entry.view.items ?? []).map((item) => receivedById.get(item.id) ?? item),
      ...received.filter((item) => !current.has(item.id)),
    ] : received;
    entry.etag = append ? undefined : page.etag;
    entry.needsRefresh = page.items.some((item) => this.incomparable(current.get(item.id), item));
    this.cursor ??= page.changes_cursor;
    this.set(entry, { items, total: page.total, filteredTotal: page.filtered_total, nextCursor: page.next_cursor,
      scope: page.scope, catalogStatus: page.catalog_status, observedAt: page.observed_at,
      error: null, refreshError: null });
  }
  load(query: LibraryQuery, append = false): Promise<void> {
    const entry = this.entry(query);
    if (entry.request) return entry.request;
    if (append && !entry.view.nextCursor) return Promise.resolve();
    const generation = ++entry.generation;
    const globalGeneration = this.generation;
    const controller = new AbortController();
    entry.controller = controller;
    this.set(entry, append ? { loadingMore: true, refreshError: null } : { refreshing: !!entry.view.items, error: null });
    const cursor = append ? entry.view.nextCursor : undefined;
    const desiredCount = !append ? Math.max(entry.query.limit, entry.view.items?.length ?? 0) : entry.query.limit;
    entry.request = api.library({ ...entry.query, cursor }, { signal: controller.signal,
      etag: !append && desiredCount <= entry.query.limit && !entry.needsRefresh ? entry.etag : undefined }).then(async (page) => {
      if (generation !== entry.generation || globalGeneration !== this.generation || controller.signal.aborted) return;
      if (!page) { this.set(entry, { error: null, refreshError: null }); return; }
      if (page.scope !== this.scope) {
        this.activateScope(page.scope);
        return this.load(query);
      }
      // Reconcile only the window the author explicitly loaded. Publish one
      // coherent window; keep the previous cards visible while fetching it.
      if (!append) {
        let pages = 1;
        const visited = new Set<string>();
        while (desiredCount > entry.query.limit && page.next_cursor && page.items.length < desiredCount && pages < Math.ceil(desiredCount / entry.query.limit)) {
          if (visited.has(page.next_cursor)) throw new Error("作品库返回了重复分页，请重试。");
          visited.add(page.next_cursor);
          const following = await api.library({ ...entry.query, cursor: page.next_cursor }, { signal: controller.signal });
          if (generation !== entry.generation || globalGeneration !== this.generation || controller.signal.aborted) return;
          if (!following || following.scope !== page.scope || following.collection_revision !== page.collection_revision) {
            entry.needsRefresh = true;
            throw new Error("作品列表已变化，正在重新同步。");
          }
          page = { ...page, items: [...page.items, ...following.items], next_cursor: following.next_cursor, etag: undefined };
          pages += 1;
        }
      }
      this.acceptPage(entry, page, append);
      this.failures = 0;
    }).catch(async (error: unknown) => {
      if (generation !== entry.generation || controller.signal.aborted || globalGeneration !== this.generation) return;
      if (append && error instanceof ApiError && error.status === 409) {
        entry.request = undefined;
        entry.needsRefresh = true;
        await this.load(query);
        return;
      }
      this.set(entry, entry.view.items ? { refreshError: message(error) } : { error: message(error) });
    }).finally(() => {
      if (generation === entry.generation && globalGeneration === this.generation) {
        entry.request = undefined;
        entry.controller = undefined;
        this.set(entry, { loadingMore: false, refreshing: false });
        this.schedule();
      }
    });
    return entry.request;
  }
  private applyChanges(delta: LibraryChanges) {
    for (const item of delta.deleted) this.deleted.set(item.id, Math.max(item.epoch, this.deleted.get(item.id) ?? -1));
    const byId = new Map(delta.items.map((item) => [item.id, item]));
    for (const entry of this.entries.values()) {
      if (!entry.view.items) continue;
      const items = entry.view.items.filter((item) => this.allowed(item)).map((item) => {
        const changed = byId.get(item.id);
        return changed && this.allowed(changed) ? this.merge(item, changed) : item;
      });
      const incomparable = entry.view.items.some((item) => {
        const incoming = byId.get(item.id);
        return incoming && this.incomparable(item, incoming);
      });
      entry.needsRefresh ||= delta.membership_changed || delta.reset_required || incomparable;
      const fence = delta.membership_changed || delta.reset_required || incomparable;
      if (fence) {
        // A page captured before this event has obsolete membership even if its
        // per-card revisions are still valid. Fence it before reconciling.
        ++entry.generation;
        entry.controller?.abort();
        entry.request = undefined;
        entry.etag = undefined;
      }
      entry.view = { ...entry.view, ...(fence ? { refreshing: false, loadingMore: false } : {}),
        items, refreshError: null, catalogStatus: delta.catalog_status, observedAt: delta.observed_at };
    }
    this.emit();
  }
  synchronize(): Promise<void> {
    if (this.syncRequest) return this.syncRequest;
    if (!this.visible()) return Promise.resolve();
    if (!this.cursor) return Promise.all(this.activeEntries().map((entry) => this.load(entry.query))).then(() => undefined);
    const generation = this.generation;
    const controller = new AbortController();
    this.syncController = controller;
    const request = (async () => {
      let more = false;
      for (let batch = 0; batch < 5; batch++) {
        const delta = await api.libraryChanges(this.cursor!, { signal: controller.signal });
        if (generation !== this.generation || controller.signal.aborted) return;
        if (delta.scope !== this.scope) {
          this.activateScope(delta.scope);
          await Promise.all(this.activeEntries().map((entry) => this.load(entry.query)));
          return;
        }
        this.applyChanges(delta);
        this.cursor = delta.reset_required ? null : delta.changes_cursor;
        more = delta.has_more && !delta.reset_required;
        if (!more) break;
      }
      await Promise.all(this.activeEntries().filter((entry) => entry.needsRefresh).map((entry) => this.load(entry.query)));
      this.failures = 0;
      this.moreChanges = more;
      if (more) this.schedule(true);
    })().catch((error: unknown) => {
      if (controller.signal.aborted || generation !== this.generation) return;
      this.failures = Math.min(4, this.failures + 1);
      for (const entry of this.activeEntries()) this.set(entry, { refreshError: message(error) });
    }).finally(() => {
      if (this.syncRequest === request) {
        this.syncRequest = undefined;
        this.syncController = undefined;
      }
    });
    this.syncRequest = request;
    return request;
  }
  patch(id: string, patch: Partial<ProjectSummary>) {
    this.cancelObsoleteReads();
    for (const entry of this.entries.values()) {
      if (!entry.view.items) continue;
      this.set(entry, { items: entry.view.items.map((item) => item.id === id ? { ...item, ...patch } : item) });
    }
    this.schedule();
  }
  remove(id: string) {
    const epoch = Math.max(0, ...[...this.entries.values()].flatMap((entry) => entry.view.items?.filter((item) => item.id === id).map((item) => item.epoch ?? 0) ?? []));
    this.cancelObsoleteReads();
    this.deleted.set(id, epoch);
    for (const entry of this.entries.values()) {
      if (!entry.view.items) continue;
      const existed = entry.view.items.some((item) => item.id === id);
      entry.needsRefresh = true;
      this.set(entry, { items: entry.view.items.filter((item) => item.id !== id), total: Math.max(0, entry.view.total - 1),
        filteredTotal: Math.max(0, entry.view.filteredTotal - (existed ? 1 : 0)) });
    }
    void this.synchronize();
  }
  private cancelObsoleteReads() {
    ++this.generation;
    this.syncController?.abort();
    for (const entry of this.entries.values()) {
      ++entry.generation;
      entry.controller?.abort();
      entry.request = undefined;
      entry.etag = undefined;
      entry.view = { ...entry.view, loadingMore: false, refreshing: false };
    }
  }
  refresh(query: LibraryQuery) {
    const entry = this.entry(query);
    entry.needsRefresh = true;
    return this.context().then(() => this.load(query));
  }
  cachedNavigation(query = "") {
    const needle = query.toLocaleLowerCase();
    const result = new Map<string, Pick<ProjectSummary, "id" | "title" | "genre">>();
    for (const entry of this.entries.values()) for (const item of entry.view.items ?? []) {
      if (this.allowed(item) && `${item.title} ${item.genre}`.toLocaleLowerCase().includes(needle)) result.set(item.id, item);
    }
    return [...result.values()].slice(0, 20);
  }
  /** Clear on identity changes/log-out and in isolated regression fixtures. */
  reset() {
    this.unlisten();
    ++this.contextEpoch;
    this.cancelObsoleteReads();
    this.entries.clear();
    this.deleted.clear();
    this.subscribers.clear();
    this.scope = "";
    this.cursor = null;
    this.contextRequest = undefined;
    this.syncRequest = undefined;
    this.failures = 0;
    this.moreChanges = false;
  }
}

export const libraryResource = new LibraryResource();
