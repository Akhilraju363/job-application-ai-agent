// Pure helpers for the Logs page (no DOM) -- unit-tested in web/tests.

export const LEVEL_FILTERS = ['All', 'INFO', 'WARNING', 'ERROR'];
export const ID_FIELDS = [['request_id', 'Request ID'], ['task_id', 'Task ID'], ['resume_id', 'Resume ID'], ['job_id', 'Job ID']];
const KNOWN = new Set(['timestamp', 'level', 'component', 'message', 'exception', ...ID_FIELDS.map(([k]) => k)]);

// Query string for GET /api/logs. Empty values are dropped; the server validates the rest.
export function buildLogQuery(f = {}) {
  const q = new URLSearchParams();
  const level = f.level && f.level !== 'All' ? f.level : '';
  const entries = [['level', level], ['component', f.component], ['date', f.date], ['request_id', f.request_id],
    ['task_id', f.task_id], ['resume_id', f.resume_id], ['job_id', f.job_id], ['limit', f.limit], ['offset', f.offset]];
  for (const [k, v] of entries) {
    const s = v == null ? '' : String(v).trim();
    if (s && !(k === 'offset' && s === '0')) q.set(k, s);
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

export function levelTone(level) {
  switch (String(level || '').toUpperCase()) {
    case 'ERROR': case 'CRITICAL': return 'red';
    case 'WARNING': return 'orange';
    case 'DEBUG': return 'neutral';
    default: return 'blue';
  }
}

// "21:20:01" for today's entries in the viewer's timezone; date + time otherwise.
export function formatLogTime(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return String(iso || '');
  const d = new Date(t);
  const pad = (n) => String(n).padStart(2, '0');
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const n = new Date(now);
  const sameDay = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
  return sameDay ? time : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`;
}

// The id chips shown in the table row: [[label, value], ...] for whichever ids the entry has.
export function logIds(entry) {
  return ID_FIELDS.filter(([k]) => entry && entry[k]).map(([k, label]) => [label, String(entry[k])]);
}

// Everything else on the entry (method, path, status, duration_ms, ...) for the detail drawer.
export function extraFields(entry) {
  return Object.entries(entry || {}).filter(([k, v]) => !KNOWN.has(k) && v != null && v !== '')
    .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)]);
}

// "1-50", "51-100" ... for the pager; server pages are offset/limit with a has_more flag.
export function pageLabel({ offset = 0, count = 0 }) {
  return count ? `${offset + 1}–${offset + count}` : '0';
}
