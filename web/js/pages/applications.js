// Applications: the tracker as a filterable, sortable, paginated table over /api/tracker
// -- the Google Sheet is the single source of truth. Search and sort run on
// the loaded rows (no extra Sheet reads); the sort choice is remembered per page.
import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { confirmDialog, emptyState, errorState, loadable, pageHeader, scoreBadge, searchInput, statusBadge, toast } from '../ui.js';
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

const deleting = new Set();
async function deleteApplication(row, reload) {
  if (deleting.has(row.key)) return;
  if (!await confirmDialog({
    title: 'Delete job?',
    body: `Delete the application record for ${row.title} at ${row.company}? Only this Google Sheets tracker row will be deleted; resumes, job artifacts, interviews, and unrelated records will remain.`,
    confirmLabel: 'Delete Job', tone: 'red',
  })) return;
  deleting.add(row.key);
  try {
    await api.delete(`/api/tracker/${row.key}`);
    toast('Job deleted', 'success');
    reload();
  } catch (e) { toast(e.message, 'error'); } finally { deleting.delete(row.key); }
}

function reportBulkDelete(result, state) {
  const records = Array.isArray(result?.results) ? result.results : [];
  const deleted = records.filter((record) => record.status === 'deleted');
  const failed = records.filter((record) => record.status !== 'deleted');
  for (const record of deleted) state.selected.delete(record.id);
  const details = failed.slice(0, 3).map((record) => {
    const row = state.rowsByKey.get(record.id);
    const label = row ? `${row.title} at ${row.company}` : record.id;
    return `${label}: ${record.message || 'Deletion failed'}`;
  }).join('; ');
  const extra = failed.length > 3 ? `; and ${failed.length - 3} more` : '';
  toast(`${deleted.length} deleted; ${failed.length} failed${details ? `. ${details}${extra}` : ''}`,
    failed.length ? 'error' : 'success');
  return deleted.length;
}

async function deleteSelectedApplications(state, reload) {
  if (state.bulkPending || state.selected.size === 0) return;
  const ids = [...state.selected];
  if (ids.some((id) => deleting.has(id))) {
    toast('A selected application is already being deleted.', 'error');
    return;
  }
  state.bulkPending = true;
  state.rerender();
  let reloadStarted = false;
  try {
    const noun = ids.length === 1 ? 'application' : 'applications';
    const confirmed = await confirmDialog({
      title: `Delete ${ids.length} ${noun}?`,
      body: `This permanently deletes ${ids.length} ${noun} from the Google Sheets tracker. This action cannot be undone. Generated resumes, job artifacts, interviews, and unrelated tracker rows will not be deleted.`,
      confirmLabel: `Delete ${ids.length}`,
      tone: 'red',
    });
    if (!confirmed) return;
    for (const id of ids) deleting.add(id);
    try {
      const result = await api.post('/api/tracker/bulk-delete', { ids });
      reportBulkDelete(result, state);
      reload();
      reloadStarted = true;
    } catch (error) {
      if (Array.isArray(error.details?.results)) {
        const deleted = reportBulkDelete(error.details, state);
        if (deleted) {
          reload();
          reloadStarted = true;
        }
      } else {
        toast(error.message, 'error');
      }
    } finally {
      for (const id of ids) deleting.delete(id);
    }
  } finally {
    state.bulkPending = false;
    if (!reloadStarted) state.rerender();
  }
}

