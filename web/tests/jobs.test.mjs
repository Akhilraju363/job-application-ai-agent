// Tests for the inline, editable Status dropdown in the jobs table (web/js/components/jobs.js),
// used by the dashboard's "Latest Job Matches" card and the Find Jobs page.
//
// The dropdown is a safe update, not an optimistic one: it PATCHes /api/tracker/status (the
// existing tracker endpoint -- see tests/test_dashboard_server.py for its own coverage), which
// writes straight to the Google Sheet via tracker_service.py. A job not yet in the tracker is
// saved first via the existing POST /api/jobs/:key/save. No new backend or storage is involved.
//
// Zero dependencies:  node --test web/tests/jobs.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { installFakeDom, forbidBrowserStorage } from './fakeDom.mjs';

installFakeDom();
forbidBrowserStorage();

const { jobsTable, TRACKER_STATUSES } = await import('../js/components/jobs.js');

const respond = (status, body = {}) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function recorder(...responses) {
  const calls = [];
  const queue = [...responses];
  const fn = async (url, init) => {
    calls.push({ url, init: init && { ...init, body: init.body ? JSON.parse(init.body) : undefined } });
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next instanceof Error) throw next;
    return typeof next === 'function' ? next() : next;
  };
  fn.calls = calls;
  return fn;
}

const baseJob = (over = {}) => ({
  key: 'abc123def456', title: 'Java Fullstack (Angular) Developer', company: 'Infosys',
  link: 'https://www.linkedin.com/jobs/view/1', match_pct: 80, location: 'Hyderabad, India',
  source: 'LinkedIn', state: 'Not Applied', tracker_status: null, ...over,
});

const selectIn = (table) => table.find((e) => e.tagName === 'SELECT' && (e.attributes.class || '').includes('status-select'));
const noteIn = (table) => table.find((e) => (e.attributes.class || '').includes('status-note'));
const hintIn = (table) => table.find((e) => (e.attributes.class || '').includes('status-hint'));
const optionValues = (sel) => sel.children.filter((c) => c.tagName === 'OPTION').map((o) => o.value);
const selectedValue = (sel) => sel.children.find((o) => o.selected)?.value;
const lastToast = (tone) => document.body.findAll((e) => (e.attributes.class || '').includes(`toast-${tone}`)).at(-1);

// ---- rendering ----------------------------------------------------------------------------

test('the dropdown offers exactly the canonical five application statuses, in order', () => {
  const sel = selectIn(jobsTable([baseJob()], () => {}));
  assert.deepEqual(optionValues(sel), ['Not Applied', 'Applied', 'Interviewing', 'Offer', 'Rejected']);
  assert.deepEqual(optionValues(sel), TRACKER_STATUSES);
});

test('a tracked job pre-selects its current status; an untracked job defaults to Not Applied', () => {
  assert.equal(selectedValue(selectIn(jobsTable([baseJob({ tracker_status: 'Interviewing' })], () => {}))), 'Interviewing');
  assert.equal(selectedValue(selectIn(jobsTable([baseJob({ tracker_status: null })], () => {}))), 'Not Applied');
});

test('the dropdown is a native <select> with an accessible label naming the job (keyboard-operable by default)', () => {
  const job = baseJob({ title: 'Senior Software Engineer (Java/SpringBoot)' });
  const sel = selectIn(jobsTable([job], () => {}));
  assert.equal(sel.tagName, 'SELECT');
  assert.match(sel.attributes['aria-label'], /Senior Software Engineer \(Java\/SpringBoot\)/);
});

test('a below-cutoff job with no application status yet displays "Below cutoff" itself, not "Not Applied" plus a hint', () => {
  const table = jobsTable([baseJob({ tracker_status: null, state: 'Below cutoff' })], () => {});
  const sel = selectIn(table);
  assert.equal(selectedValue(sel), 'Below cutoff');
  assert.deepEqual(optionValues(sel), ['Below cutoff', ...TRACKER_STATUSES], 'the five real statuses stay selectable alongside it');
  assert.equal(hintIn(table), null, 'no separate "Below cutoff" label duplicating the dropdown value');
});

test('a qualified job with no application status yet displays "Not Applied" (not the below-cutoff placeholder)', () => {
  const table = jobsTable([baseJob({ tracker_status: null, state: 'Qualified' })], () => {});
  const sel = selectIn(table);
  assert.equal(selectedValue(sel), 'Not Applied');
  assert.deepEqual(optionValues(sel), TRACKER_STATUSES, 'no placeholder option for a job that is not below cutoff');
});

