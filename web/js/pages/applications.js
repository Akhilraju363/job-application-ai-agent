// Applications (filterable table) and Job Tracker (board). Both read the same
// /api/tracker data -- the Google Sheet is the single source of truth.
import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { emptyState, errorState, loadable, pageHeader, scoreBadge, searchInput, statusBadge, toast } from '../ui.js';
import { navigate } from '../router.js';
import { relDay, safeHref } from '../lib/format.js';
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
const notConfigured = () => emptyState({ title: 'No tracker sheet yet', iconName: 'list',
  text: 'It’s created automatically the first time a job is saved or the pipeline logs one.' });
const findJobsAction = () => h('a', { class: 'btn btn-primary btn-sm', href: '/find-jobs', 'data-link': true }, icon('search', 14), 'Find Jobs');
const noApplications = (title = 'No applications yet') => emptyState({ title, iconName: 'send',
  text: 'Your applications appear here after you save a job from Find Jobs or save a tailored resume to the tracker.', action: findJobsAction() });

// Client-side search over the loaded tracker rows (role / company) -- no extra Sheet reads.
export const matchesQuery = (row, q) => !q || `${row.title} ${row.company}`.toLowerCase().includes(q.toLowerCase());

// loadable() + a search box that re-renders the last response instead of refetching the Sheet.
function searchableSection(host, load, render, label) {
  let last = null;
  const state = { q: '' };
  const list = loadable(host, { skeleton: 'table', load, render: (d, reload) => { last = [d, reload]; return render(d, reload, state.q); } });
  const search = searchInput({ label, placeholder: 'Search role or company', onSearch: (v) => {
    state.q = v;
    if (last) mount(host, render(last[0], last[1], state.q));
  } });
  return { list, search };
}

export function applicationsPage(root, { query }) {
  const wanted = query.get('status');
  const status = TRACKER_STATUSES.includes(wanted) ? wanted : '';
  const host = h('div', {});
  const render = (d, reload, q) => {
    if (!d.tracker.available) return errorState(`The tracker couldn’t be read: ${d.tracker.error}`, reload);
    if (!d.tracker.configured) return notConfigured();
    if (d.rows.length === 0) return noApplications(status ? `No ${status} applications` : undefined);
    const rows = d.rows.filter((r) => matchesQuery(r, q));
    if (rows.length === 0) return emptyState({ title: 'No applications match your search', text: 'Try a different company or role.', iconName: 'search' });
    return h('div', {}, trackerBanner(d.tracker),
      h('p', { class: 'result-count', role: 'status' }, `${rows.length} application${rows.length === 1 ? '' : 's'}`),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, ...['Role', 'Company', 'Fit', 'Status', 'Resume', 'Logged', 'Source', ''].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', {}, ...rows.map((r) => {
          const href = safeHref(r.link);
          return h('tr', {}, h('td', { class: 'cell-title' }, r.title), h('td', {}, r.company),
            h('td', {}, scoreBadge(r.score || null)), h('td', {}, statusSelect(r, () => list.reload())),
            h('td', {}, resumeCell(r)), h('td', { class: 'nowrap' }, relDay(r.timestamp) || '—'), h('td', {}, r.source || 'LinkedIn'),
            h('td', {}, href ? h('a', { class: 'btn-icon', href, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Open ${r.title}`, title: 'Open job posting' }, icon('ext', 16)) : null));
        })))));
  };
  const { list, search } = searchableSection(host, () => api.get(`/api/tracker${qs({ status })}`), render, 'Search applications');

  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Filter by status' }, ...[['', 'All'], ...TRACKER_STATUSES.map((s) => [s, s])].map(([k, label]) =>
    h('button', { class: `tab ${status === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(status === k),
      onClick: () => navigate(k ? `/applications?status=${encodeURIComponent(k)}` : '/applications') }, label)));

  mount(root,
    pageHeader({ title: 'Applications', subtitle: 'Everything in your tracker. The agent never submits an application — you apply, then update the status here.',
      actions: h('button', { class: 'btn btn-outline', onClick: () => list.reload() }, icon('refresh', 16), 'Refresh') }),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs, h('div', { class: 'toolbar-right' }, search)), host));
}

export function trackerBoardPage(root) {
  const host = h('div', {});
  const render = (d, reload, q) => {
    if (!d.tracker.available) return errorState(`The tracker couldn’t be read: ${d.tracker.error}`, reload);
    if (!d.tracker.configured) return notConfigured();
    if (d.rows.length === 0) return noApplications();
    const visible = d.rows.filter((r) => matchesQuery(r, q));
    return h('div', {}, trackerBanner(d.tracker), h('div', { class: 'board' }, ...TRACKER_STATUSES.map((s) => {
      const rows = visible.filter((r) => r.status === s);
      return h('section', { class: 'board-col', 'aria-label': `${s}: ${rows.length}` },
        h('header', {}, statusBadge(s), h('span', { class: 'muted' }, String(rows.length))),
        ...rows.map((r) => h('article', { class: 'board-card' }, h('strong', {}, r.title), h('span', { class: 'muted' }, r.company),
          h('div', { class: 'board-meta' }, r.score ? scoreBadge(r.score) : null, resumeCell(r)),
          statusSelect(r, () => list.reload()))),
        rows.length === 0 && h('p', { class: 'muted small' }, q ? 'No matches' : 'Nothing here'));
    })));
  };
  const { list, search } = searchableSection(host, () => api.get('/api/tracker'), render, 'Search the tracker');
  mount(root,
    pageHeader({ title: 'Job Tracker', subtitle: 'A board view of the same tracker data — change a status and the Google Sheet updates.',
      actions: h('button', { class: 'btn btn-outline', onClick: () => list.reload() }, icon('refresh', 16), 'Refresh') }),
    h('div', { class: 'toolbar' }, h('div', { class: 'toolbar-right' }, search)),
    host);
}
