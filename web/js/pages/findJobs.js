import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { loadable, pageHeader, searchInput } from '../ui.js';
import { jobsTable } from '../components/jobs.js';
import { pageSizeSelect, pager, refocusPager } from '../components/pagination.js';
import { runPipelineFlow } from '../components/pipelineRunner.js';
import { PAGE_SIZE_KEYS, loadPageSize, paginate, savePageSize } from '../lib/trackerView.js';

const FILTERS = [['all', 'All'], ['qualified', 'Qualified (8+)'], ['below', 'Below cutoff'], ['tailored', 'Tailored']];
// Client-side ordering of the loaded list (the API returns newest first).
const SORTS = {
  newest: ['Newest', null],
  match: ['Best match', (a, b) => (b.match_pct ?? -1) - (a.match_pct ?? -1)],
  company: ['Company A–Z', (a, b) => (a.company || '').localeCompare(b.company || '')],
};
export const sortJobs = (jobs, key) => (SORTS[key]?.[1] ? [...jobs].sort(SORTS[key][1]) : jobs);

// "Showing 11–20 of 93 jobs" when there is more than one page, else "93 jobs". The API caps a response at
// 200 jobs; when more match, the count says so (search or filter to narrow it).
function resultCount(pg, total) {
  const noun = (n) => `job${n === 1 ? '' : 's'}`;
  const text = pg.pages > 1 ? `Showing ${pg.start + 1}–${pg.end} of ${pg.total} ${noun(pg.total)}` : `${pg.total} ${noun(pg.total)}`;
  return h('p', { class: 'result-count', role: 'status' }, total > pg.total ? `${text} (first ${pg.total} of ${total} loaded)` : text);
}

export function findJobsPage(root, { query }) {
  const state = { filter: FILTERS.some(([k]) => k === query.get('filter')) ? query.get('filter') : 'all', q: '', sort: 'newest',
    page: 1, pageSize: loadPageSize(undefined, PAGE_SIZE_KEYS.findJobs) };
  const host = h('div', {});
  let last = null;   // the current response, so sort / page / page-size changes re-slice it without refetching

  // filter + search (server) -> sort -> page: only the current page's rows are rendered
  const render = (d) => {
    last = d;
    const pg = paginate(sortJobs(d.jobs, state.sort), state.page, state.pageSize);
    state.page = pg.page;   // clamp: a smaller result set (e.g. after a status change reload) never leaves an empty page
    return h('div', {}, resultCount(pg, d.total), jobsTable(pg.items, () => list.reload()),
      pg.pages > 1 && pager(pg, go, 'Find Jobs pages'));
  };
  const rerender = () => { if (last) mount(host, render(last)); };
  const go = (n, focusKey) => { state.page = n; rerender(); refocusPager(host, focusKey); };

  const list = loadable(host, {
    skeleton: 'table',
    load: () => { last = null; return api.get(`/api/jobs${qs({ filter: state.filter, q: state.q, limit: 200 })}`); },
    isEmpty: (d) => d.jobs.length === 0,
    empty: { title: state.q || state.filter !== 'all' ? 'No jobs match this filter' : 'No jobs found yet',
      text: state.q || state.filter !== 'all' ? 'Try another filter or search.' : 'Use Find New Jobs to scrape and score the latest listings.', iconName: 'search' },
    render,
  });
  // a new result set (tab, search, pipeline run) starts on page 1; a row's status change keeps the page
  const refetch = () => { state.page = 1; list.reload(); };

  const tabs = h('div', { class: 'tabs', role: 'tablist' }, ...FILTERS.map(([k, label]) =>
    h('button', { class: `tab ${state.filter === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(state.filter === k), onClick: (e) => {
      state.filter = k;
      tabs.querySelectorAll('.tab').forEach((t) => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
      e.currentTarget.classList.add('active'); e.currentTarget.setAttribute('aria-selected', 'true');
      refetch();
    } }, label)));

  const search = searchInput({ label: 'Search jobs', placeholder: 'Search title, company or location', onSearch: (v) => { state.q = v; refetch(); } });
  const sort = h('select', { class: 'input input-sm', 'aria-label': 'Sort jobs', onChange: (e) => { state.sort = e.target.value; state.page = 1; rerender(); } },
    ...Object.entries(SORTS).map(([k, [label]]) => h('option', { value: k }, label)));
  const size = pageSizeSelect({ value: state.pageSize, onChange: (n) => {
    state.pageSize = n; state.page = 1; savePageSize(n, undefined, PAGE_SIZE_KEYS.findJobs); rerender();
  } });

  mount(root,
    pageHeader({
      title: 'Find Jobs', subtitle: 'Scraped and scored against your master resume. Open a job to see why it scored, or tailor a resume for it.',
      actions: [
        h('button', { class: 'btn btn-outline', title: 'Scrape, score, tailor 8+ jobs, research and log to the tracker', onClick: () => runPipelineFlow('full', refetch) }, icon('zap', 16), 'Run full pipeline'),
        h('button', { class: 'btn btn-primary', onClick: () => runPipelineFlow('find', refetch) }, icon('search', 16), 'Find New Jobs'),
      ],
    }),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs, h('div', { class: 'toolbar-right' }, search, sort, size)), host));
}
