// Frontend authentication tests (login screen, sign-in/out calls, session restoration, 401 handling).
// Zero dependencies:  node --test web/tests/auth.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { installFakeDom, forbidBrowserStorage } from './fakeDom.mjs';

installFakeDom();
forbidBrowserStorage();

const { loginView } = await import('../js/components/loginForm.js');
const { signIn, signOut, fetchAuthState, ensureSession, MESSAGES } = await import('../js/lib/authClient.js');
const { api, auth } = await import('../js/api.js');

const respond = (status, body = {}) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function recorder(...responses) {
  const calls = [];
  const queue = [...responses];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next instanceof Error) throw next;
    return typeof next === 'function' ? next() : next;
  };
  fn.calls = calls;
  return fn;
}

const inputs = (view) => view.findAll((e) => e.tagName === 'INPUT');
const usernameInput = (view) => inputs(view).find((i) => i.attributes.name === 'username');
const passwordInput = (view) => inputs(view).find((i) => i.attributes.name === 'password');
const button = (view) => view.find((e) => e.tagName === 'BUTTON');
const errorEl = (view) => view.find((e) => e.attributes.role === 'alert');

function render(signInFn, onSignedIn = () => {}) {
  const view = loginView({ onSignedIn, signInFn });
  return view;
}

async function submit(view, user, pass) {
  usernameInput(view).value = user;
  passwordInput(view).value = pass;
  return view.dispatch('submit');
}

// ---- rendering ---------------------------------------------------------------------------------

test('the login screen renders a title, a prompt, both fields and a Sign In button', () => {
  const view = render(async () => ({ ok: true }));
  assert.equal(view.tagName, 'FORM');
  assert.match(view.textContent, /Job Application AI Agent/);
  assert.match(view.textContent, /Sign in to your dashboard/);
  assert.ok(usernameInput(view), 'username input');
  assert.ok(passwordInput(view), 'password input');
  assert.equal(button(view).attributes.type, 'submit');
  assert.equal(button(view).textContent, 'Sign In');
});

test('the password field is masked and both fields have accessible labels and autofill hints', () => {
  const view = render(async () => ({ ok: true }));
  assert.equal(passwordInput(view).attributes.type, 'password');
  assert.equal(passwordInput(view).attributes.autocomplete, 'current-password');
  assert.equal(usernameInput(view).attributes.type, 'text');
  assert.equal(usernameInput(view).attributes.autocomplete, 'username');
  for (const [input, label] of [[usernameInput(view), 'Username'], [passwordInput(view), 'Password']]) {
    const lab = view.find((e) => e.tagName === 'LABEL' && e.attributes.for === input.attributes.id);
    assert.ok(lab, `label for ${label}`);
    assert.match(lab.textContent, new RegExp(label));
  }
  assert.ok(errorEl(view).hidden, 'no error before the first attempt');
});

test('the login UI never mentions a token or environment variable names', () => {
  const view = render(async () => ({ ok: true }));
  const dump = JSON.stringify(view, (k, v) => (k === 'listeners' ? undefined : v));
  assert.doesNotMatch(dump, /token/i);
  assert.doesNotMatch(dump, /DASHBOARD_/);
  assert.equal(inputs(view).length, 2, 'exactly a username and a password field');
});

// ---- signing in --------------------------------------------------------------------------------

test('submitting posts JSON to /api/auth/login on the same origin, with no bearer header and no storage', async () => {
  const fetch = recorder(respond(200, { authenticated: true, username: 'akhil' }));
  let signedIn = 0;
  const view = render((u, p) => signIn(u, p, fetch), () => { signedIn += 1; });
  const event = await submit(view, '  akhil ', 's3cret-password');
  assert.equal(event.defaultPrevented, true);
  assert.equal(fetch.calls.length, 1);
  const { url, init } = fetch.calls[0];
  assert.equal(url, '/api/auth/login');
  assert.equal(init.method, 'POST');
  assert.equal(init.credentials, 'same-origin');
  assert.equal(init.headers['Content-Type'], 'application/json');
  assert.equal(init.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(init.body), { username: 'akhil', password: 's3cret-password' });
  assert.equal(signedIn, 1);
  assert.equal(passwordInput(view).value, '', 'the password is cleared from the field after a successful sign-in');
});

