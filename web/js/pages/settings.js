import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { loadable } from '../ui.js';
import { theme } from '../theme.js';

export function settingsPage(root) {
  const host = h('div', {});
  loadable(host, {
    load: () => api.get('/api/settings'),
    render: (d) => {
      const status = (s) => h('li', { class: 'source' }, h('span', {}, s.name),
        h('span', { class: `source-status ${s.active ? 'on' : 'off'}` }, h('span', { class: `dot ${s.active ? 'dot-green' : 'dot-grey'}` }), s.active ? 'Configured' : 'Not configured'));
      const themeSel = h('select', { class: 'input input-sm', 'aria-label': 'Theme', onChange: (e) => theme.set(e.target.value) },
        ...[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([v, l]) => h('option', { value: v, selected: theme.preference() === v }, l)));
      return h('div', { class: 'settings-grid' },
        h('section', { class: 'card' }, h('h3', {}, 'Appearance'), h('label', { class: 'field' }, h('span', {}, 'Theme'), themeSel)),
        h('section', { class: 'card' }, h('h3', {}, 'Master resume'),
          h('p', { class: 'muted' }, `${d.master_resume.path} — the only source tailored resumes may draw from.`),
          h('ul', { class: 'plain-list' }, h('li', {}, `${d.master_resume.roles.length} roles, ${d.master_resume.skills} skills listed`),
            h('li', {}, d.master_resume.years != null ? `${d.master_resume.years}+ years stated` : 'No years-of-experience stated'),
            h('li', {}, `Sections: ${d.master_resume.sections.join(', ')}`))),
        h('section', { class: 'card' }, h('h3', {}, 'Job sources'), h('ul', { class: 'source-list' }, ...d.job_sources.map(status))),
        h('section', { class: 'card' }, h('h3', {}, 'Integrations'), h('ul', { class: 'source-list' }, ...d.services.map(status)),
          h('p', { class: 'muted small' }, `LLM: ${d.llm.mode === 'local' ? 'local model' : 'cloud chain'} — ${d.llm.chain}`)),
        h('section', { class: 'card' }, h('h3', {}, 'Security'),
          h('ul', { class: 'plain-list' },
            h('li', {}, 'API keys, tokens and Google credentials stay on the server; this page only shows whether each is set.'),
            h('li', {}, d.auth.token_required ? 'Access token is required for this dashboard.' : 'Local mode: the dashboard listens on this machine only. Set DASHBOARD_TOKEN to require a token.'))));
    },
  });
  mount(root, h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Settings'), h('p', { class: 'muted' }, 'Configuration status. Secrets are never displayed.'))), host);
}