const trackerBanner = (t) => (t.stale ? h('p', { class: 'notice notice-warn' }, `Showing cached data — the tracker is temporarily unreachable (${t.error}).`) : null);
const notConfigured = () => emptyState({ title: 'Tracker spreadsheet ID is not configured', iconName: 'list',
  text: 'Set google_sheet_id to the existing tracker spreadsheet in your local environment or protected Modal settings.' });
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
  const state = { q: '', sort: loadSort(sortKey), page: 1, pageSize, selected: new Set(), rowsByKey: new Map(), bulkPending: false };
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
    if (d.rows.length === 0) {
      st.selected.clear();
      return noApplications(status ? `No ${status} applications` : undefined);
    }
    // status filter (server) -> search -> sort -> page: only the current page's rows are rendered
    const matching = sortRows(d.rows.filter((r) => matchesQuery(r, st.q)), st.sort);
    const matchingKeys = new Set(matching.map((r) => r.key));
    for (const key of st.selected) if (!matchingKeys.has(key)) st.selected.delete(key);
    st.rowsByKey = new Map(d.rows.map((r) => [r.key, r]));
    if (matching.length === 0) return emptyState({ title: 'No applications match your search', text: 'Try a different company or role.', iconName: 'search' });
    const pg = paginate(matching, st.page, st.pageSize);
    st.page = pg.page;   // clamp: a smaller result set never leaves an empty page
    const rows = pg.items;
    const selectedOnPage = rows.filter((r) => st.selected.has(r.key)).length;
    const allOnPageSelected = rows.length > 0 && selectedOnPage === rows.length;
    const selectAll = h('input', { class: 'selection-checkbox', type: 'checkbox',
      'aria-label': 'Select all applications on this page', checked: allOnPageSelected, disabled: st.bulkPending,
      onChange: (e) => {
        for (const row of rows) {
          if (e.target.checked) st.selected.add(row.key);
          else st.selected.delete(row.key);
        }
        st.rerender();
      } });
    selectAll.indeterminate = selectedOnPage > 0 && !allOnPageSelected;
    const noun = `application${pg.total === 1 ? '' : 's'}`;
    return h('div', {}, trackerBanner(d.tracker),
      h('p', { class: 'result-count', role: 'status' }, pg.pages > 1 ? `Showing ${pg.start + 1}–${pg.end} of ${pg.total} ${noun}` : `${pg.total} ${noun}`),
      h('div', { class: 'bulk-delete-toolbar' },
        h('span', { class: 'bulk-delete-count', 'aria-live': 'polite' }, `${st.selected.size} selected`),
        h('button', { class: 'btn btn-danger btn-sm bulk-delete-button', type: 'button',
          disabled: st.selected.size === 0 || st.bulkPending,
          onClick: () => deleteSelectedApplications(st, reload) },
        icon('trash', 14), st.bulkPending ? 'Processing…' : 'Delete Selected')),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, h('th', { scope: 'col', class: 'selection-cell' }, selectAll),
          ...['Role', 'Company', 'Fit', 'Status', 'Suitability', 'Resume', 'Date', 'Source', 'Actions'].map((t) =>
            h('th', { scope: 'col', class: t === 'Actions' ? 'application-actions-header' : '' }, t)))),
        h('tbody', {}, ...rows.map((r) => {
          const href = safeHref(r.link);
          return h('tr', {}, h('td', { class: 'selection-cell' }, h('input', { class: 'selection-checkbox', type: 'checkbox',
            'aria-label': `Select ${r.title} at ${r.company}`, checked: st.selected.has(r.key),
            disabled: st.bulkPending || deleting.has(r.key),
            onChange: (e) => {
              if (e.target.checked) st.selected.add(r.key);
              else st.selected.delete(r.key);
              st.rerender();
            } })), h('td', { class: 'cell-title' }, r.title), h('td', {}, r.company),
            h('td', {}, scoreBadge(r.score || null)), h('td', {}, statusSelect(r, () => list.reload())),
            h('td', {}, suitabilitySelect(r, () => list.reload())),
            h('td', {}, resumeCell(r)), h('td', { class: 'nowrap cell-date' }, formatDateMMDDYYYY(r.timestamp) || '—'), h('td', {}, r.source || 'LinkedIn'),
          h('td', { class: 'row-actions application-row-actions' },
              href
                ? h('span', { class: 'application-open-slot' },
                  h('a', { class: 'btn btn-outline btn-sm', href, target: '_blank', rel: 'noopener noreferrer',
                    'aria-label': `Open ${r.title}`, title: 'Open job posting' }, icon('ext', 14), 'Open'))
                : h('span', { class: 'application-open-slot', 'aria-hidden': 'true' }),
              h('button', { class: 'btn btn-danger btn-sm', type: 'button', disabled: st.bulkPending || deleting.has(r.key),
                onClick: () => deleteApplication(r, list.reload) },
                icon('trash', 14), 'Delete')));
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
