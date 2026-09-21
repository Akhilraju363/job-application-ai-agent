// Unit tests for the Logs page's pure helpers. Zero dependencies:  node --test web/tests/logs.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildLogQuery, levelTone, formatLogTime, logIds, extraFields, pageLabel, LEVEL_FILTERS } from '../js/lib/logs.js';

test('buildLogQuery drops empty values, the All level and offset 0', () => {
  assert.equal(buildLogQuery({}), '');
  assert.equal(buildLogQuery({ level: 'All', offset: 0, limit: 50 }), '?limit=50');
  assert.equal(buildLogQuery({ level: 'ERROR', component: 'drive', limit: 50, offset: 100 }), '?level=ERROR&component=drive&limit=50&offset=100');
  assert.equal(buildLogQuery({ task_id: '  abc123 ', resume_id: '' }), '?task_id=abc123');
});

test('buildLogQuery percent-encodes user input (no injection into the query string)', () => {
  const q = buildLogQuery({ request_id: 'a&level=DEBUG', date: '2026-09-21' });
  assert.equal(new URLSearchParams(q).get('request_id'), 'a&level=DEBUG');
  assert.equal(new URLSearchParams(q).get('level'), null);
});

test('levelTone maps severities', () => {
  assert.equal(levelTone('ERROR'), 'red');
  assert.equal(levelTone('critical'), 'red');
  assert.equal(levelTone('WARNING'), 'orange');
  assert.equal(levelTone('INFO'), 'blue');
  assert.equal(levelTone('DEBUG'), 'neutral');
  assert.equal(levelTone(undefined), 'blue');
});

test('formatLogTime shows time for today and date+time otherwise; tolerates garbage', () => {
  const now = new Date(2026, 8, 21, 12, 0, 0).getTime();
  const today = new Date(2026, 8, 21, 9, 5, 7).toISOString();
  const older = new Date(2026, 8, 19, 23, 1, 2).toISOString();
  assert.equal(formatLogTime(today, now), '09:05:07');
  assert.equal(formatLogTime(older, now), '2026-09-19 23:01:02');
  assert.equal(formatLogTime('not a date', now), 'not a date');
  assert.equal(formatLogTime('', now), '');
});

test('logIds returns only the ids present, in a stable order', () => {
  assert.deepEqual(logIds({ task_id: 't1', request_id: 'r1' }), [['Request ID', 'r1'], ['Task ID', 't1']]);
  assert.deepEqual(logIds({ message: 'x' }), []);
  assert.deepEqual(logIds(null), []);
});

test('extraFields excludes the columns already shown and empty values', () => {
  const fields = extraFields({ timestamp: 't', level: 'INFO', component: 'dashboard', message: 'm', request_id: 'r',
    status: 200, path: '/api/x', empty: '', gone: null, nested: { a: 1 } });
  assert.deepEqual(fields, [['status', '200'], ['path', '/api/x'], ['nested', '{"a":1}']]);
});

test('pageLabel', () => {
  assert.equal(pageLabel({ offset: 0, count: 50 }), '1–50');
  assert.equal(pageLabel({ offset: 50, count: 7 }), '51–57');
  assert.equal(pageLabel({ offset: 0, count: 0 }), '0');
});

test('level filters offered by the UI', () => {
  assert.deepEqual(LEVEL_FILTERS, ['All', 'INFO', 'WARNING', 'ERROR']);
});

test('the Logs page only talks to the same-origin /api/logs and builds no absolute URLs', () => {
  const src = readFileSync(new URL('../js/pages/logs.js', import.meta.url), 'utf8');
  assert.match(src, /api\.get\('\/api\/logs'/);
  assert.doesNotMatch(src, /localhost|127\.0\.0\.1|https?:\/\/|fetch\(/);
});
