// Tests for the Applications sort control, DATE column and the "Open Google Sheet" action
// (web/js/lib/trackerView.js + web/js/pages/applications.js).
//
// Sorting is client-side over the rows already loaded from GET /api/tracker: changing it must not
// refetch the Sheet or PATCH a status. The sort choice is written to browser storage (option name only).
//
// Zero dependencies:  node --test web/tests/trackerView.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { installFakeDom, FakeElement } from './fakeDom.mjs';

installFakeDom();

const {
  DEFAULT_SORT, SORT_KEYS, SORT_OPTIONS, formatDateMMDDYYYY, loadSort, rowTime, saveSort, sheetHref, sortRows,
} = await import('../js/lib/trackerView.js');
const { applicationsPage, sheetAction } = await import('../js/pages/applications.js');

const FAKE_SHEET = 'https://docs.google.com/spreadsheets/d/FAKE_sheet_id_0123456789/edit';

const row = (n, over = {}) => ({
  row: n, title: `Role ${n}`, company: `Company ${n}`, link: `https://example.com/jobs/${n}`, score: '8',
  resume_path: '', status: 'Not Applied', timestamp: '2026-09-01', company_notes: '', source: 'LinkedIn',
  status_updated: '', ...over,
});
const titles = (rows) => rows.map((r) => r.title);

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
}

// ---- sorting ------------------------------------------------------------------------------

test('the sort options are exactly the five supported ones, Newest first by default', () => {
  assert.deepEqual(SORT_OPTIONS.map((o) => o.label), ['Newest first', 'Oldest first', 'Best match', 'Company A–Z', 'Role A–Z']);
  assert.equal(DEFAULT_SORT, 'newest');
});

test('rowTime reads the Logged date by value (not as text); missing or invalid dates are null', () => {
  assert.equal(rowTime(row(1, { timestamp: '2026-09-01' })), Date.UTC(2026, 8, 1));
  assert.ok(rowTime(row(1, { timestamp: '9/2/2026' })) > rowTime(row(1, { timestamp: '2026-09-01' })));
  assert.equal(rowTime(row(1, { timestamp: '' })), null);
  assert.equal(rowTime(row(1, { timestamp: 'not a date' })), null);
});

test('newest first orders by actual date, not string order, and puts undated rows last', () => {
  const rows = [
    row(2, { title: 'Sep 2 (US format)', timestamp: '9/2/2026' }),
    row(3, { title: 'Undated', timestamp: '' }),
    row(4, { title: 'Aug 31', timestamp: '2026-08-31' }),
    row(5, { title: 'Oct 1', timestamp: '2026-10-01' }),
    row(6, { title: 'Garbage date', timestamp: 'soon' }),
  ];
  assert.deepEqual(titles(sortRows(rows, 'newest')), ['Oct 1', 'Sep 2 (US format)', 'Aug 31', 'Garbage date', 'Undated'], 'undated rows tie, then newest Sheet row first');
});

test('oldest first reverses the dated order but still keeps undated rows last', () => {
  const rows = [row(2, { title: 'B', timestamp: '2026-09-02' }), row(3, { title: 'none', timestamp: '' }), row(4, { title: 'A', timestamp: '2026-08-31' })];
  assert.deepEqual(titles(sortRows(rows, 'oldest')), ['A', 'B', 'none']);
});

test('equal dates are ordered deterministically by Sheet row (later row = logged later)', () => {
  const rows = [row(7, { title: 'r7' }), row(3, { title: 'r3' }), row(9, { title: 'r9' })];
  assert.deepEqual(titles(sortRows(rows, 'newest')), ['r9', 'r7', 'r3']);
  assert.deepEqual(titles(sortRows(rows, 'oldest')), ['r3', 'r7', 'r9']);
});

test('best match orders by fit score (numeric), ties newest first, unscored last', () => {
  const rows = [
    row(2, { title: '9 old', score: '9', timestamp: '2026-08-01' }),
    row(3, { title: 'none', score: '' }),
    row(4, { title: '10', score: '10' }),
    row(5, { title: '9 new', score: '9', timestamp: '2026-09-20' }),
    row(6, { title: '8', score: '8' }),
  ];
  assert.deepEqual(titles(sortRows(rows, 'score')), ['10', '9 new', '9 old', '8', 'none']);
});