test('other pipeline states ("Tailored", "Unscored") still show their small hint next to the Not Applied default', () => {
  for (const state of ['Tailored', 'Unscored']) {
    const table = jobsTable([baseJob({ tracker_status: null, state })], () => {});
    assert.equal(selectedValue(selectIn(table)), 'Not Applied');
    assert.equal(hintIn(table).textContent, state);
  }
});

test('an existing application status always overrides the pipeline state, even if state is stale/inconsistent', () => {
  const table = jobsTable([baseJob({ tracker_status: 'Applied', state: 'Below cutoff' })], () => {});
  const sel = selectIn(table);
  assert.equal(selectedValue(sel), 'Applied');
  assert.deepEqual(optionValues(sel), TRACKER_STATUSES, 'no placeholder option once a real status exists');
  assert.equal(hintIn(table), null);
});

test('scroll:true adds the scrollable-table modifier (Latest Job Matches); the default (Find Jobs) omits it', () => {
  const scrolled = jobsTable([baseJob()], () => {}, { scroll: true });
  const plain = jobsTable([baseJob()], () => {});
  assert.match(scrolled.attributes.class, /table-wrap-scroll/);
  assert.doesNotMatch(plain.attributes.class, /table-wrap-scroll/);
});

// ---- persisting a change --------------------------------------------------------------------

test('changing an untracked job saves it to the tracker first, then sets the chosen status, keyed by link', async () => {
  const fetch = recorder(respond(200, { result: 'added' }), respond(200, { row: 5, status: 'Applied' }));
  globalThis.fetch = fetch;
  const job = baseJob({ key: 'deadbeef0001', link: 'https://x/1', tracker_status: null });
  const sel = selectIn(jobsTable([job], () => {}));
  sel.value = 'Applied';
  await sel.dispatch('change');
  assert.equal(fetch.calls.length, 2);
  assert.equal(fetch.calls[0].url, '/api/jobs/deadbeef0001/save');
  assert.equal(fetch.calls[1].url, '/api/tracker/status');
  assert.deepEqual(fetch.calls[1].init.body, { link: 'https://x/1', status: 'Applied' });
});

test('changing an already-tracked job skips the save call and only PATCHes the status', async () => {
  const fetch = recorder(respond(200, { row: 5, status: 'Interviewing' }));
  globalThis.fetch = fetch;
  const job = baseJob({ key: 'deadbeef0002', link: 'https://x/2', tracker_status: 'Applied' });
  const sel = selectIn(jobsTable([job], () => {}));
  sel.value = 'Interviewing';
  await sel.dispatch('change');
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].url, '/api/tracker/status');
  assert.deepEqual(fetch.calls[0].init.body, { link: 'https://x/2', status: 'Interviewing' });
});

test('a below-cutoff job moved to Applied saves it to the tracker and persists the real status, keyed by link', async () => {
  const fetch = recorder(respond(200, { result: 'added' }), respond(200, { row: 9, status: 'Applied' }));
  globalThis.fetch = fetch;
  const job = baseJob({ key: 'cutoff0001', link: 'https://x/cutoff-1', tracker_status: null, state: 'Below cutoff' });
  const sel = selectIn(jobsTable([job], () => {}));
  assert.equal(selectedValue(sel), 'Below cutoff');
  sel.value = 'Applied';
  await sel.dispatch('change');
  assert.equal(fetch.calls.length, 2);
  assert.equal(fetch.calls[0].url, '/api/jobs/cutoff0001/save');
  assert.deepEqual(fetch.calls[1].init.body, { link: 'https://x/cutoff-1', status: 'Applied' });
  assert.equal(job.tracker_status, 'Applied', 'the job object reflects the persisted status');
});

test('a below-cutoff job moved to Interviewing persists correctly the same way', async () => {
  const fetch = recorder(respond(200, { result: 'added' }), respond(200, { row: 9, status: 'Interviewing' }));
  globalThis.fetch = fetch;
  const job = baseJob({ key: 'cutoff0002', link: 'https://x/cutoff-2', tracker_status: null, state: 'Below cutoff' });
  const sel = selectIn(jobsTable([job], () => {}));
  sel.value = 'Interviewing';
  await sel.dispatch('change');
  assert.equal(fetch.calls.length, 2);
  assert.deepEqual(fetch.calls[1].init.body, { link: 'https://x/cutoff-2', status: 'Interviewing' });
});

