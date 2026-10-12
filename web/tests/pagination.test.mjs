// Tests for Applications pagination (web/js/lib/trackerView.js paginate/pageList + the page in
// web/js/pages/applications.js). Pagination is client-side: GET /api/tracker already returns every
// row, so a page, page-size, search or sort change only re-slices the loaded rows -- no extra Sheet
// read and never a status write. The page size is remembered under jobagent.pageSize.applications.
//
// Zero dependencies:  node --test web/tests/pagination.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { installFakeDom, FakeElement } from './fakeDom.mjs';

installFakeDom();

const {
  DEFAULT_PAGE_SIZE, PAGE_SIZES, PAGE_SIZE_KEY, SORT_KEYS, loadPageSize, pageList, paginate, savePageSize,
} = await import('../js/lib/trackerView.js');
const { applicationsPage } = await import('../js/pages/applications.js');

const FAKE_SHEET = 'https://docs.google.com/spreadsheets/d/FAKE_sheet_id_0123456789/edit';
const DAY = 86400000;

// Job n is logged n days after 2026-01-01, so newest first = highest n first.
const makeRows = (count, over = () => ({})) => Array.from({ length: count }, (_, i) => {
  const n = i + 1;
  return { row: n + 1, title: `Job ${n}`, company: `Co ${String(n).padStart(3, '0')}`, link: `https://example.com/jobs/${n}`,
    score: String((n % 3) + 8), resume_path: n === 115 ? 'https://drive.google.com/file/d/FAKE_resume/view' : '',
    status: 'Not Applied', timestamp: new Date(Date.UTC(2026, 0, 1) + n * DAY).toISOString().slice(0, 10),
    company_notes: '', source: 'LinkedIn', status_updated: '', ...over(n) };
});

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
}

