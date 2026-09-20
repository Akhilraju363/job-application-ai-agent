// JDAnalysisPanel: everything here is rendered from the backend's analysis + match
// (LLM-extracted JD requirements, coverage computed in code against the master resume).
import { h, svg } from '../dom.js';
import { icon } from '../icons.js';
import { badge } from '../ui.js';
import { matchTone } from '../lib/format.js';

function ring(pct) {
  const r = 34, c = 2 * Math.PI * r;
  return svg('svg', { class: `ring ring-${matchTone(pct)}`, viewBox: '0 0 84 84', role: 'img', 'aria-label': `Overall match ${pct}%` },
    svg('circle', { class: 'ring-track', cx: 42, cy: 42, r, fill: 'none', 'stroke-width': 8 }),
    svg('circle', { class: 'ring-value', cx: 42, cy: 42, r, fill: 'none', 'stroke-width': 8, 'stroke-linecap': 'round',
      'stroke-dasharray': `${(pct / 100) * c} ${c}`, transform: 'rotate(-90 42 42)' }),
    svg('text', { class: 'ring-text', x: 42, y: 48, 'text-anchor': 'middle' }, `${pct}%`));
}

function stat(label, pct, note) {
  return h('div', { class: 'stat' },
    h('div', { class: 'stat-head' }, h('span', {}, label), h('strong', {}, pct == null ? 'n/a' : `${pct}%`)),
    h('div', { class: 'meter', role: 'presentation' }, h('div', { class: `meter-fill meter-${matchTone(pct)}`, style: { width: `${pct ?? 0}%` } })),
    note && h('small', { class: 'muted' }, note));
}

const skillChips = (matched, missing) => h('div', { class: 'chips' },
  ...matched.map((t) => h('span', { class: 'chip chip-green', title: 'Found in your master resume' }, icon('check', 12), t)),
  ...missing.map((t) => h('span', { class: 'chip chip-red', title: 'Not in your master resume — omitted, never invented' }, t)));

function block(title, ...kids) { return h('section', { class: 'analysis-block' }, h('h4', {}, title), ...kids); }

export function jdAnalysisPanel(rec) {
  const { match: m, analysis: a, job } = rec;
  const kw = m.keywords, ex = m.experience;
  const expNote = ex.required_years == null ? 'The job description states no minimum years'
    : ex.resume_years == null ? 'Years not stated on your resume'
      : `${ex.resume_years}+ yrs on your resume vs ${ex.required_years} asked`;
  const kwNote = kw.tailored_pct != null && kw.base_pct != null
    ? `${kw.base_pct}% before tailoring → ${kw.tailored_pct}% after` : null;

  return h('div', { class: 'analysis' },
    h('div', { class: 'analysis-top' }, ring(m.overall),
      h('div', {}, h('h3', {}, 'Overall Match'),
        h('p', { class: 'muted' }, `${job.title} at ${job.company}`),
        job.pipeline_score != null && badge(`Pipeline fit score ${job.pipeline_score}/10`, job.pipeline_score >= 8 ? 'green' : 'neutral'))),
    h('div', { class: 'stats' },
      stat('Skills Match', m.skills.pct, `${m.skills.required_matched.length} of ${a.required_skills.length} required skills found`),
      stat('Experience Match', ex.pct, expNote),
      stat('Keyword Coverage', kw.tailored_pct ?? kw.base_pct, kwNote)),
    block('Required skills', a.required_skills.length ? skillChips(m.skills.required_matched, m.skills.required_missing) : h('span', { class: 'muted' }, 'None stated')),
    a.preferred_skills.length > 0 && block('Preferred skills', skillChips(m.skills.preferred_matched, m.skills.preferred_missing)),
    block('Missing / Not Verified Skills', m.missing_skills.length
      ? h('div', {}, h('div', { class: 'chips' }, ...m.missing_skills.map((t) => h('span', { class: 'chip chip-red' }, t))),
        h('small', { class: 'muted' }, 'Not supported by your master resume, so they are left out of the tailored version — never invented.'))
      : h('span', { class: 'muted' }, 'No gaps found')),
    block('Recommended resume emphasis', m.emphasis.length
      ? h('div', { class: 'chips' }, ...m.emphasis.map((t) => h('span', { class: 'chip chip-blue' }, t)))
      : h('span', { class: 'muted' }, 'No overlapping skills to emphasise')),
    block('Relevant experience', m.relevant_experience.length
      ? h('ul', { class: 'plain-list' }, ...m.relevant_experience.map((r) =>
        h('li', {}, h('strong', {}, r.role), r.dates && h('span', { class: 'muted' }, ` · ${r.dates}`),
          h('div', { class: 'chips' }, ...r.matched_terms.map((t) => h('span', { class: 'chip chip-soft' }, t))))))
      : h('span', { class: 'muted' }, 'No role in your master resume mentions these requirements')),
    block('Relevant projects', m.relevant_projects.length
      ? h('ul', { class: 'plain-list' }, ...m.relevant_projects.map((p) => h('li', {}, p)))
      : h('span', { class: 'muted' }, m.projects_note)),
    block('Relevant responsibilities (from your master resume)', (m.relevant_responsibilities || []).length
      ? h('ul', { class: 'plain-list' }, ...m.relevant_responsibilities.map((r) => h('li', {}, r.text, h('div', { class: 'muted small' }, r.role))))
      : h('span', { class: 'muted' }, 'None of your bullets mention these requirements')),
    block('Relevant achievements', (m.relevant_achievements || []).length
      ? h('ul', { class: 'plain-list' }, ...m.relevant_achievements.map((r) => h('li', {}, r.text)))
      : h('span', { class: 'muted' }, m.achievements_note || 'None')),
    h('details', { class: 'analysis-more' },
      h('summary', {}, 'Job description details'),
      a.domain && h('p', {}, h('strong', {}, 'Domain: '), a.domain),
      ...[['Programming languages', a.programming_languages], ['Frameworks', a.frameworks], ['Databases', a.databases], ['Cloud', a.cloud], ['Tools', a.tools],
        ['Experience requirements', a.experience_requirements], ['Education', a.education], ['Certifications', a.certifications], ['Responsibilities', a.responsibilities]]
        .filter(([, items]) => items && items.length)
        .map(([label, items]) => h('div', {}, h('strong', {}, label), h('div', { class: 'chips' }, ...items.map((t) => h('span', { class: 'chip chip-soft' }, t)))))));
}
