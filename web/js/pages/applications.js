// Applications: the tracker as a filterable, sortable, paginated table over /api/tracker
// -- the Google Sheet is the single source of truth. Search and sort run on
// the loaded rows (no extra Sheet reads); the sort choice is remembered per page.
import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { emptyState, errorState, loadable, pageHeader, scoreBadge, searchInput, statusBadge, toast } from '../ui.js';
import { navigate } from '../router.js';
import { safeHref } from '../lib/format.js';
import { SUITABILITIES, TRACKER_STATUSES } from '../components/jobs.js';
import { pageSizeSelect, pager, refocusPager } from '../components/pagination.js';
import {
  SORT_KEYS, SORT_OPTIONS, formatDateMMDDYYYY, loadPageSize, loadSort, paginate, savePageSize, saveSort, sheetHref, sortRows,
} from '../lib/trackerView.js';

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

function suitabilitySelect(row, onChanged) {
  return h('select', { class: 'input input-sm suitability-select', 'aria-label': `Suitability for ${row.title}`, onChange: async (e) => {
    const sel = e.target;
    const prev = row.suitability || 'Not reviewed';
    sel.disabled = true;
    try {
      await api.patch('/api/tracker/suitability', { link: row.link, suitability: sel.value });
      toast(`Marked ${sel.value}`, 'success');
      onChanged();
    } catch (err) { toast(err.message, 'error'); sel.value = prev; } finally { sel.disabled = false; }
  } }, ...SUITABILITIES.map((s) => h('option', { value: s, selected: s === (row.suitability || 'Not reviewed') }, s)));
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

// "Open Google Sheet": the link the backend reports for the tracker sheet, opened in a new tab --
// only when it is a real https Google Sheets URL; otherwise a short note instead.
export function sheetAction(url) {
  const href = sheetHref(url);
  if (!href) return h('span', { class: 'muted small sheet-missing', role: 'note' }, 'Google Sheets link is not configured.');
  return h('a', { class: 'btn btn-outline sheet-link', href, target: '_blank', rel: 'noopener noreferrer',
    'aria-label': 'Open Google Sheet (opens in a new tab)', title: 'Open the tracker in Google Sheets' }, icon('ext', 16), 'Open Google Sheet');
}

export function sortSelect({ value, label, onSort }) {
  const sel = h('select', { class: 'input input-sm sort-select', 'aria-label': label, onChange: (e) => onSort(e.target.value) },
    ...SORT_OPTIONS.map((o) => h('option', { value: o.value, selected: o.value === value }, o.label)));
  return h('label', { class: 'sort-control' }, h('span', { class: 'sort-label', 'aria-hidden': 'true' }, 'Sort by'), sel);
}

// loadable() + search and sort controls that re-render the last response instead of refetching the Sheet.
// The page header's sheet slot is filled from the same response. Search and sort send `state.page`
// back to 1; render() clamps it to the pages that exist.
function searchableSection(host, load, render, { label, sortLabel, sortKey, sheetSlot, pageSize }) {
  let last = null;
  const state = { q: '', sort: loadSort(sortKey), page: 1, pageSize };
  const rerender = () => { if (last) mount(host, render(last[0], last[1], state)); };
  state.go = (n, focusKey) => { state.page = n; rerender(); refocusPager(host, focusKey); };
  state.rerender = rerender;
  const list = loadable(host, { skeleton: 'table', load, render: (d, reload) => {
    last = [d, reload];
    mount(sheetSlot, d.tracker?.configured === false ? null : sheetAction(d.tracker?.sheet_url));
    return render(d, reload, state);
  } });
  const search = searchInput({ label, placeholder: 'Search role or company', onSearch: (v) => { state.q = v; state.page = 1; rerender(); } });
  const sort = sortSelect({ value: state.sort, label: sortLabel, onSort: (v) => { state.sort = v; state.page = 1; saveSort(sortKey, v); rerender(); } });
  return { list, search, sort, state };
}

export function applicationsPage(root, { query }) {
  const wanted = query.get('status');
  const status = TRACKER_STATUSES.includes(wanted) ? wanted : '';
  const wantedSuitability = query.get('suitability');
  const suitability = SUITABILITIES.includes(wantedSuitability) ? wantedSuitability : '';
  const host = h('div', {});
  const sheetSlot = h('span', { class: 'sheet-slot' });
  const render = (d, reload, st) => {
    if (!d.tracker.available) return errorState(`The tracker couldn’t be read: ${d.tracker.error}`, reload);
    if (!d.tracker.configured) return notConfigured();
    if (d.rows.length === 0) return noApplications(status ? `No ${status} applications` : undefined);
    // status filter (server) -> search -> sort -> page: only the current page's rows are rendered
    const matching = sortRows(d.rows.filter((r) => matchesQuery(r, st.q)), st.sort);
    if (matching.length === 0) return emptyState({ title: 'No applications match your search', text: 'Try a different company or role.', iconName: 'search' });
    const pg = paginate(matching, st.page, st.pageSize);
    st.page = pg.page;   // clamp: a smaller result set never leaves an empty page
    const rows = pg.items;
    const noun = `application${pg.total === 1 ? '' : 's'}`;
    return h('div', {}, trackerBanner(d.tracker),
      h('p', { class: 'result-count', role: 'status' }, pg.pages > 1 ? `Showing ${pg.start + 1}–${pg.end} of ${pg.total} ${noun}` : `${pg.total} ${noun}`),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, ...['Role', 'Company', 'Fit', 'Status', 'Suitability', 'Resume', 'Date', 'Source', ''].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', {}, ...rows.map((r) => {
          const href = safeHref(r.link);
          return h('tr', {}, h('td', { class: 'cell-title' }, r.title), h('td', {}, r.company),
            h('td', {}, scoreBadge(r.score || null)), h('td', {}, statusSelect(r, () => list.reload())),
            h('td', {}, suitabilitySelect(r, () => list.reload())),
            h('td', {}, resumeCell(r)), h('td', { class: 'nowrap cell-date' }, formatDateMMDDYYYY(r.timestamp) || '—'), h('td', {}, r.source || 'LinkedIn'),
            h('td', {}, href ? h('a', { class: 'btn-icon', href, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Open ${r.title}`, title: 'Open job posting' }, icon('ext', 16)) : null));
        })))),
      pg.pages > 1 && pager(pg, st.go, 'Applications pages'));
  };
  const { list, search, sort, state } = searchableSection(host, () => api.get(`/api/tracker${qs({ status, suitability })}`), render,
    { label: 'Search applications', sortLabel: 'Sort applications', sortKey: SORT_KEYS.applications, sheetSlot, pageSize: loadPageSize() });
  // a status tab is a navigation, so it re-creates this page and starts on page 1
  const size = pageSizeSelect({ value: state.pageSize, onChange: (n) => { state.pageSize = n; state.page = 1; savePageSize(n); state.rerender(); } });
  const refresh = () => { state.page = 1; list.reload(); };

  const suitabilityFilter = h('label', { class: 'sort-control' }, h('span', { class: 'sort-label', 'aria-hidden': 'true' }, 'Suitability'),
    h('select', { class: 'input input-sm suitability-filter', 'aria-label': 'Filter by suitability', onChange: (e) => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      if (e.target.value) params.set('suitability', e.target.value);
      navigate(`/applications${params.toString() ? `?${params}` : ''}`);
    } }, h('option', { value: '', selected: !suitability }, 'All'), ...SUITABILITIES.map((s) =>
      h('option', { value: s, selected: s === suitability }, s))));
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Filter by status' }, ...[['', 'All'], ...TRACKER_STATUSES.map((s) => [s, s])].map(([k, label]) =>
    h('button', { class: `tab ${status === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(status === k),
      onClick: () => {
        const params = new URLSearchParams();
        if (k) params.set('status', k);
        if (suitability) params.set('suitability', suitability);
        navigate(`/applications${params.toString() ? `?${params}` : ''}`);
      } }, label)));

  mount(root,
    pageHeader({ title: 'Applications', subtitle: 'Everything in your tracker. The agent never submits an application — you apply, then update the status here.',
      actions: [sheetSlot, h('button', { class: 'btn btn-outline', onClick: refresh }, icon('refresh', 16), 'Refresh')] }),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs, h('div', { class: 'toolbar-right' }, suitabilityFilter, search, sort, size)), host));
}
