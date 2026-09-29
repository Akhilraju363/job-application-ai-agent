// Settings: configuration *status*, grouped as in docs/ui/UI_SETTINGS.md. Booleans and labels
// only -- secrets never reach the browser. Every section maps to something that exists.
import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { loadable, pageHeader } from '../ui.js';
import { theme } from '../theme.js';
import { signOut } from '../lib/authClient.js';

const POSTED = { past24Hours: 'Past 24 hours', pastWeek: 'Past week', pastMonth: 'Past month' };

const section = (iconName, title, ...kids) => h('section', { class: 'card settings-section' }, h('h3', {}, icon(iconName, 16), title), ...kids);
const kv = (pairs) => h('dl', { class: 'kv-list' }, ...pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]));
const foot = (...kids) => h('div', { class: 'card-foot' }, ...kids);
const link = (href, label) => h('a', { class: 'link', href, 'data-link': true }, label, ' →');

export function settingsPage(root, { profile = {} } = {}) {
  const host = h('div', {});
  loadable(host, {
    load: async () => {
      const [settings, prefs] = await Promise.all([api.get('/api/settings'), api.get('/api/preferences').catch(() => null)]);
      return { ...settings, prefs };
    },
    render: (d) => {
      const status = (s) => h('li', { class: 'source' }, h('span', {}, s.name),
        h('span', { class: `source-status ${s.active ? 'on' : 'off'}` }, h('span', { class: `dot ${s.active ? 'dot-green' : 'dot-grey'}`, 'aria-hidden': 'true' }), s.active ? 'Configured' : 'Not configured'));
      const themeSel = h('select', { class: 'input', id: 'settings-theme', onChange: (e) => theme.set(e.target.value) },
        ...[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([v, l]) => h('option', { value: v, selected: theme.preference() === v }, l)));
      const m = d.master_resume;
      const p = d.prefs?.preferences;
      const signedIn = d.auth.password_required;

      return h('div', { class: 'settings-grid' },
        section('sun', 'Appearance',
          h('label', { class: 'field', for: 'settings-theme' }, h('span', {}, 'Theme'), themeSel,
            h('small', { class: 'muted' }, 'Applies immediately and is remembered in this browser.'))),
        section('user', 'Account',
          kv([['Profile', profile.name || '—'], ['Sign-in', signedIn ? 'Username and password required' : 'Local mode — no sign-in, this machine only']]),
          signedIn && foot(h('button', { class: 'btn btn-outline btn-sm', onClick: async () => { await signOut(); location.reload(); } }, icon('logout', 14), 'Sign out'))),
        section('bell', 'Job Preferences',
          p ? kv([['Keywords', p.keywords], ['Location', p.location], ['Posted within', POSTED[p.date_posted] || p.date_posted], ['Jobs per run', String(p.limit)]])
            : h('p', { class: 'muted' }, 'Preferences are unavailable right now.'),
          foot(link('/job-alerts', 'Edit job preferences'))),
        section('file', 'Resume',
          kv([['Master resume', h('code', {}, m.path)], ['Roles', String(m.roles.length)], ['Skills listed', String(m.skills)],
            ['Experience stated', m.years != null ? `${m.years}+ years` : 'Not stated'], ['Sections', m.sections.join(', ')]]),
          h('p', { class: 'muted small' }, 'The only source tailored resumes may draw from.'),
          foot(link('/master-resume', 'Open Master Resume'))),
        section('zap', 'Integrations',
          h('h4', {}, 'Job sources'), h('ul', { class: 'source-list' }, ...d.job_sources.map(status)),
          h('h4', {}, 'Services'), h('ul', { class: 'source-list' }, ...d.services.map(status)),
          h('p', { class: 'muted small' }, `LLM: ${d.llm.mode === 'local' ? 'local model' : 'cloud chain'} — ${d.llm.chain}`)),
        section('settings', 'System',
          h('ul', { class: 'plain-list' },
            h('li', {}, 'API keys, tokens and Google credentials stay on the server; this page only shows whether each is set.'),
            h('li', {}, signedIn ? 'Every API call needs a signed-in session.' : 'The dashboard listens on this machine only.')),
          foot(link('/logs', 'View application logs'))));
    },
  });
  mount(root, pageHeader({ title: 'Settings', subtitle: 'How this workspace is configured. Secrets are never displayed.' }), host);
}
