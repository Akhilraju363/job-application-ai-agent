// Sidebar navigation after Job Tracker and Logs were removed (2026-10-12). Source-level checks on
// web/js/app.js plus a scan of every module for links to the removed routes.
//
// Zero dependencies:  node --test web/tests/navigation.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';

const WEB_JS = new URL('../js/', import.meta.url);
const read = (rel) => readFileSync(new URL(rel, WEB_JS), 'utf8');
const app = read('app.js');
const navBlock = app.slice(app.indexOf('const NAV'), app.indexOf('];', app.indexOf('const NAV')));
const entries = [...navBlock.matchAll(/\{ path: '([^']+)', label: '([^']+)', icon: '\w+', page: (\w+), group: '(\w+)' \}/g)]
  .map(([, path, label, page, group]) => ({ path, label, page, group }));

function jsFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) => (d.isDirectory() ? jsFiles(new URL(`${d.name}/`, dir)) : d.name.endsWith('.js') ? [new URL(d.name, dir)] : []));
}

test('the sidebar has exactly the seven remaining pages, in order and group', () => {
  assert.deepEqual(entries.map((e) => [e.path, e.label, e.group]), [
    ['/', 'Dashboard', 'Workspace'],
    ['/find-jobs', 'Find Jobs', 'Workspace'],
    ['/tailor-resume', 'Tailor Resume', 'Workspace'],
    ['/master-resume', 'Master Resume', 'Workspace'],
    ['/applications', 'Applications', 'Tracking'],
    ['/job-alerts', 'Job Alerts', 'Tracking'],
    ['/settings', 'Settings', 'System'],
  ]);
});

test('every nav page is imported from a module that exists and exports it', () => {
  for (const { page } of entries) {
    const imp = new RegExp(`import \\{[^}]*\\b${page}\\b[^}]*\\} from '\\./(pages/\\w+\\.js)'`).exec(app);
    assert.ok(imp, `${page} is imported`);
    assert.ok(existsSync(new URL(imp[1], WEB_JS)), imp[1]);
    assert.match(read(imp[1]), new RegExp(`export function ${page}\\b`), `${imp[1]} exports ${page}`);
  }
  assert.doesNotMatch(app, /trackerBoardPage|logsPage|pages\/logs\.js/);
});

test('old /job-tracker and /logs URLs redirect instead of showing Page not found', () => {
  assert.match(app, /export const MOVED = \{ '\/job-tracker': '\/applications', '\/logs': '\/settings' \};/);
  assert.match(app, /if \(MOVED\[path\]\) \{ navigate\(MOVED\[path\], \{ replace: true \}\); return; \}/);
  for (const target of ['/applications', '/settings']) assert.ok(entries.some((e) => e.path === target), target);
});

test('no module links to a removed page or still ships the removed board/logs UI', () => {
  for (const file of jsFiles(WEB_JS)) {
    const src = readFileSync(file, 'utf8');
    const name = file.pathname.split('/web/js/')[1];
    if (name === 'app.js') continue;   // MOVED names the old routes on purpose
    assert.doesNotMatch(src, /['"`]\/(job-tracker|logs)\b/, `${name} links to a removed route`);
    assert.doesNotMatch(src, /\bJob Tracker\b|board-card|board-col/, `${name} mentions the removed Job Tracker`);
  }
  assert.ok(!existsSync(new URL('pages/logs.js', WEB_JS)) && !existsSync(new URL('lib/logs.js', WEB_JS)));
});
