// ATSValidation: the measurable ATS report. Every number comes from the backend's
// tailoring_service.ats_validation (weighted components computed from the resume text).
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { matchTone } from '../lib/format.js';

const STATUS_ICON = { pass: ['check', 'green'], warn: ['alert', 'orange'], fail: ['x', 'red'] };

const chips = (items, tone) => (items?.length
  ? h('div', { class: 'chips' }, ...items.map((t) => h('span', { class: `chip chip-${tone}` }, t)))
  : h('span', { class: 'muted' }, 'None'));

export function atsValidation(ats) {
  if (!ats) return null;
  const score = ats.score;
  return h('section', { class: 'ats', 'aria-label': 'ATS validation' },
    h('div', { class: 'ats-head' },
      h('h4', {}, 'ATS validation'),
      score != null && h('span', { class: `ats-score ats-${matchTone(score)}`, title: 'Weighted score of the criteria below' }, `${score}/100`)),
    ats.components?.length > 0 && h('table', { class: 'ats-table' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Criterion'), h('th', { scope: 'col' }, 'Weight'), h('th', { scope: 'col' }, 'Score'))),
      h('tbody', {}, ...ats.components.map((c) => h('tr', {},
        h('td', {}, c.name), h('td', {}, `${c.weight}%`), h('td', {}, c.score == null ? 'n/a' : `${c.score}%`))))),
    h('div', { class: 'ats-facts' },
      h('span', {}, 'Keyword coverage: ', h('strong', {}, ats.keyword_coverage == null ? 'n/a' : `${ats.keyword_coverage}%`)),
      h('span', {}, 'Required-skill coverage: ', h('strong', {}, ats.required_skill_coverage == null ? 'n/a' : `${ats.required_skill_coverage}%`))),
    ats.issues?.length > 0 && h('div', { class: 'problems', role: 'alert' }, h('strong', {}, 'Issues'), h('ul', {}, ...ats.issues.map((i) => h('li', {}, i)))),
    ats.warnings?.length > 0 && h('ul', { class: 'warnings' }, ...ats.warnings.map((w) => h('li', {}, icon('alert', 14), w))),
    h('details', {},
      h('summary', {}, `Keywords (${ats.missing_keywords?.length ?? 0} missing)`),
      h('h5', {}, 'Missing — not in your master resume, so not added'), chips(ats.not_in_master_resume, 'red'),
      ats.duplicate_keywords?.length > 0 && [h('h5', {}, 'Repeated too often'), chips(ats.duplicate_keywords, 'blue')]),
    ats.checks?.length > 0 && h('details', {}, h('summary', {}, 'Structure & formatting checks'),
      h('ul', { class: 'checks' }, ...ats.checks.map((c) => {
        const [ic, tone] = STATUS_ICON[c.status];
        return h('li', { class: `check check-${tone}` }, icon(ic, 14), h('span', {}, c.name, c.detail && h('small', { class: 'muted' }, ` — ${c.detail}`)));
      }))),
    ats.disclaimer && h('p', { class: 'muted small' }, ats.disclaimer));
}
