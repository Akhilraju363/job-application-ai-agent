// Sign-in / session calls for the login screen. Same-origin `/api/auth*` only. The session itself is an
// HttpOnly cookie the browser manages; nothing about the user's credentials is ever kept in JavaScript
// beyond the moment of the request (nothing is written to browser storage).
//
// These do NOT go through api.js: a 401 from a failed sign-in is an answer to show, not a reason to
// reload the page.

export const MESSAGES = {
  invalid: 'Invalid username or password.',
  required: 'Enter your username and password.',
  rateLimited: 'Too many sign-in attempts. Please wait a few minutes and try again.',
  network: 'Could not reach the server. Check your connection and try again.',
  error: 'Sign-in failed. Please try again.',
};

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function validateCredentials(username, password) {
  return String(username || '').trim() && String(password || '') ? '' : MESSAGES.required;
}

// -> { authenticated: bool, username?: string, authRequired?: bool }. Rejects if the server is unreachable.
export async function fetchAuthState(fetchImpl = globalThis.fetch) {
  const res = await fetchImpl('/api/auth', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`auth status failed (${res.status})`);
  const body = await res.json();
  return { authenticated: !!body.authenticated, username: body.username, authRequired: body.auth_required !== false };
}

// -> { ok: true, username } | { ok: false, kind: 'required'|'invalid'|'rateLimited'|'network'|'error', message }
export async function signIn(username, password, fetchImpl = globalThis.fetch) {
  const problem = validateCredentials(username, password);
  if (problem) return { ok: false, kind: 'required', message: problem };
  let res;
  try {
    res = await fetchImpl('/api/auth/login', {
      method: 'POST', credentials: 'same-origin', headers: JSON_HEADERS,
      body: JSON.stringify({ username: String(username).trim(), password: String(password) }),
    });
  } catch {
    return { ok: false, kind: 'network', message: MESSAGES.network };
  }
  if (res.ok) {
    let body = {};
    try { body = await res.json(); } catch { /* ignore */ }
    return { ok: true, username: body.username };
  }
  if (res.status === 401) return { ok: false, kind: 'invalid', message: MESSAGES.invalid };
  if (res.status === 429) return { ok: false, kind: 'rateLimited', message: MESSAGES.rateLimited };
  return { ok: false, kind: 'error', message: MESSAGES.error };
}

// Ends the server-side session and clears the cookie. Best effort: the caller reloads either way.
export async function signOut(fetchImpl = globalThis.fetch) {
  try {
    await fetchImpl('/api/auth/logout', { method: 'POST', credentials: 'same-origin', headers: JSON_HEADERS, body: '{}' });
  } catch { /* offline: the reload will show the login screen if the session is gone */ }
}

// Session restoration: a valid session opens the app directly, otherwise show the login screen and
// wait for it to succeed. Injected so it can be tested without a DOM.
export async function ensureSession({ getState = fetchAuthState, showLogin }) {
  const state = await getState();
  if (!state.authenticated) await showLogin();
  return state;
}
