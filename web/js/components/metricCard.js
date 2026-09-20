import { h } from '../dom.js';
import { icon } from '../icons.js';
import { trendInfo, periodLabel } from '../lib/format.js';

const EMPTY_HINT = {
  jobs_found: 'No jobs found yet',
  resumes_tailored: 'No resumes tailored yet',
  applications_sent: 'No applications yet',
  interviews: 'No interviews yet',
};

// metric === null means the backing data source (the tracker Sheet) is unavailable.
export function metricCard({ id, label, iconName, tone }, metric, period, error, onRetry) {
  const t = trendInfo(metric);
  const body = metric === null
    ? [h('div', { class: 'metric-value muted' }, '—'),
       h('div', { class: 'metric-label' }, label),
       h('div', { class: 'metric-hint error-text', title: error || '' }, 'Tracker unavailable'),
       onRetry && h('button', { class: 'link-btn', onClick: onRetry }, 'Retry')]
    : [h('div', { class: 'metric-value' }, String(metric.current)),
       h('div', { class: 'metric-label' }, label),
       metric.current === 0 && metric.previous === 0 && h('div', { class: 'metric-hint' }, EMPTY_HINT[id])];

  return h('article', { class: `card metric metric-${tone}`, 'data-metric': id },
    h('div', { class: 'metric-icon' }, icon(iconName, 26)),
    h('div', { class: 'metric-body' }, ...body),
    metric && t && h('div', { class: 'metric-trend' },
      h('span', { class: `trend trend-${t.tone}`, title: `${metric.previous} in the previous period` },
        t.dir === 'up' ? '▲ ' : t.dir === 'down' ? '▼ ' : '', t.text),
      h('span', { class: 'metric-period' }, periodLabel(period))));
}
