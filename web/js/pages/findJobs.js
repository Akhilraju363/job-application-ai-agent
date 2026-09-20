import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { loadable } from '../ui.js';
import { jobsTable } from '../components/jobs.js';
import { runPipelineFlow } from '../components/pipelineRunner.js';

const FILTERS = [['all', 'All'], ['qualified', 'Qualified (8+)'], ['below', 'Below cutoff'], ['tailored', 'Tailored']];

export function findJobsPage(root, { query }) {
  const state = { filter: FILTERS.some(([k]) => k === query.get('filter')) ? query.get('filter') : 'all', q: '' };
  const host = h('div', {});

  const list = loadable(host, {
    skeleton: 'table',
    load: () => api.get(`/api/jobs${qs({ filter: state.filter, q: state.q, limit: 200 })}`),
    isEmpty: (d) => d.jobs.length === 0,
    empty: { title: state.q || state.filter !== 'all' ? 'No jobs match this filter' : 'No jobs found yet',
      text: state.q || state.filter !== 'all' ? 'Try another filter or search.' : 'Use Find New Jobs to scrape and score the latest listings.', iconName: 'search' },
    render: (d) => h('div', {}, h('p', { class: 'muted small' }, `${d.total} job${d.total === 1 ? '' : 's'}`), jobsTable(d.jobs, () => list.reload())),
  });

  const tabs = h('div', { class: 'tabs', role: 'tablist' }, ...FILTERS.map(([k, label]) =>
    h('button', { class: `tab ${state.filter === k ? 'active' : ''}`, role: 'tab', 'aria-selected': String(state.filter === k), onClick: (e) => {
      state.filter = k;
      tabs.querySelectorAll('.tab').forEach((t) => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
      e.currentTarget.classList.add('active'); e.currentTarget.setAttribute('aria-selected', 'true');
      list.reload();
    } }, label)));

  let timer;
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search title, company or location', 'aria-label': 'Search jobs',
    onInput: (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value.trim(); list.reload(); }, 300); } });

  mount(root,
    h('div', { class: 'page-head' },
      h('div', {}, h('h1', {}, 'Find Jobs'), h('p', { class: 'muted' }, 'Scraped and scored jobs. Pick one to view it or tailor a resume for it.')),
      h('div', { class: 'head-actions' },
        h('button', { class: 'btn btn-primary', onClick: () => runPipelineFlow('find', () => list.reload()) }, icon('search', 16), 'Find New Jobs'),
        h('button', { class: 'btn btn-outline', title: 'Scrape, score, tailor 8+ jobs, research and log to the tracker', onClick: () => runPipelineFlow('full', () => list.reload()) }, icon('zap', 16), 'Run full pipeline'))),
    h('section', { class: 'card' }, h('div', { class: 'toolbar' }, tabs, search), host));
}
