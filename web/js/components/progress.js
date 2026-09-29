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

// Tailor Resume workflow position (docs/ui/UI_TAILOR_RESUME.md). Driven only by real state:
// the server task's stages while it runs, the shown record afterwards -- never a timer.
export const TAILOR_STEPS = ['Job description', 'Analyze', 'Match', 'Tailor', 'Validate', 'Preview', 'Export'];
const STAGE_STEP = { received: 1, analyze: 1, match: 2, generate: 3, validate: 4, prepare: 4 };

export function stepFromStages(stages = []) {
  const active = stages.find((s) => s.status === 'active') || [...stages].reverse().find((s) => s.status === 'done');
  return active ? (STAGE_STEP[active.key] ?? 1) : 1;
}

// current = index of the step in progress; `complete` marks every step done (e.g. after an export).
export function workflowSteps(labels, current, { complete = false } = {}) {
  return h('ol', { class: 'steps', 'aria-label': 'Workflow' }, ...labels.map((label, i) => {
    const done = complete || i < current;
    const now = !complete && i === current;
    return h('li', { class: `step${done ? ' step-done' : ''}${now ? ' step-current' : ''}`, 'aria-current': now ? 'step' : null },
      h('span', { class: 'step-num', 'aria-hidden': 'true' }, done ? icon('check', 12) : String(i + 1)),
      h('span', {}, label, done ? h('span', { class: 'sr-only' }, ' (done)') : null));
  }));
}
