// Unit tests for the frontend's pure logic. Zero dependencies:  node --test web/tests
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  relTime, relDay, matchTone, statusTone, trendInfo, periodLabel, initials, safeHref, fileNameFromDisposition, todayISO,
} from '../js/lib/format.js';
import { parseResume, groupBullets } from '../js/lib/resumeMarkdown.js';
import { niceMax, gridTicks, donutArcs, percentOf } from '../js/lib/chartGeometry.js';
import { validateTailorInput } from '../js/lib/tailorValidation.js';

const NOW = Date.parse('2026-09-20T12:00:00Z');

test('relTime buckets', () => {
  assert.equal(relTime('2026-09-20T11:59:30Z', NOW), 'just now');
  assert.equal(relTime('2026-09-20T11:15:00Z', NOW), '45 min ago');
  assert.equal(relTime('2026-09-20T10:00:00Z', NOW), '2 hours ago');
  assert.equal(relTime('2026-09-20T11:00:00Z', NOW), '1 hour ago');
  assert.equal(relTime('2026-09-18T12:00:00Z', NOW), '2 days ago');
  assert.equal(relTime('', NOW), '');
  assert.equal(relTime('garbage', NOW), 'garbage');
});

test('relDay treats date-only values as days, not hours', () => {
  assert.equal(relDay('2026-09-20', NOW), 'today');
  assert.equal(relDay('2026-09-19', NOW), 'yesterday');
  assert.equal(relDay('2026-09-14T00:00:00+00:00', NOW), '6 days ago');
  assert.equal(relDay('2026-09-20T10:00:00Z', NOW), '2 hours ago');
  assert.match(relDay('2026-06-01', NOW), /Jun 1, 2026/);
});

test('tones', () => {
  assert.equal(matchTone(92), 'green');
  assert.equal(matchTone(70), 'orange');
  assert.equal(matchTone(40), 'red');
  assert.equal(matchTone(null), 'neutral');
  assert.equal(statusTone('Applied'), 'blue');
  assert.equal(statusTone('Interviewing'), 'orange');
  assert.equal(statusTone('whatever'), 'neutral');
});

test('trendInfo reflects the API metric, including new / no-change / unavailable', () => {
  assert.deepEqual(trendInfo({ trend: 'up', change_pct: 24 }), { dir: 'up', text: '+24%', tone: 'green' });
  assert.deepEqual(trendInfo({ trend: 'down', change_pct: -10 }), { dir: 'down', text: '-10%', tone: 'red' });
  assert.deepEqual(trendInfo({ trend: 'new', change_pct: null }), { dir: 'up', text: 'new', tone: 'green' });
  assert.deepEqual(trendInfo({ trend: 'flat', change_pct: null }), { dir: 'flat', text: '—', tone: 'neutral' });
  assert.deepEqual(trendInfo({ trend: 'flat', change_pct: 0 }), { dir: 'flat', text: '0%', tone: 'neutral' });
  assert.equal(trendInfo(null), null);
  assert.equal(periodLabel({ days: 7 }), 'last 7 days');
  assert.equal(periodLabel({ days: 1 }), 'last 1 day');
});

test('safeHref only allows http(s)', () => {
  assert.equal(safeHref('https://example.com/a?b=1'), 'https://example.com/a?b=1');
  assert.equal(safeHref('http://example.com'), 'http://example.com/');
  for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', 'manual:abc', '', null, undefined, 'not a url']) {
    assert.equal(safeHref(bad), null, String(bad));
  }
});

test('helpers', () => {
  assert.equal(initials('Akhil Dalali'), 'AD');
  assert.equal(initials('  cher '), 'C');
  assert.equal(initials(''), '?');
  assert.equal(todayISO(new Date(2026, 8, 5)), '2026-09-05');
  assert.equal(fileNameFromDisposition('attachment; filename="a b.pdf"; filename*=UTF-8\'\'a%20b.pdf', 'x'), 'a b.pdf');
  assert.equal(fileNameFromDisposition('attachment; filename="plain.md"', 'x'), 'plain.md');
  assert.equal(fileNameFromDisposition(null, 'fallback.pdf'), 'fallback.pdf');
});

