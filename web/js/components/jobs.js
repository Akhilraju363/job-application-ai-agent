// LatestJobsTable + row actions + job detail drawer. Shared by the dashboard and Find Jobs.
import { h } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { badge, loadable, menu, openDrawer, toast } from '../ui.js';
import { navigate } from '../router.js';
import { matchTone, relDay, safeHref, statusTone } from '../lib/format.js';

export const TRACKER_STATUSES = ['Not Applied', 'Applied', 'Interviewing', 'Offer', 'Rejected'];

export const matchBadge = (pct) => (pct == null ? h('span', { class: 'muted' }, '—') : badge(`${pct}%`, matchTone(pct), 'badge-match'));

export async function saveJob(job, onChange) {
  try {
    const r = await api.post(`/api/jobs/${job.key}/save`, {});
    toast(r.result === 'added' ? 'Saved to your tracker' : 'Already in your tracker', 'success');
    onChange?.();
  } catch (e) { toast(e.message, 'error'); }
}

// Shared by the inline status dropdown and (for back-compat) any other status-change caller.
// A job not yet in the tracker is saved first (as "Not Applied") -- same two-step flow the
// backend already expects -- then PATCH /api/tracker/status makes the actual change, which
// tracker_service.py writes straight to the Google Sheet (the source of truth).
async function persistStatus(job, status) {
  if (!job.tracker_status) await api.post(`/api/jobs/${job.key}/save`, {});
  await api.patch('/api/tracker/status', { link: job.link, status });
  job.tracker_status = status;
}

export async function setJobStatus(job, status, onChange) {
  try {
    await persistStatus(job, status);
    toast(`Marked ${status}`, 'success');
    onChange?.();
  } catch (e) { toast(e.message, 'error'); }
}

// Pipeline (qualification) states that stand in for "no application status yet" when the
// dropdown has nothing real to show. Deliberately NOT part of TRACKER_STATUSES/STATUS_OPTIONS
// -- it's never sent to the backend, only ever the dropdown's initial display value.
const QUALIFICATION_PLACEHOLDER_STATES = new Set(['Below cutoff']);

// Inline, editable Status cell for the jobs table. Safe update, not optimistic: the dropdown
// is disabled and shows "Saving…" while the request is in flight; on failure the previous
// value is restored and nothing is left showing a status that was never persisted.
function statusCell(job, onChange) {
  // A job that's never been saved to the tracker has no application status yet. Most such jobs
  // just default to "Not Applied" -- but a job scored below the 8+ cutoff shouldn't silently
  // read as "Not Applied" (that implies it qualified and simply hasn't been touched); instead
  // the dropdown itself shows "Below cutoff" until a real status is chosen, with no separate
  // "Not Applied" text alongside it. The moment any real status is set, that persisted value
  // takes over (job.tracker_status, and the pipeline `state` itself, both follow the tracker row).
  const showPlaceholder = !job.tracker_status && QUALIFICATION_PLACEHOLDER_STATES.has(job.state);
  const current = job.tracker_status || (showPlaceholder ? job.state : 'Not Applied');
  const note = h('span', { class: 'muted small status-note', hidden: true }, 'Saving…');
  const sel = h('select', {
    class: 'input input-sm status-select',
    'aria-label': `Application status for ${job.title || 'this job'}`,
    onChange: async (e) => {
      const chosen = e.target.value;
      if (!TRACKER_STATUSES.includes(chosen)) return; // the placeholder option itself -- nothing to persist
      sel.disabled = true;
      note.hidden = false;
      try {
        await persistStatus(job, chosen);
        toast(`Marked ${chosen}`, 'success');
        onChange?.();
      } catch (err) {
        sel.value = current;
        toast(err.message, 'error');
      } finally {
        sel.disabled = false;
        note.hidden = true;
      }
    },
  },
  showPlaceholder && h('option', { value: job.state, selected: true }, job.state),
  ...TRACKER_STATUSES.map((s) => h('option', { value: s, selected: !showPlaceholder && s === current }, s)));
  // Other pipeline states ("Qualified", "Tailored", "Unscored") aren't an application status
  // either, but -- unlike "Below cutoff" -- reading them as "Not Applied" isn't misleading, so
  // the dropdown just shows "Not Applied" with no separate hint.
  return h('div', { class: 'status-cell' }, sel, note);
}

export function openJob(job) {
  const contents = h('div', { class: 'drawer-body' });
  const ov = openDrawer(contents, { label: `${job.title} at ${job.company}` });
  loadable(contents, {
    load: () => api.get(`/api/jobs/${job.key}`),
    render: (d) => jobDetail(d, ov.close),
  });
}

