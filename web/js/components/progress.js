// ResumeGenerationProgress / pipeline progress. Renders the backend task's *actual* stage
// list (from /api/tasks/<id>); there is no timer-driven fake progress anywhere.
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { spinner } from '../ui.js';

export function stageList(stages) {
  return h('ol', { class: 'stages', 'aria-label': 'Progress' }, ...stages.map((s) =>
    h('li', { class: `stage stage-${s.status}`, 'aria-current': s.status === 'active' ? 'step' : null },
      h('span', { class: 'stage-mark' },
        s.status === 'done' ? icon('check', 14) : s.status === 'active' ? spinner('spinner-sm')
          : s.status === 'error' ? icon('x', 14) : null),
      h('span', {}, s.label))));
}

export function pendingStages(stages) {
  return stages.map(([key, label]) => ({ key, label, status: 'pending' }));
}
