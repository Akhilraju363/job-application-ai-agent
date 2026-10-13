// Pure helpers for the Applications page (no DOM) -- unit-tested in web/tests.
// Sorting happens on the rows already loaded from /api/tracker: it never refetches the Sheet
// and never touches a row's status.

export const SORT_OPTIONS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'score', label: 'Best match' },
  { value: 'company', label: 'Company A–Z' },
  { value: 'role', label: 'Role A–Z' },
];
export const DEFAULT_SORT = 'newest';
const VALID = new Set(SORT_OPTIONS.map((o) => o.value));

// The value is only the sort option name (never job data).
export const SORT_KEYS = { applications: 'jobagent.sort.applications' };

// A tracker date (Sheet column G, `timestamp`) -> { t: epoch ms, utc } or null when missing/invalid.
// ISO calendar dates (optionally at UTC midnight) are read as UTC dates, so formatting them with the
// UTC getters never shifts the day in any timezone; an impossible one (2026-02-30) is invalid, not rolled
// over. Anything else (e.g. "9/2/2026", a full timestamp) goes through Date.parse as a local date.
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})(?:T00:00:00(?:\.0+)?(?:Z|\+00:00))?$/;
function parseTrackerDate(value) {
  const s = String(value ?? '').trim();
  if (!s) return null;
  const m = ISO_DAY.exec(s);
  if (m) {
    const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
    const t = Date.UTC(y, mo - 1, d);
    const back = new Date(t);
    return back.getUTCFullYear() === y && back.getUTCMonth() === mo - 1 && back.getUTCDate() === d ? { t, utc: true } : null;
  }
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : { t, utc: false };
}

// The row's Logged date as epoch ms (what the date sorts compare), or null when missing/invalid.
export function rowTime(row) {
  return parseTrackerDate(row?.timestamp)?.t ?? null;
}

// The Applications DATE column: MM/DD/YYYY with leading zeros (2026-01-05 -> "01/05/2026"); '' when missing/invalid.
// Display only -- sorting always uses rowTime(), never this string.
export function formatDateMMDDYYYY(value) {
  const p = parseTrackerDate(value);
  if (!p) return '';
  const d = new Date(p.t);
  const [y, mo, day] = p.utc ? [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()] : [d.getFullYear(), d.getMonth() + 1, d.getDate()];
  if (y < 0 || y > 9999) return '';
  return `${String(mo).padStart(2, '0')}/${String(day).padStart(2, '0')}/${String(y).padStart(4, '0')}`;
}

function rowScore(row) {
  const s = String(row?.score ?? '').trim();
  const n = s === '' ? NaN : Number(s);
  return Number.isFinite(n) ? n : null;
}

const text = (v) => String(v ?? '').trim();
const collator = new Intl.Collator('en', { sensitivity: 'base', numeric: true });
const rowNum = (row) => (Number.isFinite(Number(row?.row)) ? Number(row.row) : 0);

// Missing values always sort last, whatever the direction.
function cmpMissingLast(a, b, cmp) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return cmp(a, b);
}
const byDate = (dir) => (a, b) => cmpMissingLast(rowTime(a), rowTime(b), (x, y) => (x - y) * dir)
  || (rowNum(a) - rowNum(b)) * dir;   // same day: the later Sheet row was logged later
const byText = (field) => (a, b) => cmpMissingLast(text(a[field]) || null, text(b[field]) || null, (x, y) => collator.compare(x, y));
const newest = byDate(-1);

const COMPARATORS = {
  newest,
  oldest: byDate(1),
  score: (a, b) => cmpMissingLast(rowScore(a), rowScore(b), (x, y) => y - x) || newest(a, b),
  company: (a, b) => byText('company')(a, b) || byText('title')(a, b) || newest(a, b),
  role: (a, b) => byText('title')(a, b) || byText('company')(a, b) || newest(a, b),
};

export const validSort = (value) => (VALID.has(value) ? value : DEFAULT_SORT);

// A new, sorted array; the input rows are not mutated. Array.prototype.sort is stable.
export function sortRows(rows, sort) {
  return [...(rows || [])].sort(COMPARATORS[validSort(sort)]);
}

const storageOf = (storage) => {
  try { return storage === undefined ? globalThis.localStorage : storage; } catch { return null; }
};

// Read a page's saved sort; anything missing, invalid or unreadable falls back to Newest first.
export function loadSort(key, storage) {
  try { return validSort(storageOf(storage)?.getItem(key)); } catch { return DEFAULT_SORT; }
}

export function saveSort(key, value, storage) {
  try { storageOf(storage)?.setItem(key, validSort(value)); } catch { /* storage blocked: keep it for this visit only */ }
}

// ---- pagination (Applications, Find Jobs) ------------------------------------------------------
// Client-side: each page already holds its full result set (/api/tracker every row, /api/jobs up to
// 200), so a page change only re-slices what is loaded. Controls: components/pagination.js.
export const PAGE_SIZES = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 10;
export const PAGE_SIZE_KEY = 'jobagent.pageSize.applications';
// One remembered page size per page; the value is only the number.
export const PAGE_SIZE_KEYS = { applications: PAGE_SIZE_KEY, findJobs: 'jobagent.pageSize.findJobs' };

export const validPageSize = (value) => {
  const n = Number(value);
  return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE;
};

export function loadPageSize(storage, key = PAGE_SIZE_KEY) {
  try { return validPageSize(storageOf(storage)?.getItem(key)); } catch { return DEFAULT_PAGE_SIZE; }
}

export function savePageSize(value, storage, key = PAGE_SIZE_KEY) {
  try { storageOf(storage)?.setItem(key, String(validPageSize(value))); } catch { /* storage blocked: keep it for this visit only */ }
}

// The requested page clamped to 1..pages, and that page's slice of the (already filtered + sorted) rows.
// `start`/`end` are 0-based, end exclusive: "Showing start+1–end of total".
export function paginate(rows, page, size) {
  const list = rows || [];
  const pageSize = validPageSize(size);
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, Math.floor(Number(page)) || 1), pages);
  const start = (current - 1) * pageSize;
  const end = Math.min(start + pageSize, total);
  return { page: current, pages, pageSize, total, start, end, items: list.slice(start, end) };
}

// Page numbers to show, with null for a gap: always the first and last page and the current page ±1,
// so at most 7 entries however many pages there are. A gap of exactly one page shows that page instead.
export function pageList(current, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const lo = Math.max(2, Math.min(current - 1, pages - 4));
  const hi = Math.min(pages - 1, Math.max(current + 1, 5));
  const out = [1];
  if (lo > 2) out.push(lo === 3 ? 2 : null);
  for (let n = lo; n <= hi; n++) out.push(n);
  if (hi < pages - 1) out.push(hi === pages - 2 ? pages - 1 : null);
  out.push(pages);
  return out;
}

// Only an https Google Sheets document URL may become the "Open Google Sheet" link.
export function sheetHref(url) {
  try {
    const u = new URL(String(url));
    if (u.protocol !== 'https:' || u.hostname !== 'docs.google.com' || u.username || u.password || u.port) return null;
    return /^\/spreadsheets\/d\/[A-Za-z0-9_-]+(\/|$)/.test(u.pathname) ? u.href : null;
  } catch { return null; }
}