test('company and role sorts are case-insensitive A–Z with blanks last', () => {
  const rows = [row(2, { title: 'zeta', company: 'impact.com' }), row(3, { title: 'Alpha', company: 'Apex' }),
    row(4, { title: 'beta', company: '' }), row(5, { title: 'Gamma', company: 'OPERS' })];
  assert.deepEqual(sortRows(rows, 'company').map((r) => r.company), ['Apex', 'impact.com', 'OPERS', '']);
  assert.deepEqual(titles(sortRows(rows, 'role')), ['Alpha', 'beta', 'Gamma', 'zeta']);
});

test('sortRows does not mutate its input or any row (statuses untouched); unknown sort = newest', () => {
  const rows = [row(2, { timestamp: '2026-08-01', status: 'Applied' }), row(3, { timestamp: '2026-09-01' })];
  const before = JSON.stringify(rows);
  assert.deepEqual(titles(sortRows(rows, 'bogus')), ['Role 3', 'Role 2']);
  assert.equal(JSON.stringify(rows), before);
});

// ---- preference persistence ----------------------------------------------------------------

test('the sort preference falls back to Newest first when missing or invalid', () => {
  assert.deepEqual(SORT_KEYS, { applications: 'jobagent.sort.applications' });
  const key = SORT_KEYS.applications;
  assert.equal(loadSort(key, memoryStorage({ [key]: 'score' })), 'score');
  assert.equal(loadSort(key, memoryStorage({ [key]: '<script>' })), 'newest');
  assert.equal(loadSort(key, memoryStorage()), 'newest');
  const store = memoryStorage();
  saveSort(key, 'company', store);
  assert.equal(store.data.get(key), 'company');
  saveSort(key, 'javascript:alert(1)', store);
  assert.equal(store.data.get(key), 'newest', 'only a known option name is ever stored');
});

test('blocked storage never breaks the page: reads default, writes are dropped', () => {
  const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(loadSort(SORT_KEYS.applications, blocked), 'newest');
  assert.doesNotThrow(() => saveSort(SORT_KEYS.applications, 'oldest', blocked));
  assert.equal(loadSort(SORT_KEYS.applications, null), 'newest');
});

// ---- Google Sheet link ------------------------------------------------------------------------

test('only an https docs.google.com spreadsheet URL is accepted', () => {
  assert.equal(sheetHref(FAKE_SHEET), FAKE_SHEET);
  for (const bad of [null, '', 'not a url', 'http://docs.google.com/spreadsheets/d/FAKE_sheet_id_0123456789/edit',
    'https://evil.example.com/spreadsheets/d/FAKE/edit', 'https://docs.google.com.evil.example.com/spreadsheets/d/x',
    'https://docs.google.com/document/d/FAKE_doc/edit', 'https://user:pw@docs.google.com/spreadsheets/d/x/edit',
    'javascript:alert(1)//docs.google.com/spreadsheets/d/x']) {
    assert.equal(sheetHref(bad), null, String(bad));
  }
});

test('the sheet action opens a valid link in a new tab, or explains when none is configured', () => {
  const a = sheetAction(FAKE_SHEET);
  assert.equal(a.tagName, 'A');
  assert.equal(a.attributes.href, FAKE_SHEET);
  assert.equal(a.attributes.target, '_blank');
  assert.match(a.attributes.rel, /noopener/);
  assert.match(a.attributes['aria-label'], /Open Google Sheet/);
  for (const url of [null, 'https://evil.example.com/x']) {
    const note = sheetAction(url);
    assert.notEqual(note.tagName, 'A');
    assert.equal(note.textContent, 'Google Sheets link is not configured.');
  }
});

// ---- pages ------------------------------------------------------------------------------------

const ROWS = [
  row(2, { title: 'Old applied', company: 'Zeta', status: 'Applied', timestamp: '2026-08-01', score: '10' }),
  row(3, { title: 'New applied', company: 'Acme', status: 'Applied', timestamp: '2026-09-20', score: '8' }),
  row(4, { title: 'Mid open', company: 'Beta', status: 'Not Applied', timestamp: '2026-09-01', score: '9' }),
  row(5, { title: 'Newest open', company: 'Delta', status: 'Not Applied', timestamp: '2026-10-01', score: '8' }),
  row(6, { title: 'Undated open', company: 'Alpha', status: 'Not Applied', timestamp: '', score: '9' }),
];

