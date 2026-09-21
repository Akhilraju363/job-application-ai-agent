// API client. The frontend only ever talks to this app's own /api -- it never holds a
// provider key, Apify token or Google credential (those live server-side).
import { fileNameFromDisposition } from './lib/format.js';

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// The session is an HttpOnly cookie the browser sends on its own (same-origin); JavaScript never sees it.
// A 401 from any protected call means the session ended -- app.js reloads into the login screen.
export const auth = {
  onUnauthorized: () => {},
};

function headers(json) {
  const h = {};
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

async function fail(res) {
  let msg = `Request failed (${res.status})`;
  let code = 'error';
  try {
    const body = await res.json();
    if (body.error) { msg = body.error.message || msg; code = body.error.code || code; }
  } catch { /* non-JSON error body */ }
  if (res.status === 401) auth.onUnauthorized();
  throw new ApiError(msg, res.status, code);
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: headers(method !== 'GET'),
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    });
  } catch {
    throw new ApiError('Could not reach the dashboard server. Is it still running?', 0, 'network');
  }
  if (!res.ok) await fail(res);
  return res.json();
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  patch: (path, body) => request('PATCH', path, body),
};

export function qs(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null && v !== '') q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : '';
}

// Download through fetch so the bearer token (when configured) is sent.
export async function download(path, fallbackName) {
  let res;
  try { res = await fetch(path, { credentials: 'same-origin', headers: headers(false) }); } catch { throw new ApiError('Download failed: server unreachable', 0, 'network'); }
  if (!res.ok) await fail(res);
  const name = fileNameFromDisposition(res.headers.get('Content-Disposition'), fallbackName);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// Poll a backend task until it finishes. `onUpdate` receives each real task snapshot.
export function pollTask(taskId, onUpdate, { interval = 800 } = {}) {
  let stopped = false;
  const promise = new Promise((resolve, reject) => {
    const tick = async () => {
      if (stopped) return;
      try {
        const t = await api.get(`/api/tasks/${taskId}`);
        onUpdate?.(t);
        if (t.status === 'done') return resolve(t);
        if (t.status === 'error') return reject(new ApiError(t.error?.message || 'Task failed', 500, t.error?.code || 'failed'));
      } catch (e) {
        return reject(e);
      }
      setTimeout(tick, interval);
    };
    tick();
  });
  return { promise, stop: () => { stopped = true; } };
}