const SAMPLE = `# Jane Roe
Software Engineer | Java

jane@example.com | Bengaluru

## Summary
Four years of experience.

## Experience

### Software Engineer — Acme
Jan 2022 - Jun 2024 | Chennai
- Built APIs.
- Wrote tests.
### Intern — Beta
- One bullet.
`;

test('resume markdown parses into the same block types the exports use', () => {
  const blocks = parseResume(SAMPLE);
  assert.deepEqual(blocks.map((b) => b.type), ['name', 'tagline', 'text', 'section', 'text', 'section', 'role', 'date', 'bullet', 'bullet', 'role', 'bullet']);
  assert.equal(blocks[0].text, 'Jane Roe');
  assert.equal(blocks[1].text, 'Software Engineer | Java');
  assert.equal(blocks[7].text, 'Jan 2022 - Jun 2024 | Chennai');
});

test('bullets are grouped per run and markup is left as plain text', () => {
  const grouped = groupBullets(parseResume(SAMPLE));
  assert.deepEqual(grouped.filter((b) => b.type === 'bullets').map((b) => b.items.length), [2, 1]);
  const hostile = parseResume('# <img src=x onerror=alert(1)>\n- <script>alert(1)</script>');
  assert.equal(hostile[0].text, '<img src=x onerror=alert(1)>'); // returned as data; the UI uses textContent
  assert.deepEqual(parseResume(''), []);
  assert.deepEqual(parseResume(null), []);
});

test('chart geometry', () => {
  assert.equal(niceMax(0), 4);
  assert.equal(niceMax(3), 4);
  assert.equal(niceMax(9), 10);
  assert.equal(niceMax(23), 25);
  assert.equal(niceMax(127), 200);
  assert.deepEqual(gridTicks(20), [0, 5, 10, 15, 20]);
  assert.equal(percentOf(12, 30), 40);
  assert.equal(percentOf(1, 0), 0);
});

test('donut arcs tile the full circle without overlap and skip nothing', () => {
  const arcs = donutArcs([{ key: 'a', value: 12 }, { key: 'b', value: 5 }, { key: 'c', value: 0 }, { key: 'd', value: 13 }], 70);
  const c = 2 * Math.PI * 70;
  assert.ok(Math.abs(arcs.reduce((s, a) => s + a.dash, 0) - c) < 1e-9);
  assert.equal(arcs[0].offset, 0);
  assert.ok(Math.abs(arcs[1].offset + arcs[0].dash) < 1e-9);
  assert.equal(arcs[2].dash, 0);
  assert.equal(donutArcs([{ key: 'a', value: 0 }], 70)[0].dash, 0); // empty data never divides by zero
});

test('tailor form: job title required in any case, company optional', () => {
  const JD = 'Looking for Java Developers to join in Bangalore. 3+ years of Java, Spring Boot and REST APIs required.';
  const ok = (o) => validateTailorInput({ title: 'JAVA DEVELOPER', company: '', url: '', description: JD, ...o });
  for (const title of ['java developer', 'Java Developer', 'JAVA DEVELOPER', 'JaVa DeVeLoPeR']) assert.equal(ok({ title }), '', title);
  for (const company of ['', '   ', undefined, 'Acme']) assert.equal(ok({ company }), '', String(company));
  for (const title of ['', '   ', undefined]) {
    assert.equal(ok({ title }), 'Job title is required.');
    assert.equal(ok({ title, company: 'Acme' }), 'Job title is required.');
  }
  assert.doesNotMatch(ok({ title: '' }), /company/i);
  assert.match(ok({ description: 'short' }), /at least 80/);
  assert.match(ok({ url: 'javascript:alert(1)' }), /http/);
});