function fakeServer({ rows = ROWS, tracker = {} } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined });
    if ((init.method || 'GET') === 'PATCH') return { ok: true, status: 200, json: async () => ({ ok: true }) };
    const wanted = new URL(url, 'http://x').searchParams.get('status');
    return { ok: true, status: 200, json: async () => ({
      rows: rows.filter((r) => !wanted || r.status === wanted), total: rows.length,
      tracker: { available: true, error: null, stale: false, configured: true, sheet_url: FAKE_SHEET, ...tracker },
    }) };
  };
  return calls;
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const byClass = (root, cls) => root.findAll((e) => (e.attributes.class || '').split(' ').includes(cls));
const sortSel = (root) => byClass(root, 'sort-select')[0];
const tableTitles = (root) => byClass(root, 'cell-title').map((td) => td.textContent);
async function choose(sel, value) { sel.value = value; await sel.dispatch('change'); }

async function renderPage(page, { storage = memoryStorage(), query = '' } = {}) {
  globalThis.localStorage = storage;
  const root = new FakeElement('div');
  page(root, { query: new URLSearchParams(query) });
  await settle();
  return root;
}

test('Applications: newest first by default, with exactly one h1 and the sheet link in the header', async () => {
  fakeServer();
  const root = await renderPage(applicationsPage);
  assert.deepEqual(tableTitles(root), ['Newest open', 'New applied', 'Mid open', 'Old applied', 'Undated open']);
  assert.equal(root.findAll((e) => e.tagName === 'H1').length, 1);
  assert.equal(sortSel(root).children.find((o) => o.selected).value, 'newest');
  assert.equal(byClass(root, 'sheet-link')[0].attributes.href, FAKE_SHEET);
});

test('Applications: changing the sort re-renders from memory -- no refetch, no PATCH -- and is remembered', async () => {
  const calls = fakeServer();
  const storage = memoryStorage();
  const root = await renderPage(applicationsPage, { storage });
  assert.equal(calls.length, 1);
  await choose(sortSel(root), 'oldest');
  assert.deepEqual(tableTitles(root), ['Old applied', 'Mid open', 'New applied', 'Newest open', 'Undated open']);
  await choose(sortSel(root), 'score');
  assert.deepEqual(tableTitles(root), ['Old applied', 'Mid open', 'Undated open', 'Newest open', 'New applied']);
  await choose(sortSel(root), 'company');
  assert.deepEqual(tableTitles(root), ['New applied', 'Undated open', 'Mid open', 'Newest open', 'Old applied']);
  assert.equal(calls.length, 1, 'sorting never reads the Sheet again');
  assert.ok(calls.every((c) => c.method === 'GET'));
  assert.equal(storage.data.get(SORT_KEYS.applications), 'company');

  const again = await renderPage(applicationsPage, { storage });
  assert.equal(sortSel(again).children.find((o) => o.selected).value, 'company');
  assert.deepEqual(tableTitles(again), ['New applied', 'Undated open', 'Mid open', 'Newest open', 'Old applied']);
});

test('Applications: the status filter and search combine with the sort', async () => {
  fakeServer();
  const root = await renderPage(applicationsPage, { query: 'status=Not Applied', storage: memoryStorage({ [SORT_KEYS.applications]: 'oldest' }) });
  assert.deepEqual(tableTitles(root), ['Mid open', 'Newest open', 'Undated open']);
  const input = root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search');
  await input.dispatch('input', { target: { value: 'open' } });
  await new Promise((r) => setTimeout(r, 350));   // search is debounced
  assert.deepEqual(tableTitles(root), ['Mid open', 'Newest open', 'Undated open']);
  await input.dispatch('input', { target: { value: 'delta' } });
  await new Promise((r) => setTimeout(r, 350));
  assert.deepEqual(tableTitles(root), ['Newest open']);
});

test('Applications: no configured sheet link shows the note instead of a link', async () => {
  fakeServer({ tracker: { sheet_url: null } });
  const root = await renderPage(applicationsPage);
  assert.equal(byClass(root, 'sheet-link').length, 0);
  assert.equal(byClass(root, 'sheet-missing')[0].textContent, 'Google Sheets link is not configured.');
});

test('Applications: a status change still PATCHes the tracker and reloads; the sort is kept', async () => {
  const calls = fakeServer();
  const storage = memoryStorage({ [SORT_KEYS.applications]: 'role' });
  const root = await renderPage(applicationsPage, { storage });
  assert.deepEqual(tableTitles(root), ['Mid open', 'New applied', 'Newest open', 'Old applied', 'Undated open']);
  const status = byClass(root, 'status-select').find((s) => s.attributes['aria-label'] === 'Status for Mid open');
  status.value = 'Applied';
  await status.dispatch('change');
  await settle();
  const patch = calls.filter((c) => c.method === 'PATCH');
  assert.deepEqual(patch.map((c) => [c.url, c.body]), [['/api/tracker/status', { link: 'https://example.com/jobs/4', status: 'Applied' }]]);
  assert.equal(calls.filter((c) => c.method === 'GET').length, 2, 'reloaded once after the status change');
  assert.equal(sortSel(root).children.find((o) => o.selected).value, 'role');
  assert.equal(storage.data.get(SORT_KEYS.applications), 'role');
});