// A stateful fake /api/tracker: PATCH really changes the row, so a reload sees the new status.
function fakeServer(rows) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || 'GET';
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    if (method === 'PATCH') {
      rows.find((r) => r.link === body.link).status = body.status;
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    const wanted = new URL(url, 'http://x').searchParams.get('status');
    return { ok: true, status: 200, json: async () => ({
      rows: rows.filter((r) => !wanted || r.status === wanted).map((r) => ({ ...r })), total: rows.length,
      tracker: { available: true, error: null, stale: false, configured: true, sheet_url: FAKE_SHEET },
    }) };
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
const range = (from, to) => Array.from({ length: Math.abs(to - from) + 1 }, (_, i) => `Job ${from > to ? from - i : from + i}`);
async function choose(sel, value) { sel.value = String(value); await sel.dispatch('change'); }
async function click(el) { await el.dispatch('click'); await settle(); }
async function search(root, value) {
  const input = root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search');
  await input.dispatch('input', { target: { value } });
  await new Promise((r) => setTimeout(r, 350));   // search is debounced
}

async function renderApps({ storage = memoryStorage(), query = '' } = {}) {
  globalThis.localStorage = storage;
  const root = new FakeElement('div');
  applicationsPage(root, { query: new URLSearchParams(query) });
  await settle();
  return root;
}

// ---- pure helpers -----------------------------------------------------------------------------

test('page sizes are 10, 20, 50, 100 with 10 as the default and a dedicated storage key', () => {
  assert.deepEqual(PAGE_SIZES, [10, 20, 50, 100]);
  assert.equal(DEFAULT_PAGE_SIZE, 10);
  assert.equal(PAGE_SIZE_KEY, 'jobagent.pageSize.applications');
  assert.notEqual(PAGE_SIZE_KEY, SORT_KEYS.applications);
});

test('paginate slices (page-1)*size .. +size and clamps the page into range', () => {
  const rows = Array.from({ length: 25 }, (_, i) => i);
  assert.deepEqual(paginate(rows, 1, 10).items, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  const last = paginate(rows, 3, 10);
  assert.deepEqual([last.page, last.pages, last.start, last.end, last.items], [3, 3, 20, 25, [20, 21, 22, 23, 24]]);
  assert.equal(paginate(rows, 9, 10).page, 3, 'past the end -> last page, never an empty page');
  assert.equal(paginate(rows, 0, 10).page, 1);
  assert.equal(paginate(rows, 'x', 10).page, 1);
  assert.equal(paginate(rows, 1, 7).pageSize, 10, 'unsupported size -> 10');
  assert.deepEqual([paginate([], 4, 10).page, paginate([], 4, 10).pages, paginate([], 4, 10).items], [1, 1, []]);
});

test('pageList keeps at most 7 entries: first, last, current ±1 and gaps', () => {
  assert.deepEqual(pageList(1, 1), [1]);
  assert.deepEqual(pageList(3, 7), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(pageList(1, 9), [1, 2, 3, 4, 5, null, 9]);
  assert.deepEqual(pageList(5, 9), [1, null, 4, 5, 6, null, 9]);
  assert.deepEqual(pageList(9, 9), [1, null, 5, 6, 7, 8, 9]);
  assert.deepEqual(pageList(4, 9), [1, 2, 3, 4, 5, null, 9]);
  assert.deepEqual(pageList(50, 100), [1, null, 49, 50, 51, null, 100]);
  for (let p = 1; p <= 40; p++) assert.ok(pageList(p, 40).length <= 7 && pageList(p, 40).includes(p));
});

test('page-size preference: valid values round-trip; missing, invalid or blocked storage means 10', () => {
  const store = memoryStorage();
  assert.equal(loadPageSize(store), 10);
  savePageSize(50, store);
  assert.equal(store.data.get(PAGE_SIZE_KEY), '50');
  assert.equal(loadPageSize(store), 50);
  for (const bad of ['7', '1000', 'abc', '', '-10']) assert.equal(loadPageSize(memoryStorage({ [PAGE_SIZE_KEY]: bad })), 10, bad);
  const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(loadPageSize(blocked), 10);
  assert.doesNotThrow(() => savePageSize(20, blocked));
  savePageSize(33, store);
  assert.equal(store.data.get(PAGE_SIZE_KEY), '10', 'only a supported size is ever stored');
});

// ---- the Applications page ----------------------------------------------------------------------

test('default: 10 rows, newest first, "Showing 1–10 of N", Previous disabled, page 1 current', async () => {
  fakeServer(makeRows(115));
  const root = await renderApps();
  assert.deepEqual(titles(root), range(115, 106));
  assert.equal(countText(root), 'Showing 1–10 of 115 applications');
  assert.equal(byClass(root, 'result-count')[0].attributes.role, 'status');
  assert.equal(navBtn(root, 'prev').disabled, true);
  assert.equal(navBtn(root, 'next').disabled, false);
  assert.equal(currentPage(root), '1');
  assert.deepEqual(pageLabels(root), ['1', '2', '3', '4', '5', '…', '12']);
  assert.equal(navBtn(root, '1').tagName, 'BUTTON');
  assert.equal(navBtn(root, '1').attributes['aria-label'], 'Page 1');
  assert.equal(sizeSel(root).attributes['aria-label'], 'Rows per page');
  assert.equal(sizeSel(root).children.find((o) => o.selected).value, '10');
});

test('Next, Previous and a numbered page move between pages; the last page is short and Next is disabled', async () => {
  const calls = fakeServer(makeRows(115));
  const root = await renderApps();
  await click(navBtn(root, 'next'));
  assert.deepEqual(titles(root), range(105, 96));
  assert.equal(countText(root), 'Showing 11–20 of 115 applications');
  assert.equal(currentPage(root), '2');
  assert.equal(navBtn(root, 'prev').disabled, false);
  await click(navBtn(root, 'prev'));
  assert.deepEqual(titles(root), range(115, 106));
  await click(navBtn(root, '12'));
  assert.deepEqual(titles(root), range(5, 1));
  assert.equal(countText(root), 'Showing 111–115 of 115 applications');
  assert.equal(navBtn(root, 'next').disabled, true);
  assert.equal(currentPage(root), '12');
  assert.equal(calls.length, 1, 'changing pages never refetches the Sheet');
  assert.ok(calls.every((c) => c.method === 'GET'), 'and never writes a status');
});

test('rows per page 20 / 50 / 100 show up to that many rows and the choice survives a reload', async () => {
  const calls = fakeServer(makeRows(115));
  const storage = memoryStorage();
  const root = await renderApps({ storage });
  await click(navBtn(root, '3'));
  for (const [size, pages] of [[20, 6], [50, 3], [100, 2]]) {
    await choose(sizeSel(root), size);
    assert.equal(titles(root).length, size, `size ${size}`);
    assert.equal(titles(root)[0], 'Job 115', 'a page-size change goes back to page 1');
    assert.equal(currentPage(root), '1');
    assert.equal(pageLabels(root).at(-1), String(pages));
  }
  await click(navBtn(root, '2'));
  assert.equal(titles(root).length, 15, 'the last page of 100 holds the remaining 15');
  assert.equal(storage.data.get(PAGE_SIZE_KEY), '100');
  assert.equal(calls.length, 1);

  const again = await renderApps({ storage });
  assert.equal(sizeSel(again).children.find((o) => o.selected).value, '100');
  assert.equal(titles(again).length, 100);
  assert.equal(currentPage(again), '1', 'the page number itself is not remembered');
});

test('an invalid stored page size falls back to 10', async () => {
  fakeServer(makeRows(30));
  const root = await renderApps({ storage: memoryStorage({ [PAGE_SIZE_KEY]: '25' }) });
  assert.equal(titles(root).length, 10);
  assert.equal(sizeSel(root).children.find((o) => o.selected).value, '10');
});

test('search filters before paging, counts the matches, and resets to page 1', async () => {
  const calls = fakeServer(makeRows(115));
  const root = await renderApps();
  await click(navBtn(root, '3'));
  await search(root, 'Job 1');   // Job 1, 10-19, 100-115 = 27 matches
  assert.equal(countText(root), 'Showing 1–10 of 27 applications');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(titles(root), range(115, 106));
  await click(navBtn(root, '3'));
  assert.deepEqual(titles(root), ['Job 19', 'Job 18', 'Job 17', 'Job 16', 'Job 15', 'Job 14', 'Job 13', 'Job 12', 'Job 11', 'Job 10', 'Job 1'].slice(4));
  await search(root, 'Job 11');  // Job 11, 110-115 = 7 -> one page, no pager
  assert.equal(countText(root), '7 applications');
  assert.equal(byClass(root, 'pager').length, 0);
  await search(root, 'nothing like this');
  assert.equal(byClass(root, 'pager').length, 0);
  assert.equal(titles(root).length, 0);
  assert.equal(calls.length, 1);
});

test('sorting applies before paging and resets to page 1; newest first stays the default', async () => {
  fakeServer(makeRows(115));
  const root = await renderApps();
  await click(navBtn(root, '2'));
  await choose(byClass(root, 'sort-select')[0], 'oldest');
  assert.equal(currentPage(root), '1');
  assert.deepEqual(titles(root), range(1, 10));
  await click(navBtn(root, 'next'));
  assert.deepEqual(titles(root), range(11, 20));
});

test('the status filter is applied before paging and the count reflects it', async () => {
  fakeServer(makeRows(115, (n) => ({ status: n % 5 === 0 ? 'Applied' : 'Not Applied' })));
  const root = await renderApps({ query: 'status=Applied' });
  assert.equal(countText(root), 'Showing 1–10 of 23 applications');
  assert.deepEqual(titles(root), ['Job 115', 'Job 110', 'Job 105', 'Job 100', 'Job 95', 'Job 90', 'Job 85', 'Job 80', 'Job 75', 'Job 70']);
  await click(navBtn(root, '3'));
  assert.deepEqual(titles(root), ['Job 15', 'Job 10', 'Job 5']);
});

test('a status change that empties the last page clamps to the new last page; the change is the only write', async () => {
  const rows = makeRows(111);
  const calls = fakeServer(rows);
  const root = await renderApps({ query: 'status=Not Applied' });
  await click(navBtn(root, '12'));
  assert.deepEqual(titles(root), ['Job 1']);
  const status = byClass(root, 'status-select')[0];
  status.value = 'Applied';
  await status.dispatch('change');
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').map((c) => c.body), [{ link: 'https://example.com/jobs/1', status: 'Applied' }]);
  assert.equal(rows[0].status, 'Applied');
  assert.equal(currentPage(root), '11', 'clamped, not an empty page 12');
  assert.deepEqual(titles(root), range(11, 2));
  assert.equal(countText(root), 'Showing 101–110 of 110 applications');
});

test('Refresh refetches once and goes back to page 1; the sheet link and resume links still render', async () => {
  const calls = fakeServer(makeRows(115));
  const root = await renderApps();
  assert.equal(byClass(root, 'sheet-link')[0].attributes.href, FAKE_SHEET);
  assert.equal(root.find((e) => e.tagName === 'A' && e.textContent === 'Open resume').attributes.href, 'https://drive.google.com/file/d/FAKE_resume/view');
  await click(navBtn(root, '4'));
  const refresh = root.find((e) => e.tagName === 'BUTTON' && e.textContent === 'Refresh');
  await click(refresh);
  assert.equal(calls.filter((c) => c.method === 'GET').length, 2);
  assert.equal(currentPage(root), '1');
  assert.equal(byClass(root, 'sheet-link').length, 1);
});
