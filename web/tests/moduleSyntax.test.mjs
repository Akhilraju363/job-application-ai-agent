import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';

const WEB_JS = new URL('../js/', import.meta.url);

function jsFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? jsFiles(new URL(`${entry.name}/`, dir)) : entry.name.endsWith('.js') ? [new URL(entry.name, dir)] : []);
}

test('all frontend modules parse as ES modules', () => {
  for (const file of jsFiles(WEB_JS)) {
    assert.doesNotThrow(() => execFileSync(process.execPath, ['--input-type=module', '--check'], {
      input: readFileSync(file, 'utf8'),
      stdio: ['pipe', 'pipe', 'pipe'],
    }), file.pathname);
  }
});