test('while signing in the button is disabled and shows progress; a second submit is ignored', async () => {
  let release;
  const fetch = recorder(() => new Promise((res) => { release = () => res(respond(200, { authenticated: true })); }));
  const view = render((u, p) => signIn(u, p, fetch));
  usernameInput(view).value = 'akhil';
  passwordInput(view).value = 'pw-pw-pw-pw';
  const first = view.dispatch('submit');
  await Promise.resolve();
  assert.equal(button(view).disabled, true);
  assert.match(button(view).textContent, /Signing in/);
  await view.dispatch('submit');
  assert.equal(fetch.calls.length, 1, 'no duplicate request');
  release();
  await first;
});

test('invalid credentials show one generic message, re-enable the form and clear the password', async () => {
  const fetch = recorder(respond(401, { error: { code: 'invalid_credentials', message: 'Invalid username or password.' } }));
  const view = render((u, p) => signIn(u, p, fetch));
  await submit(view, 'akhil', 'wrong-password');
  assert.equal(errorEl(view).hidden, false);
  assert.equal(errorEl(view).textContent, 'Invalid username or password.');
  assert.equal(button(view).disabled, false);
  assert.equal(button(view).textContent, 'Sign In');
  assert.equal(passwordInput(view).value, '');
  assert.equal(usernameInput(view).value, 'akhil', 'the username is kept for the retry');
  assert.equal(passwordInput(view).focused, true);
});

test('a wrong username and a wrong password produce the identical message', async () => {
  const messages = [];
  for (const who of ['nobody', 'akhil']) {
    const view = render((u, p) => signIn(u, p, recorder(respond(401))));
    await submit(view, who, 'x-wrong-password');
    messages.push(errorEl(view).textContent);
  }
  assert.equal(messages[0], messages[1]);
});

test('network failures and rate limiting get their own safe messages', async () => {
  const offline = render((u, p) => signIn(u, p, recorder(new TypeError('Failed to fetch'))));
  await submit(offline, 'akhil', 'pw-pw-pw-pw');
  assert.equal(errorEl(offline).textContent, MESSAGES.network);
  assert.equal(button(offline).disabled, false);

  const limited = render((u, p) => signIn(u, p, recorder(respond(429))));
  await submit(limited, 'akhil', 'pw-pw-pw-pw');
  assert.equal(errorEl(limited).textContent, MESSAGES.rateLimited);

  const broken = render((u, p) => signIn(u, p, recorder(respond(500))));
  await submit(broken, 'akhil', 'pw-pw-pw-pw');
  assert.equal(errorEl(broken).textContent, MESSAGES.error);
  assert.doesNotMatch(MESSAGES.error + MESSAGES.network + MESSAGES.rateLimited, /token|DASHBOARD|stack|exception/i);
});

test('empty fields are rejected locally without contacting the server', async () => {
  const fetch = recorder(respond(200));
  const view = render((u, p) => signIn(u, p, fetch));
  await submit(view, '', '');
  assert.equal(fetch.calls.length, 0);
  assert.equal(errorEl(view).textContent, MESSAGES.required);
});

test('pressing Enter is a form submit: the handler is on the form, not only on the button', () => {
  const view = render(async () => ({ ok: true }));
  assert.ok((view.listeners.submit || []).length >= 1);
  assert.equal(button(view).attributes.type, 'submit');
});

// ---- session restoration and logout -------------------------------------------------------------