test('re-selecting the "Below cutoff" placeholder itself is not sent to the backend', async () => {
  const fetch = recorder(respond(200, {}));
  globalThis.fetch = fetch;
  const job = baseJob({ tracker_status: null, state: 'Below cutoff' });
  const sel = selectIn(jobsTable([job], () => {}));
  sel.value = 'Below cutoff';
  await sel.dispatch('change');
  assert.equal(fetch.calls.length, 0, 'the placeholder is never a real status change');
});

test('never identifies the job by title or company -- only the tracker\'s stable link key', async () => {
  const fetch = recorder(respond(200, { row: 1, status: 'Rejected' }));
  globalThis.fetch = fetch;
  const job = baseJob({ title: 'Java Full Stack', company: 'Infosys', link: 'https://x/dup', tracker_status: 'Not Applied' });
  const sel = selectIn(jobsTable([job], () => {}));
  sel.value = 'Rejected';
  await sel.dispatch('change');
  assert.deepEqual(Object.keys(fetch.calls[0].init.body), ['link', 'status']);
  assert.equal(fetch.calls[0].init.body.link, 'https://x/dup');
});

// ---- safe (non-optimistic) update UX --------------------------------------------------------

test('while the request is in flight the dropdown is disabled and shows a "Saving…" note', async () => {
  let release;
  globalThis.fetch = async () => new Promise((res) => { release = () => res(respond(200, { row: 1, status: 'Applied' })); });
  const table = jobsTable([baseJob({ tracker_status: 'Not Applied' })], () => {});
  const sel = selectIn(table);
  const note = noteIn(table);
  assert.equal(note.hidden, true, 'hidden before any change');
  sel.value = 'Applied';
  const pending = sel.dispatch('change');
  await Promise.resolve();
  assert.equal(sel.disabled, true);
  assert.equal(note.hidden, false);
  release();
  await pending;
  assert.equal(sel.disabled, false);
  assert.equal(note.hidden, true);
});

test('a successful update toasts, is not intrusive (no alert()), and triggers onChange so the row refreshes', async () => {
  globalThis.fetch = async () => respond(200, { row: 1, status: 'Applied' });
  let changed = 0;
  const sel = selectIn(jobsTable([baseJob({ tracker_status: 'Not Applied' })], () => { changed += 1; }));
  sel.value = 'Applied';
  await sel.dispatch('change');
  assert.equal(changed, 1);
  assert.match(lastToast('success').textContent, /Applied/);
});

test('a failed update restores the previous value, re-enables the dropdown, shows an error toast, and does not call onChange', async () => {
  globalThis.fetch = async () => respond(502, { error: { code: 'tracker_unavailable', message: 'Tracker unavailable: offline' } });
  let changed = 0;
  const table = jobsTable([baseJob({ tracker_status: 'Applied' })], () => { changed += 1; });
  const sel = selectIn(table);
  sel.value = 'Rejected';
  await sel.dispatch('change');
  assert.equal(sel.value, 'Applied', 'reverted to the value in effect before the change');
  assert.equal(sel.disabled, false);
  assert.equal(changed, 0, 'a value that was never persisted must not trigger a refresh');
  assert.match(lastToast('error').textContent, /Tracker unavailable/);
  assert.doesNotMatch(lastToast('error').textContent, /Traceback|credential|secret/i);
});

test('a failed update on a below-cutoff job reverts to showing "Below cutoff" again, not "Not Applied"', async () => {
  globalThis.fetch = async () => respond(502, { error: { code: 'tracker_unavailable', message: 'Tracker unavailable: offline' } });
  let changed = 0;
  const job = baseJob({ tracker_status: null, state: 'Below cutoff' });
  const sel = selectIn(jobsTable([job], () => { changed += 1; }));
  sel.value = 'Rejected';
  await sel.dispatch('change');
  assert.equal(sel.value, 'Below cutoff', 'reverted to the placeholder that was showing before the change');
  assert.equal(sel.disabled, false);
  assert.equal(changed, 0);
  assert.equal(job.tracker_status, null, 'the job object was never mutated by the failed attempt');
});

test('an invalid/unknown status from the backend (400) is surfaced the same safe way as any other failure', async () => {
  globalThis.fetch = async () => respond(400, { error: { code: 'bad_status', message: 'status must be one of: Not Applied, Applied, Interviewing, Offer, Rejected' } });
  const sel = selectIn(jobsTable([baseJob({ tracker_status: 'Not Applied' })], () => {}));
  sel.value = 'Offer';
  await sel.dispatch('change');
  assert.equal(sel.value, 'Not Applied');
  assert.equal(sel.disabled, false);
});
