// Applications (filterable table) and Job Tracker (board). Both read the same
// /api/tracker data -- the Google Sheet is the single source of truth.
import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { badge, errorState, loadable, toast } from '../ui.js';
import { navigate } from '../router.js';
import { matchTone, relDay, safeHref, statusTone } from '../lib/format.js';
import { TRACKER_STATUSES } from '../components/jobs.js';

function statusSelect(row, onChanged) {
  return h('select', { class: 'input input-sm status-select', 'aria-label': `Status for ${row.title}`, onChange: async (e) => {
    const sel = e.target;
    const prev = row.status;
    sel.disabled = true;
    try {
      await api.patch('/api/tracker/status', { link: row.link, status: sel.value });
      toast(`Marked ${sel.value}`, 'success');
      onChanged();
    } catch (err) { toast(err.message, 'error'); sel.value = prev; } finally { sel.disabled = false; }
  } }, ...TRACKER_STATUSES.map((s) => h('option', { value: s, selected: s === row.status }, s)));
}

function resumeCell(row) {
  const href = safeHref(row.resume_path);
  if (href) return h('a', { class: 'link', href, target: '_blank', rel: 'noopener noreferrer' }, 'Open resume');
  return row.resume_path ? h('span', { class: 'muted small', title: row.resume_path }, 'Local file') : h('span', { class: 'muted' }, '—');
}

const trackerBanner = (t) => (t.stale ? h('p', { class: 'notice notice-warn' }, `Showing cached data — the tracker is temporarily unreachable (${t.error}).`) : null);
const notConfigured = () => h('div', { class: 'state state-empty' }, h('strong', {}, 'No tracker sheet yet'),
  h('p', {}, 'It’s created automatically the first time a job is saved or the pipeline logs one.'));

export function applicationsPage(root, { query }) {
  const wanted = query.get('status');
  const state = { status: TRACKER_STATUSES.includes(wanted) ? wanted : '' };
  const host = h('div', {});
  const list = loadable(host, {
    skeleton: 'table',
    load: () => api.get(`/api/tracker${qs({ status: state.status })}`),
    render: (d, reload) => {
      if (!d.tracker.available) return errorState(`The tracker couldn’t be read: ${d.tracker.error}`, reload);
      if (!d.tracker.configured) return notConfigured();
      if (d.rows.length === 0) return h('div', { class: 'state state-empty' }, h('strong', {}, state.status ? `No ${state.status} applications` : 'No applications yet'),
        h('p', {}, 'Save a job from Find Jobs, or generate a tailored resume and save it to the tracker.'));
      return h('div', {}, trackerBanner(d.tracker), h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, ...['Job Title', 'Company', 'Fit', 'Status', 'Resume', 'Logged', 'Source', ''].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', {}, ...d.rows.map((r) => {
          const href = safeHref(r.link);
          const score = Number(r.score);
          return h('tr', {}, h('td', { class: 'cell-title' }, r.title), h('td', {}, r.company),
            h('td', {}, r.score ? badge(`${r.score}/10`, matchTone(score * 10)) : '—'), h('td', {}, statusSelect(r, () => list.reload())),
            h('td', {}, resumeCell(r)), h('td', { class: 'nowrap' }, relDay(r.timestamp) || '—'), h('td', {}, r.source || 'LinkedIn'),
            h('td', {}, href ? h('a', { class: 'btn-icon', href, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Open ${r.title}`, title: 'Open job posting' }, icon('ext', 16)) : null));
        })))));
    },
  });

  const tabs = h('div', { class: 'tabs', role: 'tablist' }, ...[['', 'All'], ...TRACKER_STATUSES.map((s) => [s, s])].map(([k, label]) =>
    h('button', { class: `tab ${state.status === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(state.status === k),
      onClick: () => navigate(k ? `/applications?status=${encodeURIComponent(k)}` : '/applications') }, label)));

  mount(root,
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Applications'),
      h('p', { class: 'muted' }, 'Everything in your tracker. The agent never submits an application — you apply, then update the status here.')),
    h('div', { class: 'head-actions' }, h('button', { class: 'btn btn-outline', onClick: () => list.reload() }, icon('refresh', 16), 'Refresh'))),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs), host));
}

export function trackerBoardPage(root) {
  const host = h('div', {});
  const list = loadable(host, {
    skeleton: 'table',
    load: () => api.get('/api/tracker'),
    render: (d, reload) => {
      if (!d.tracker.available) return errorState(`The tracker couldn’t be read: ${d.tracker.error}`, reload);
      if (!d.tracker.configured || d.rows.length === 0) return notConfigured();
      return h('div', {}, trackerBanner(d.tracker), h('div', { class: 'board' }, ...TRACKER_STATUSES.map((s) => {
        const rows = d.rows.filter((r) => r.status === s);
        return h('section', { class: 'board-col', 'aria-label': s },
          h('header', {}, badge(s, statusTone(s)), h('span', { class: 'muted' }, String(rows.length))),
          ...rows.map((r) => h('article', { class: 'board-card' }, h('strong', {}, r.title), h('span', { class: 'muted' }, r.company),
            h('div', { class: 'board-meta' }, r.score ? badge(`${r.score}/10`, matchTone(Number(r.score) * 10)) : null, resumeCell(r)),
            statusSelect(r, () => list.reload()))),
          rows.length === 0 && h('p', { class: 'muted small' }, 'Nothing here'));
      })));
    },
  });
  mount(root,
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Job Tracker'), h('p', { class: 'muted' }, 'A board view of the same tracker data — change a status and the Google Sheet updates.')),
      h('div', { class: 'head-actions' }, h('button', { class: 'btn btn-outline', onClick: () => list.reload() }, icon('refresh', 16), 'Refresh'))),
    host);
}