test('a valid session opens the app without showing the login screen', async () => {
  let shown = 0;
  const fetch = recorder(respond(200, { authenticated: true, username: 'akhil' }));
  const state = await ensureSession({ getState: () => fetchAuthState(fetch), showLogin: async () => { shown += 1; } });
  assert.equal(shown, 0);
  assert.deepEqual([state.authenticated, state.username], [true, 'akhil']);
  assert.equal(fetch.calls[0].url, '/api/auth');
});

test('no session shows the login screen and waits for it', async () => {
  let shown = 0;
  const fetch = recorder(respond(200, { authenticated: false }));
  await ensureSession({ getState: () => fetchAuthState(fetch), showLogin: async () => { shown += 1; } });
  assert.equal(shown, 1);
});

test('local mode (no sign-in configured) is reported as authenticated without a username', async () => {
  const state = await fetchAuthState(recorder(respond(200, { authenticated: true, auth_required: false })));
  assert.deepEqual([state.authenticated, state.authRequired], [true, false]);
});

test('an unreachable server rejects the status check (so the app can show its error state)', async () => {
  await assert.rejects(fetchAuthState(recorder(new TypeError('offline'))));
  await assert.rejects(fetchAuthState(recorder(respond(500))));
});

test('logout posts to /api/auth/logout and tolerates being offline', async () => {
  const fetch = recorder(respond(200, { authenticated: false }));
  await signOut(fetch);
  assert.equal(fetch.calls[0].url, '/api/auth/logout');
  assert.equal(fetch.calls[0].init.method, 'POST');
  assert.equal(fetch.calls[0].init.credentials, 'same-origin');
  await signOut(recorder(new TypeError('offline'))); // must not throw
});

test('after logout the next status check returns to the login screen', async () => {
  let authenticated = true;
  const fetch = async (url) => {
    if (url === '/api/auth/logout') authenticated = false;
    return respond(200, { authenticated });
  };
  let shown = 0;
  await ensureSession({ getState: () => fetchAuthState(fetch), showLogin: async () => { shown += 1; } });
  assert.equal(shown, 0);
  await signOut(fetch);
  await ensureSession({ getState: () => fetchAuthState(fetch), showLogin: async () => { shown += 1; } });
  assert.equal(shown, 1);
});

// ---- protected pages (Logs etc.) ---------------------------------------------------------------

test('a 401 from a protected API (e.g. Logs) triggers the return to the login screen', async () => {
  const realFetch = globalThis.fetch;
  const fetch = recorder(respond(401, { error: { code: 'unauthorized', message: 'Authentication required' } }));
  globalThis.fetch = fetch;
  let bounced = 0;
  auth.onUnauthorized = () => { bounced += 1; };
  try {
    await assert.rejects(api.get('/api/logs?limit=50'), (e) => e.status === 401);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(bounced, 1);
  assert.equal(fetch.calls[0].url, '/api/logs?limit=50');
  assert.equal(fetch.calls[0].init.credentials, 'same-origin');
  assert.equal(fetch.calls[0].init.headers.Authorization, undefined, 'no bearer token is ever attached');
});

test('a failed sign-in does not trigger the global "session ended" reload loop', async () => {
  let bounced = 0;
  auth.onUnauthorized = () => { bounced += 1; };
  await signIn('akhil', 'nope-nope-nope', recorder(respond(401)));
  assert.equal(bounced, 0);
});

// ---- same-origin / no secrets in source --------------------------------------------------------

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

test('no source file hard-codes localhost, a port or the Modal URL, and none stores credentials', () => {
  const root = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
  const files = walk(join(root, 'js')).concat(join(root, 'index.html'));
  assert.ok(files.length > 10);
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    assert.doesNotMatch(text, /localhost|127\.0\.0\.1|0\.0\.0\.0|:8765|modal\.run|akhildalali07/, f);
    assert.doesNotMatch(text, /DASHBOARD_TOKEN|DASHBOARD_PASSWORD|sessionStorage|Authorization|Bearer/, f);
  }
});
