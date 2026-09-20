// "Job Alerts" = the job search preferences that drive Find New Jobs. The project has no
// notification-alert feature, so this page is honest about what it controls.
import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { loadable, toast, withBusy } from '../ui.js';
import { runPipelineFlow } from '../components/pipelineRunner.js';

const POSTED = { past24Hours: 'Past 24 hours', pastWeek: 'Past week', pastMonth: 'Past month' };

export function alertsPage(root) {
  const host = h('div', {});
  loadable(host, {
    load: () => api.get('/api/preferences'),
    render: (d, reload) => {
      const p = d.preferences;
      const kw = h('input', { class: 'input', id: 'pref-kw', value: p.keywords, maxlength: 200, required: true });
      const loc = h('input', { class: 'input', id: 'pref-loc', value: p.location, maxlength: 100, required: true });
      const posted = h('select', { class: 'input', id: 'pref-posted' }, ...d.date_posted_options.map((o) => h('option', { value: o, selected: o === p.date_posted }, POSTED[o] || o)));
      const limit = h('input', { class: 'input', id: 'pref-limit', type: 'number', min: 1, max: d.limit_cap, value: p.limit });
      const err = h('p', { class: 'error-text', role: 'alert', hidden: true });
      const save = h('button', { class: 'btn btn-primary', type: 'submit' }, icon('check', 16), 'Save preferences');
      const field = (id, label, input, hint) => h('label', { class: 'field', for: id }, h('span', {}, label), input, hint && h('small', { class: 'muted' }, hint));

      return h('div', { class: 'tailor-grid' },
        h('form', { class: 'card', onSubmit: async (e) => {
          e.preventDefault();
          err.hidden = true;
          try {
            await withBusy(save, () => api.put('/api/preferences', { keywords: kw.value, location: loc.value, date_posted: posted.value, limit: Number(limit.value) }));
            toast('Preferences saved', 'success');
            reload();
          } catch (ex) { err.textContent = ex.message; err.hidden = false; }
        } },
        h('h3', {}, 'Job search preferences'),
        field('pref-kw', 'Keywords', kw, `Default: ${d.defaults.keywords}`), field('pref-loc', 'Location', loc, `Default: ${d.defaults.location}`),
        h('div', { class: 'field-row' }, field('pref-posted', 'Posted within', posted), field('pref-limit', 'Jobs per run', limit, `Max ${d.limit_cap} in the current mode`)),
        err, h('div', { class: 'form-actions' }, save,
          h('button', { class: 'btn btn-outline', type: 'button', onClick: () => runPipelineFlow('find') }, icon('search', 16), 'Run search now'))),
        h('div', { class: 'card' }, h('h3', {}, 'How this is used'),
          h('ul', { class: 'plain-list' },
            h('li', {}, 'Saved preferences are used by ', h('strong', {}, 'Find New Jobs'), ' and local pipeline runs.'),
            h('li', {}, 'The scheduled Modal run has no access to this file and keeps its own defaults.'),
            h('li', {}, 'The project targets one niche — Full-Stack Java / Spring Boot / Angular. Changing keywords is your call, but scoring is still against your master resume.'),
            h('li', {}, 'The daily failure alert (Telegram) is configured through environment settings, not here.'))));
    },
  });
  mount(root, h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Job Alerts'), h('p', { class: 'muted' }, 'Set your job search preferences.'))), host);
}
