import { h } from '../dom.js';
import { relDay } from '../lib/format.js';

const TONE = {
  resume_tailored: 'blue', resume_blocked: 'red', jobs_found: 'green', jobs_scored: 'blue',
  company_research: 'purple', tracker_rows_added: 'green', tracker_saved: 'green',
  status_changed: 'orange', pipeline_run: 'purple',
};

export function recentActivity(events) {
  return h('ol', { class: 'timeline' }, ...events.map((e) =>
    h('li', { class: 'timeline-item' },
      h('span', { class: `timeline-dot dot-${TONE[e.kind] || 'grey'}` }),
      h('div', {}, h('div', { class: 'timeline-msg' }, e.message),
        h('time', { class: 'timeline-time', datetime: e.ts }, relDay(e.date_only ? e.ts.slice(0, 10) : e.ts))))));
}
