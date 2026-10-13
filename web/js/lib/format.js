// Pure formatting helpers (no DOM) -- unit-tested in web/tests.

export function relTime(iso, now = Date.now()) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return String(iso);
  const s = Math.round((now - t) / 1000);
  if (s < 0) return 'just now';
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} day${d === 1 ? '' : 's'} ago`;
  return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// Date-only values (no time component) read better as a date than "13 hours ago".
export function relDay(value, now = Date.now()) {
  if (!value) return '';
  const s = String(value);
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(s) || /T00:00:00(\+00:00|Z)?$/.test(s);
  const t = Date.parse(dateOnly ? s.slice(0, 10) + 'T00:00:00Z' : s);
  if (Number.isNaN(t)) return s;
  if (!dateOnly) return relTime(s, now);
  const days = Math.floor((now - t) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

// A job's posting date as MM/DD/YYYY, or '' when missing/invalid (callers render '—').
// ISO strings keep the calendar date they were written with -- '2026-10-12T23:30:00-05:00' is
// 10/12/2026, never shifted by the viewer's timezone. Epoch numbers (seconds or ms) use UTC.
// Relative text ("2 days ago") is rejected: resolving it would substitute the current date.
export function formatPostedDate(value) {
  if (value == null || value === '' || typeof value === 'boolean') return '';
  const s = String(value).trim();
  let y, m, d;
  const iso = /^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])/.exec(s);
  if (iso) {
    if (s.length > 10 && Number.isNaN(Date.parse(s.replace(' ', 'T')))) return '';
    [y, m, d] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
    const check = new Date(Date.UTC(y, m - 1, d));
    if (check.getUTCFullYear() !== y || check.getUTCMonth() !== m - 1 || check.getUTCDate() !== d) return '';
  } else if (/^\d{9,13}$/.test(s)) {
    const n = Number(s);
    const t = new Date(n < 1e11 ? n * 1000 : n);
    [y, m, d] = [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()];
  } else {
    return '';
  }
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(m)}/${pad(d)}/${y}`;
}

export function matchTone(pct) {
  if (pct == null) return 'neutral';
  if (pct >= 80) return 'green';
  if (pct >= 60) return 'orange';
  return 'red';
}

export function statusTone(status) {
  return ({
    'Applied': 'blue', 'Interviewing': 'orange', 'Offer': 'green', 'Rejected': 'red',
    'Not Applied': 'neutral', 'Tailored': 'purple', 'Qualified': 'green',
    'Below cutoff': 'neutral', 'Unscored': 'neutral',
  })[status] || 'neutral';
}

export function trendInfo(metric) {
  if (!metric) return null;
  const { trend, change_pct: c } = metric;
  if (trend === 'new') return { dir: 'up', text: 'new', tone: 'green' };
  if (c == null) return { dir: 'flat', text: '—', tone: 'neutral' };
  if (trend === 'up') return { dir: 'up', text: `+${c}%`, tone: 'green' };
  if (trend === 'down') return { dir: 'down', text: `${c}%`, tone: 'red' };
  return { dir: 'flat', text: '0%', tone: 'neutral' };
}

export function periodLabel(period) {
  if (!period) return '';
  return `last ${period.days} day${period.days === 1 ? '' : 's'}`;
}

export function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  return ((parts[0] || '')[0] || '?').toUpperCase() + ((parts[1] || '')[0] || '').toUpperCase();
}

// Only http(s) links may become hrefs -- anything else (javascript:, data:) is dropped.
export function safeHref(url) {
  try {
    const u = new URL(String(url));
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
  } catch { return null; }
}

export function fileNameFromDisposition(header, fallback) {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) { try { return decodeURIComponent(star[1]); } catch { /* fall through */ } }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : fallback;
}

export function todayISO(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}
