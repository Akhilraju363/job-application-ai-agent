import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { loadable } from '../ui.js';
import { navigate } from '../router.js';
import { metricCard } from '../components/metricCard.js';
import { overviewChart, statusChart } from '../components/charts.js';
import { quickActions } from '../components/quickActions.js';
import { jobsTable } from '../components/jobs.js';
import { recentActivity } from '../components/activity.js';
import { configuredSources } from '../components/sources.js';
import { todayISO } from '../lib/format.js';

const KPIS = [
  { id: 'jobs_found', label: 'Jobs Found', iconName: 'search', tone: 'blue' },
  { id: 'resumes_tailored', label: 'Resumes Tailored', iconName: 'file', tone: 'green' },
  { id: 'applications_sent', label: 'Applications Sent', iconName: 'send', tone: 'purple' },
  { id: 'interviews', label: 'Interviews', iconName: 'calendar', tone: 'orange' },
];
const RANGES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['custom', 'Custom range']];

const card = (title, ...kids) => h('section', { class: 'card' }, h('div', { class: 'card-head' }, title), ...kids);

export function dashboardPage(root, { profile }) {
  const range = { key: '7d', from: '', to: '' };
  const rq = () => qs({ range: range.key, from: range.from, to: range.to });

  const first = (profile.name || 'there').split(' ')[0];
  const kpiHost = h('div', { class: 'kpi-host' });
  const chartHost = h('div', { class: 'chart-host' });
  const statusHost = h('div', {});
  const jobsHost = h('div', {});
  const activityHost = h('div', {});
  const sourcesHost = h('div', {});

  // Every section loads independently: one failing (e.g. Google Sheet offline) shows its own
  // error + retry and never blanks the page.
  const kpi = loadable(kpiHost, {
    skeleton: 'lines',
    load: () => api.get(`/api/dashboard/summary${rq()}`),
    render: (d, reload) => h('div', { class: 'grid-kpi-inner' }, ...KPIS.map((k) =>
      metricCard(k, d.metrics[k.id], d.period, d.tracker.error, reload))),
  });
  const overview = loadable(chartHost, {
    skeleton: 'chart',
    load: () => api.get(`/api/dashboard/overview${rq()}`),
    isEmpty: (d) => d.series.every((s) => !s.value) && d.tracker.available,
    empty: { title: 'No activity in this period', text: 'Run Find New Jobs, or pick a longer range.', iconName: 'search' },
    render: (d, reload) => h('div', {}, overviewChart(d.series),
      !d.tracker.available && h('p', { class: 'error-text small' }, `Application data unavailable (${d.tracker.error}). `,
        h('button', { class: 'link-btn', onClick: reload }, 'Retry')),
      h('p', { class: 'muted small' }, `${d.period.start} → ${d.period.end}. “Qualified” = scored 8+ by the pipeline.`)),
  });
  const status = loadable(statusHost, {
    load: () => api.get('/api/dashboard/status'),
    isEmpty: (d) => d.total === 0 && d.tracker.available,
    empty: { title: 'No applications yet', text: 'Jobs you save or tailor a resume for appear in your tracker.', iconName: 'send' },
    render: (d, reload) => (d.tracker.available
      ? h('div', {}, statusChart(d, (s) => navigate(`/applications?status=${encodeURIComponent(s)}`)),
        d.tracker.stale && h('p', { class: 'muted small' }, 'Showing cached data — the tracker is temporarily unreachable.'))
      : h('div', { class: 'state state-error' }, h('p', {}, `Tracker unavailable: ${d.tracker.error}`),
        h('button', { class: 'btn btn-outline btn-sm', onClick: reload }, 'Retry'))),
  });
  const jobs = loadable(jobsHost, {
    skeleton: 'table',
    load: () => api.get('/api/dashboard/jobs?limit=6'),
    isEmpty: (d) => d.jobs.length === 0,
    empty: { title: 'No jobs found yet', text: 'Use Find New Jobs to scrape and score the latest listings.', iconName: 'search' },
    render: (d) => jobsTable(d.jobs, refreshAll, { scroll: true }),
  });
  const activity = loadable(activityHost, {
    load: () => api.get('/api/dashboard/activity?limit=8'),
    isEmpty: (d) => d.events.length === 0,
    empty: { title: 'No activity yet', text: 'Pipeline runs, tailored resumes and status changes show up here.', iconName: 'calendar' },
    render: (d) => recentActivity(d.events),
  });
  const sources = loadable(sourcesHost, { load: () => api.get('/api/dashboard/sources'), render: configuredSources });

  function refreshAll() { kpi.reload(); overview.reload(); status.reload(); jobs.reload(); activity.reload(); }

  const customFields = h('div', { class: 'range-custom', hidden: true },
    h('input', { type: 'date', class: 'input input-sm', 'aria-label': 'From date', max: todayISO(), onChange: (e) => { range.from = e.target.value; } }),
    h('span', { class: 'muted' }, 'to'),
    h('input', { type: 'date', class: 'input input-sm', 'aria-label': 'To date', max: todayISO(), onChange: (e) => { range.to = e.target.value; } }),
    h('button', { class: 'btn btn-soft btn-sm', onClick: () => { if (range.from && range.to) { kpi.reload(); overview.reload(); } } }, 'Apply'));
  const select = h('select', { class: 'input input-sm', 'aria-label': 'Date range', onChange: (e) => {
    range.key = e.target.value;
    customFields.hidden = range.key !== 'custom';
    if (range.key !== 'custom') { kpi.reload(); overview.reload(); }
  } }, ...RANGES.map(([v, l]) => h('option', { value: v }, l)));

  mount(root,
    h('div', { class: 'page-head dash-head' },
      h('div', {}, h('h1', {}, `Hello, ${first}! 👋`), h('p', { class: 'muted' }, 'Your AI-powered job search assistant is working for you.')),
      h('aside', { class: 'card info-card' }, icon('quote', 22),
        h('div', {}, h('strong', {}, '“Discipline today creates opportunities tomorrow.”'),
          h('p', { class: 'muted' }, 'The agent prepares. You decide where to apply.')))),
    kpiHost,
    h('div', { class: 'grid-mid' },
      card(h('div', { class: 'card-head-row' }, 'Applications Overview', h('div', { class: 'range-controls' }, select, customFields)), chartHost),
      card('Application Status', statusHost),
      card('Quick Actions', quickActions({ onJobsChanged: refreshAll }))),
    h('div', { class: 'grid-bottom' },
      card(h('div', { class: 'card-head-row' }, 'Latest Job Matches', h('a', { class: 'link', href: '/find-jobs', 'data-link': true }, 'View All →')), jobsHost),
      h('div', { class: 'stack' },
        card(h('div', { class: 'card-head-row' }, 'Recent Activity'), activityHost),
        card(h('div', { class: 'card-head-row' }, 'Configured Sources', h('a', { class: 'link', href: '/job-alerts', 'data-link': true }, 'Edit')), sourcesHost))));
}