// ---- DATE column (MM/DD/YYYY) -----------------------------------------------------------------

test('dates format as MM/DD/YYYY with leading zeros', () => {
  assert.equal(formatDateMMDDYYYY('2026-10-12'), '10/12/2026');
  assert.equal(formatDateMMDDYYYY('2026-08-18'), '08/18/2026');
  assert.equal(formatDateMMDDYYYY('2026-09-02'), '09/02/2026');
  assert.equal(formatDateMMDDYYYY('2026-01-05'), '01/05/2026');
  assert.equal(formatDateMMDDYYYY('2026-08-05'), '08/05/2026');
  assert.equal(formatDateMMDDYYYY(' 2026-01-05 '), '01/05/2026');
  assert.equal(formatDateMMDDYYYY('2026-10-12T00:00:00Z'), '10/12/2026');
  assert.equal(formatDateMMDDYYYY('2026-10-12T00:00:00+00:00'), '10/12/2026');
  assert.equal(formatDateMMDDYYYY('9/2/2026'), '09/02/2026', 'a Sheet-formatted US date is parsed, not sliced');
  assert.equal(formatDateMMDDYYYY('September 2, 2026'), '09/02/2026');
});

test('missing, invalid and impossible dates format as empty (and sort as undated)', () => {
  for (const bad of [undefined, null, '', '   ', 'soon', 'today', '2026-02-30', '2026-13-01', '2026-00-10']) {
    assert.equal(formatDateMMDDYYYY(bad), '', String(bad));
    assert.equal(rowTime({ timestamp: bad }), null, String(bad));
  }
});

test('date-only values never shift a day whatever the local timezone', () => {
  const tz = process.env.TZ;
  try {
    for (const zone of ['America/Los_Angeles', 'Asia/Kolkata', 'Pacific/Kiritimati', 'UTC']) {
      process.env.TZ = zone;
      assert.equal(formatDateMMDDYYYY('2026-10-12'), '10/12/2026', zone);
      assert.equal(formatDateMMDDYYYY('2026-01-01'), '01/01/2026', zone);
      assert.equal(formatDateMMDDYYYY('9/2/2026'), '09/02/2026', zone);
    }
  } finally {
    if (tz === undefined) delete process.env.TZ; else process.env.TZ = tz;
  }
});

test('date sorts compare real dates, not the MM/DD/YYYY strings', () => {
  // As strings "12/31/2025" > "01/05/2026", but Jan 5 2026 is the newer date.
  const rows = [row(2, { title: 'Dec 31 2025', timestamp: '2025-12-31' }), row(3, { title: 'Jan 5 2026', timestamp: '2026-01-05' }),
    row(4, { title: 'none', timestamp: '' }), row(5, { title: 'Sep 2 2026', timestamp: '9/2/2026' }), row(6, { title: 'bad', timestamp: '2026-02-30' })];
  assert.deepEqual(titles(sortRows(rows, 'newest')), ['Sep 2 2026', 'Jan 5 2026', 'Dec 31 2025', 'bad', 'none']);
  assert.deepEqual(titles(sortRows(rows, 'oldest')), ['Dec 31 2025', 'Jan 5 2026', 'Sep 2 2026', 'none', 'bad']);
});

test('Applications: the column is headed Date (shown uppercase) and cells are MM/DD/YYYY, never relative', async () => {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  fakeServer({ rows: [row(2, { title: 'Today', timestamp: iso }), row(3, { title: 'Jan', timestamp: '2026-01-05' }),
    row(4, { title: 'Undated', timestamp: '' }), row(5, { title: 'US', timestamp: '9/2/2026' })] });
  const root = await renderPage(applicationsPage);
  const heads = root.findAll((e) => e.tagName === 'TH').map((th) => th.textContent);
  assert.deepEqual(heads, ['Role', 'Company', 'Fit', 'Status', 'Resume', 'Date', 'Source', '']);
  assert.ok(!heads.includes('Logged'));
  const dates = byClass(root, 'cell-date').map((td) => td.textContent);
  assert.deepEqual(dates, [`${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}`, '09/02/2026', '01/05/2026', '—']);
  for (const d of dates) assert.match(d, /^(\d{2}\/\d{2}\/\d{4}|—)$/, `no relative label like "today" or "3 days ago": ${d}`);
});
