// Tests for Find Jobs pagination (web/js/pages/findJobs.js, reusing paginate() from lib/trackerView.js
// and the pager / Rows-per-page controls from components/pagination.js). Filter tabs and search are
// server-side (GET /api/jobs, one request each); sort, page and page size only re-slice the loaded
// list. The page size is remembered under jobagent.pageSize.findJobs.
//
// Zero dependencies:  node --test web/tests/findJobsPagination.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { installFakeDom, FakeElement } from './fakeDom.mjs';

installFakeDom();

const { PAGE_SIZE_KEY, PAGE_SIZE_KEYS } = await import('../js/lib/trackerView.js');
const { findJobsPage } = await import('../js/pages/findJobs.js');
const { jobsTable } = await import('../js/components/jobs.js');

// Job n: newer jobs have higher n, and the API returns newest first (like dashboard_data.build_jobs).
// Match is 10..100 in a cycle so "Best match" reorders; every 3rd job is qualified, every 5th tailored.
const makeJobs = (count) => Array.from({ length: count }, (_, i) => {
  const n = count - i;
  return { key: `key${n}`, title: `Job ${n}`, company: `Co ${String(n).padStart(3, '0')}`, link: `https://example.com/jobs/${n}`,
    location: n % 2 ? 'Hyderabad' : 'Pune', posted_date: '2026-10-12', source: 'LinkedIn', match_pct: ((n % 10) + 1) * 10,
    qualified: n % 3 === 0, tailored: n % 5 === 0, state: 'Qualified', tracker_status: null };
});

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
}