function chips(items, tone) {
  return items.length ? h('div', { class: 'chips' }, ...items.map((t) => h('span', { class: `chip chip-${tone}` }, t))) : h('span', { class: 'muted' }, 'None listed');
}

function jobDetail(j, close) {
  const href = safeHref(j.link);
  const resume = safeHref(j.resume_link);
  return h('div', {},
    h('div', { class: 'drawer-head' },
      h('div', {}, h('h2', {}, j.title), h('p', { class: 'muted' }, `${j.company}${j.location ? ' · ' + j.location : ''} · ${j.source}`)),
      h('button', { class: 'btn-icon', 'aria-label': 'Close', onClick: close }, icon('x', 18))),
    h('div', { class: 'detail-meta' }, matchBadge(j.match_pct), badge(j.state, statusTone(j.state)),
      j.score != null && h('span', { class: 'muted' }, `Pipeline score ${j.score}/10 (${j.qualified ? 'meets' : 'below'} the 8+ cutoff)`),
      h('span', { class: 'muted' }, `Posted ${relDay(j.posted_date || j.found_at) || 'unknown'}`)),
    j.reasoning && h('section', {}, h('h4', {}, 'Why this score'), h('p', {}, j.reasoning)),
    h('section', {}, h('h4', {}, 'Matched requirements'), chips(j.matched, 'green')),
    h('section', {}, h('h4', {}, 'Missing requirements'), chips(j.missing, 'red')),
    j.company_notes && h('section', {}, h('h4', {}, 'Company notes'), h('p', { class: 'prewrap' }, j.company_notes)),
    h('div', { class: 'detail-actions' },
      h('button', { class: 'btn btn-primary', onClick: () => { close(); navigate(`/tailor-resume?job=${j.key}`); } }, icon('wand', 16), 'Tailor Resume'),
      href && h('a', { class: 'btn btn-outline', href, target: '_blank', rel: 'noopener noreferrer' }, icon('ext', 16), 'Open job posting'),
      resume && h('a', { class: 'btn btn-outline', href: resume, target: '_blank', rel: 'noopener noreferrer' }, icon('file', 16), 'Pipeline resume')),
    h('section', {}, h('h4', {}, 'Job description'), j.description ? h('div', { class: 'jd-text prewrap' }, j.description) : h('p', { class: 'muted' }, 'No description was scraped.')));
}

export function jobActions(job, onChange) {
  const href = safeHref(job.link);
  return h('div', { class: 'row-actions' },
    h('button', { class: 'btn btn-soft btn-sm', onClick: () => openJob(job) }, 'View'),
    h('button', { class: 'btn btn-outline btn-sm', title: 'Generate a JD-specific resume for this job', onClick: () => navigate(`/tailor-resume?job=${job.key}`) }, 'Tailor'),
    menu(`More actions for ${job.title}`, [
      { label: 'Open job posting', icon: 'ext', href },
      // Status itself is changed via the inline dropdown in the table (see statusCell) --
      // this only covers adding an untracked job with no status change.
      { label: job.tracker_status ? 'In tracker' : 'Save to tracker', icon: 'bookmark', disabled: !!job.tracker_status, onClick: () => saveJob(job, onChange) },
    ].filter((i) => i.href !== null)));
}

// `scroll: true` caps the table body's height and makes it scroll internally with a sticky
// header, instead of letting the page grow indefinitely -- used for the dashboard's "Latest
// Job Matches" card; the full Find Jobs page keeps its normal page-level scrolling.
export function jobsTable(jobs, onChange, { scroll = false } = {}) {
  const th = (t) => h('th', { scope: 'col' }, t);
  return h('div', { class: scroll ? 'table-wrap table-wrap-scroll' : 'table-wrap' }, h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, ...['Job Title', 'Company', 'Match', 'Location', 'Posted', 'Source', 'Status', 'Action'].map(th))),
    h('tbody', {}, ...jobs.map((j) => h('tr', {},
      h('td', { class: 'cell-title' }, h('button', { class: 'link-btn', onClick: () => openJob(j) }, j.title || 'Untitled')),
      h('td', {}, j.company || '—'), h('td', {}, matchBadge(j.match_pct)), h('td', {}, j.location || '—'),
      h('td', { class: 'nowrap' }, relDay(j.posted_date || j.found_at) || '—'), h('td', {}, j.source),
      h('td', {}, statusCell(j, onChange)), h('td', {}, jobActions(j, onChange)))))));
}

