import { h } from '../dom.js';

const dot = (active) => h('span', { class: `dot ${active ? 'dot-green' : 'dot-grey'}`, 'aria-hidden': 'true' });

// Booleans and labels only -- the API never returns keys, tokens or ids.
export function configuredSources({ job_sources: jobSources, services, llm }) {
  return h('div', { class: 'sources' },
    h('ul', { class: 'source-list' }, ...jobSources.map((s) =>
      h('li', { class: 'source' },
        h('div', {}, h('strong', {}, s.name), h('span', { class: 'muted' }, ` via ${s.via}`)),
        h('span', { class: `source-status ${s.active ? 'on' : 'off'}` }, dot(s.active), s.active ? 'Active' : 'Inactive')))),
    h('p', { class: 'muted small' }, 'LinkedIn is the only job source the scraper supports today.'),
    h('div', { class: 'chips' }, ...services.map((s) =>
      h('span', { class: `chip ${s.active ? 'chip-on' : 'chip-off'}`, title: s.active ? 'Configured' : 'Not configured' },
        dot(s.active), s.name))),
    h('p', { class: 'muted small' }, `LLM: ${llm.mode === 'local' ? 'local model' : 'cloud chain'} — ${llm.chain}`));
}
