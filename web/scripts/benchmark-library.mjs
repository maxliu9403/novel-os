/** Isolated browser acceptance: synthetic 1,000-book API only. Never contacts
 * another origin or an existing Novel OS API. Start `npm run preview -- --port
 * 5199 --strictPort` first. PLAYWRIGHT_MODULE may point to an installed module. */
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = 'http://127.0.0.1:5199';
const books = Array.from({ length: 1000 }, (_, i) => ({
  id: `fixture-${String(i).padStart(4, '0')}`, title: `Fixture novel ${i}`, genre: 'Romance', author: 'Synthetic author',
  chapter_count: 100, drafted_count: 100, word_count: 300000, count_unit: 'words', language: 'English', status: 'completed',
  task_id: `fixture-task-${i}`, task_started_at: '2026-11-14T00:00:00Z', library_revision: 1, epoch: 1,
  live_revision: 0, live_boot_epoch: 'synthetic', freshness: 'fresh',
  cover: { status: 'not_started', detail: '', candidate_count: 0 },
  delivery: { status: 'missing', retryable: false },
}));
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const results = [];
try {
  for (let trial = 0; trial < 10; trial++) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const requests = [];
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await context.addInitScript(() => {
      window.__libraryTiming = { firstCards: null, returnCards: null, returningAt: null, longTasks: [] };
      new PerformanceObserver((list) => window.__libraryTiming.longTasks.push(...list.getEntries().map((entry) => entry.duration))).observe({ type: 'longtask', buffered: true });
      new MutationObserver(() => {
        if (document.querySelectorAll('article.library-book').length) {
          window.__libraryTiming.firstCards ??= performance.now();
          if (window.__libraryTiming.returningAt !== null) window.__libraryTiming.returnCards ??= performance.now() - window.__libraryTiming.returningAt;
        }
      }).observe(document, { childList: true, subtree: true });
    });
    await context.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (!url.pathname.startsWith('/api/')) return route.continue();
      requests.push(url.pathname + url.search);
      let body;
      if (url.pathname === '/api/library/context') body = { scope: 'synthetic-1000' };
      else if (url.pathname === '/api/library') {
        const start = Number(url.searchParams.get('cursor') || 0);
        const limit = Number(url.searchParams.get('limit') || 20);
        const q = url.searchParams.get('q')?.toLowerCase() || '';
        const filtered = books.filter((book) => book.title.toLowerCase().includes(q));
        body = { items: filtered.slice(start, start + limit), next_cursor: start + limit < filtered.length ? String(start + limit) : null,
          total: books.length, filtered_total: filtered.length, collection_revision: '1:synthetic', changes_cursor: 'delta-1', observed_at: '2026-11-14T00:00:00Z', catalog_status: 'ready', scope: 'synthetic-1000' };
      } else if (url.pathname === '/api/library/changes') body = { items: [], deleted: [], reset_required: false, membership_changed: false, has_more: false, changes_cursor: 'delta-1', collection_revision: '1:synthetic', scope: 'synthetic-1000', catalog_status: 'ready', observed_at: '2026-11-14T00:00:01Z' };
      else if (url.pathname === '/api/studio/llm') body = { configured: true, onboarding_completed: true };
      else return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unmocked API is deliberately unavailable.' }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('article.library-book').length === 20);
    if (trial === 0 && process.env.LIBRARY_SCREENSHOT_PREFIX) await page.screenshot({ path: `${process.env.LIBRARY_SCREENSHOT_PREFIX}-desktop.png` });
    await page.evaluate(() => {
      const main = document.querySelector('#main');
      main.scrollTop = 600;
      history.pushState({}, '', '/library-benchmark-away');
      dispatchEvent(new PopStateEvent('popstate'));
    });
    await page.waitForFunction(() => document.querySelectorAll('article.library-book').length === 0);
    await page.evaluate(() => {
      window.__libraryTiming.returningAt = performance.now();
      history.pushState({}, '', '/');
      dispatchEvent(new PopStateEvent('popstate'));
    });
    await page.waitForFunction(() => document.querySelectorAll('article.library-book').length === 20);
    const restoredScroll = await page.locator('#main').evaluate((element) => element.scrollTop);
    await page.getByRole('button', { name: '加载更多作品' }).click();
    await page.waitForFunction(() => document.querySelectorAll('article.library-book').length === 40);
    const timing = await page.evaluate(() => window.__libraryTiming);
    let mobile = null;
    if (trial === 0) {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.querySelector('#main').scrollTop = 0; });
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      mobile = await page.evaluate(() => ({ viewportWidth: innerWidth, contentWidth: document.documentElement.scrollWidth }));
      if (process.env.LIBRARY_SCREENSHOT_PREFIX) await page.screenshot({ path: `${process.env.LIBRARY_SCREENSHOT_PREFIX}-mobile.png` });
    }
    results.push({ trial: trial + 1, ...timing, restoredScroll, cardCountAfterMore: 40, mobile, requests, errors });
    await context.close();
  }
} finally { await browser.close(); }
const percentile = (values, p) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * p) - 1];
const summary = {
  fixture: '1,000 synthetic projects; 100 chapters/project represented only by summary; real production frontend build; mocked API; no cover images; fresh browser context each trial',
  samples: results.length,
  firstCardsMs: { p50: percentile(results.map((r) => r.firstCards), .5), p95: percentile(results.map((r) => r.firstCards), .95) },
  cachedReturnMs: { p50: percentile(results.map((r) => r.returnCards), .5), p95: percentile(results.map((r) => r.returnCards), .95) },
  passed: results.every((r) => r.firstCards <= 1000 && r.returnCards <= 100 && r.restoredScroll === 600 && r.errors.length === 0 && (!r.mobile || r.mobile.contentWidth <= r.mobile.viewportWidth) && !r.requests.some((u) => u.startsWith('/api/projects')) && r.requests.filter((u) => u.startsWith('/api/library?')).length === 2),
  results,
};
console.log(JSON.stringify(summary, null, 2));
if (process.env.LIBRARY_PERFORMANCE_OUTPUT) await writeFile(process.env.LIBRARY_PERFORMANCE_OUTPUT, JSON.stringify(summary, null, 2));
if (!summary.passed) process.exitCode = 1;
