import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { loadable, pageHeader, searchInput } from '../ui.js';
import { jobsTable } from '../components/jobs.js';
import { runPipelineFlow } from '../components/pipelineRunner.js';

const FILTERS = [['all', 'All'], ['qualified', 'Qualified (8+)'], ['below', 'Below cutoff'], ['tailored', 'Tailored']];
// Client-side ordering of the loaded list (the API returns newest first).
const SORTS = {
  newest: ['Newest', null],
  match: ['Best match', (a, b) => (b.match_pct ?? -1) - (a.match_pct ?? -1)],
  company: ['Company A–Z', (a, b) => (a.company || '').localeCompare(b.company || '')],
};
export const sortJobs = (jobs, key) => (SORTS[key]?.[1] ? [...jobs].sort(SORTS[key][1]) : jobs);

export function findJobsPage(root, { query }) {
  const state = { filter: FILTERS.some(([k]) => k === query.get('filter')) ? query.get('filter') : 'all', q: '', sort: 'newest' };
  const host = h('div', {});

  const list = loadable(host, {
    skeleton: 'table',
    load: () => api.get(`/api/jobs${qs({ filter: state.filter, q: state.q, limit: 200 })}`),
    isEmpty: (d) => d.jobs.length === 0,
    empty: { title: state.q || state.filter !== 'all' ? 'No jobs match this filter' : 'No jobs found yet',
      text: state.q || state.filter !== 'all' ? 'Try another filter or search.' : 'Use Find New Jobs to scrape and score the latest listings.', iconName: 'search' },
    render: (d) => h('div', {}, h('p', { class: 'result-count', role: 'status' }, `${d.total} job${d.total === 1 ? '' : 's'}`),
      jobsTable(sortJobs(d.jobs, state.sort), () => list.reload())),
  });

  const tabs = h('div', { class: 'tabs', role: 'tablist' }, ...FILTERS.map(([k, label]) =>
    h('button', { class: `tab ${state.filter === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(state.filter === k), onClick: (e) => {
      state.filter = k;
      tabs.querySelectorAll('.tab').forEach((t) => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
      e.currentTarget.classList.add('active'); e.currentTarget.setAttribute('aria-selected', 'true');
      list.reload();
    } }, label)));

  const search = searchInput({ label: 'Search jobs', placeholder: 'Search title, company or location', onSearch: (v) => { state.q = v; list.reload(); } });
  const sort = h('select', { class: 'input input-sm', 'aria-label': 'Sort jobs', onChange: (e) => { state.sort = e.target.value; list.reload(); } },
    ...Object.entries(SORTS).map(([k, [label]]) => h('option', { value: k }, label)));

  mount(root,
    pageHeader({
      title: 'Find Jobs', subtitle: 'Scraped and scored against your master resume. Open a job to see why it scored, or tailor a resume for it.',
      actions: [
        h('button', { class: 'btn btn-outline', title: 'Scrape, score, tailor 8+ jobs, research and log to the tracker', onClick: () => runPipelineFlow('full', () => list.reload()) }, icon('zap', 16), 'Run full pipeline'),
        h('button', { class: 'btn btn-primary', onClick: () => runPipelineFlow('find', () => list.reload()) }, icon('search', 16), 'Find New Jobs'),
      ],
    }),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs, h('div', { class: 'toolbar-right' }, search, sort)), host));
}
