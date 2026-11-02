import { h } from '../dom.js';
import { relDay } from '../lib/format.js';

const TONE = {
  resume_tailored: 'blue', resume_blocked: 'red', jobs_found: 'green', jobs_scored: 'blue',
  company_research: 'purple', tracker_rows_added: 'green', tracker_saved: 'green',
  status_changed: 'orange', pipeline_run: 'purple',
};

export function recentActivity(events) {
  let expanded = false;
  const host = h('div', {});
  const render = () => {
    const visible = expanded ? events : events.slice(0, 3);
    const hidden = Math.max(0, events.length - 3);
    host.replaceChildren(
      h('ol', { class: 'timeline' }, ...visible.map((e) =>
    h('li', { class: 'timeline-item' },
      h('span', { class: `timeline-dot dot-${TONE[e.kind] || 'grey'}` }),
      h('div', {}, h('div', { class: 'timeline-msg' }, e.message),
        h('time', { class: 'timeline-time', datetime: e.ts }, relDay(e.date_only ? e.ts.slice(0, 10) : e.ts)))))),
      hidden > 0 && h('button', { class: 'link-btn activity-toggle', type: 'button', 'aria-expanded': String(expanded),
        'aria-label': expanded ? 'Show fewer recent activity entries' : `Show all ${events.length} recent activity entries`,
        onClick: () => { expanded = !expanded; render(); } }, expanded ? 'Show less' : `Show more (${hidden})`),
    );
  };
  render();
  return host;
}