// A fake /api/jobs with the server's filter + search + limit semantics; status writes just succeed.
function fakeServer(db) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || 'GET';
    calls.push({ url, method });
    if (method !== 'GET') return { ok: true, status: 200, json: async () => ({ ok: true, result: 'added' }) };
    const p = new URL(url, 'http://x').searchParams;
    const f = p.get('filter') || 'all';
    const q = (p.get('q') || '').toLowerCase();
    let jobs = db.jobs.filter((j) => f === 'all' || (f === 'qualified' && j.qualified) || (f === 'below' && !j.qualified) || (f === 'tailored' && j.tailored));
    if (q) jobs = jobs.filter((j) => `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q));
    const limit = Number(p.get('limit') || 50);
    return { ok: true, status: 200, json: async () => ({ jobs: jobs.slice(0, limit).map((j) => ({ ...j })), total: jobs.length }) };
  };
  return calls;
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const byClass = (root, cls) => root.findAll((e) => (e.attributes.class || '').split(' ').includes(cls));
const titles = (root) => byClass(root, 'cell-title').map((td) => td.textContent);
const countText = (root) => byClass(root, 'result-count')[0]?.textContent;
const navBtn = (root, key) => root.find((e) => e.attributes['data-page-nav'] === key);
const pageLabels = (root) => byClass(root, 'pager-pages')[0].children.map((c) => c.textContent);
const currentPage = (root) => root.find((e) => e.attributes['aria-current'] === 'page')?.textContent;
const sizeSel = (root) => byClass(root, 'page-size-select')[0];
const sortSel = (root) => root.find((e) => e.tagName === 'SELECT' && e.attributes['aria-label'] === 'Sort jobs');
const tab = (root, label) => byClass(root, 'tab').find((t) => t.textContent === label);
const gets = (calls) => calls.filter((c) => c.method === 'GET');
const range = (from, to) => Array.from({ length: Math.abs(to - from) + 1 }, (_, i) => `Job ${from > to ? from - i : from + i}`);
async function choose(sel, value) { sel.value = String(value); await sel.dispatch('change'); await settle(); }
async function click(el) { await el.dispatch('click'); await settle(); }
async function search(root, value) {
  const input = root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search');
  await input.dispatch('input', { target: { value } });
  await new Promise((r) => setTimeout(r, 350));   // search is debounced
}

async function renderFindJobs({ storage = memoryStorage(), query = '' } = {}) {
  globalThis.localStorage = storage;
  const root = new FakeElement('div');
  findJobsPage(root, { query: new URLSearchParams(query) });
  await settle();
  return root;
}

test('default: page 1 of 10 rows in API (newest) order, "Showing 1–10 of N jobs", Previous disabled', async () => {
  const calls = fakeServer({ jobs: makeJobs(45) });
  const root = await renderFindJobs();
  assert.deepEqual(titles(root), range(45, 36));
  assert.equal(countText(root), 'Showing 1–10 of 45 jobs');
  assert.equal(byClass(root, 'result-count')[0].attributes.role, 'status');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(pageLabels(root), ['1', '2', '3', '4', '5']);
  assert.equal(navBtn(root, 'prev').disabled, true);
  assert.equal(navBtn(root, 'next').disabled, false);
  assert.equal(byClass(root, 'pager')[0].attributes['aria-label'], 'Find Jobs pages');
  assert.equal(navBtn(root, '2').attributes['aria-label'], 'Page 2');
  assert.equal(sizeSel(root).attributes['aria-label'], 'Rows per page');
  assert.equal(sizeSel(root).children.find((o) => o.selected).value, '10');
  assert.equal(gets(calls).length, 1);
  assert.match(calls[0].url, /limit=200/);
});

test('Next, Previous and numbered pages move between pages; the short last page disables Next; no refetch', async () => {
  const calls = fakeServer({ jobs: makeJobs(45) });
  const root = await renderFindJobs();
  await click(navBtn(root, 'next'));
  assert.deepEqual(titles(root), range(35, 26));
  assert.equal(countText(root), 'Showing 11–20 of 45 jobs');
  assert.equal(currentPage(root), '2');
  assert.equal(navBtn(root, 'prev').disabled, false);
  await click(navBtn(root, 'prev'));
  assert.deepEqual(titles(root), range(45, 36));
  await click(navBtn(root, '5'));
  assert.deepEqual(titles(root), range(5, 1));
  assert.equal(countText(root), 'Showing 41–45 of 45 jobs');
  assert.equal(navBtn(root, 'next').disabled, true);
  assert.equal(navBtn(root, 'prev').disabled, false);
  assert.equal(calls.length, 1, 'paging only re-slices the loaded jobs');
});

test('rows per page 20 / 50 / 100 return to page 1, are remembered for Find Jobs only, and survive a reload', async () => {
  const calls = fakeServer({ jobs: makeJobs(120) });
  const storage = memoryStorage({ [PAGE_SIZE_KEY]: '50' });
  const root = await renderFindJobs({ storage });
  assert.equal(titles(root).length, 10, 'the Applications page size does not leak into Find Jobs');
  await click(navBtn(root, '3'));
  for (const [size, pages] of [[20, 6], [50, 3], [100, 2]]) {
    await choose(sizeSel(root), size);
    assert.equal(titles(root).length, size, `size ${size}`);
    assert.equal(currentPage(root), '1', 'a page-size change goes back to page 1');
    assert.equal(titles(root)[0], 'Job 120');
    assert.equal(pageLabels(root).at(-1), String(pages));
  }
  await click(navBtn(root, '2'));
  assert.equal(titles(root).length, 20, 'the last page of 100 holds the remaining 20');
  assert.equal(PAGE_SIZE_KEYS.findJobs, 'jobagent.pageSize.findJobs');
  assert.equal(storage.data.get(PAGE_SIZE_KEYS.findJobs), '100');
  assert.equal(storage.data.get(PAGE_SIZE_KEY), '50', 'Applications keeps its own size');
  assert.equal(calls.length, 1);

  const again = await renderFindJobs({ storage });
  assert.equal(sizeSel(again).children.find((o) => o.selected).value, '100');
  assert.equal(titles(again).length, 100);
  assert.equal(currentPage(again), '1', 'the page number itself is not remembered');
});

test('an invalid or unreadable stored page size falls back to 10', async () => {
  fakeServer({ jobs: makeJobs(30) });
  const root = await renderFindJobs({ storage: memoryStorage({ [PAGE_SIZE_KEYS.findJobs]: '25' }) });
  assert.equal(titles(root).length, 10);
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  const root2 = await renderFindJobs({ storage: blocked });
  assert.equal(titles(root2).length, 10);
  await choose(sizeSel(root2), 20);
  assert.equal(titles(root2).length, 20, 'blocked storage still changes the size for this visit');
});

test('search filters on the server before paging and resets to page 1; one page hides the pager', async () => {
  const calls = fakeServer({ jobs: makeJobs(120) });
  const root = await renderFindJobs();
  await click(navBtn(root, '4'));
  await search(root, 'Job 1');   // Job 1, 10-19, 100-120 = 32 matches
  assert.match(calls.at(-1).url, /q=Job(\+|%20)1/);
  assert.equal(countText(root), 'Showing 1–10 of 32 jobs');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(titles(root), range(120, 111));
  await click(navBtn(root, '4'));
  assert.deepEqual(titles(root), ['Job 10', 'Job 1'], 'the last page holds the 2 oldest matches');
  await search(root, 'Job 11');  // Job 11, 110-119 = 11 -> 2 pages
  assert.equal(currentPage(root), '1');
  await search(root, 'Job 12');  // Job 12, 120 = 2 -> one page, no pager
  assert.equal(countText(root), '2 jobs');
  assert.equal(byClass(root, 'pager').length, 0);
});

test('a filter tab refetches, resets to page 1 and the count reflects the filter', async () => {
  const calls = fakeServer({ jobs: makeJobs(120) });
  const root = await renderFindJobs();
  await click(navBtn(root, '12'));
  await click(tab(root, 'Qualified (8+)'));
  assert.match(gets(calls).at(-1).url, /filter=qualified/);
  assert.equal(countText(root), 'Showing 1–10 of 40 jobs');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(titles(root), ['Job 120', 'Job 117', 'Job 114', 'Job 111', 'Job 108', 'Job 105', 'Job 102', 'Job 99', 'Job 96', 'Job 93']);
  assert.equal(tab(root, 'Qualified (8+)').attributes['aria-selected'], 'true');
  assert.equal(tab(root, 'All').attributes['aria-selected'], 'false');
  await click(tab(root, 'Tailored'));
  assert.equal(countText(root), 'Showing 1–10 of 24 jobs');
  assert.equal(pageLabels(root).at(-1), '3');
});

test('sorting applies to the whole result before paging, resets to page 1 and does not refetch', async () => {
  const calls = fakeServer({ jobs: makeJobs(45) });
  const root = await renderFindJobs();
  await click(navBtn(root, '3'));
  await choose(sortSel(root), 'company');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(titles(root), range(1, 10), 'Company A–Z puts Co 001 first, from all 45 jobs, not just page 3');
  await click(navBtn(root, 'next'));
  assert.deepEqual(titles(root), range(11, 20));
  await choose(sortSel(root), 'match');
  const firstPage = byClass(root, 'badge-match').map((b) => b.textContent);
  // jobs 9/19/29/39 are 100%, 8/18/28/38 are 90% -- spread over pages 1-4 in API order
  assert.deepEqual(firstPage, [...Array(4).fill('100%'), ...Array(4).fill('90%'), '80%', '80%'], 'best matches across every page come first');
  assert.equal(gets(calls).length, 1);
});

test('empty results show the empty state without a pager or page numbers', async () => {
  fakeServer({ jobs: makeJobs(30) });
  const root = await renderFindJobs();
  await click(navBtn(root, '3'));
  await search(root, 'nothing like this');
  assert.equal(titles(root).length, 0);
  assert.equal(byClass(root, 'pager').length, 0);
  assert.equal(countText(root), undefined);
  assert.ok(byClass(root, 'state').length > 0, 'the shared empty state is shown');

  fakeServer({ jobs: [] });
  const none = await renderFindJobs();
  assert.equal(byClass(none, 'pager').length, 0);
  assert.equal(titles(none).length, 0);
});

test('a status change reloads but keeps the page; if the result shrank it clamps to the new last page', async () => {
  const db = { jobs: makeJobs(45) };
  const calls = fakeServer(db);
  const root = await renderFindJobs();
  await click(navBtn(root, '3'));
  let status = byClass(root, 'status-select')[0];
  status.value = 'Applied';
  await status.dispatch('change');
  await settle();
  assert.equal(currentPage(root), '3', 'a status update keeps the current page');
  assert.deepEqual(titles(root), range(25, 16));
  assert.deepEqual(calls.filter((c) => c.method !== 'GET').map((c) => c.method), ['POST', 'PATCH']);

  await click(navBtn(root, '5'));
  db.jobs = makeJobs(23);   // e.g. the result set shrank before the reload
  status = byClass(root, 'status-select')[0];
  status.value = 'Applied';
  await status.dispatch('change');
  await settle();
  assert.equal(currentPage(root), '3', 'clamped to the new last page instead of an empty page 5');
  assert.equal(countText(root), 'Showing 21–23 of 23 jobs');
  assert.deepEqual(titles(root), range(3, 1));
});

test('when the API caps the response, the count says how many matched', async () => {
  fakeServer({ jobs: makeJobs(250) });
  const root = await renderFindJobs();
  assert.equal(countText(root), 'Showing 1–10 of 200 jobs (first 200 of 250 loaded)');
  assert.equal(pageLabels(root).at(-1), '20');
});

test('the shared jobsTable itself never paginates: the dashboard\'s Latest Job Matches shows every row it gets', () => {
  const table = jobsTable(makeJobs(25), () => {}, { scroll: true });
  assert.equal(titles(table).length, 25);
  assert.equal(byClass(table, 'pager').length, 0);
  assert.equal(byClass(table, 'page-size-select').length, 0);
});
