// App shell: sidebar + header + router. Every page renders into <main>.
import { h, mount } from './dom.js';
import { api, auth } from './api.js';
import { ensureSession, signOut } from './lib/authClient.js';
import { loginView } from './components/loginForm.js';
import { icon } from './icons.js';
import { errorState } from './ui.js';
import { currentRoute, onRoute } from './router.js';
import { theme } from './theme.js';
import { initials } from './lib/format.js';
import { dashboardPage } from './pages/dashboard.js';
import { findJobsPage } from './pages/findJobs.js';
import { tailorPage } from './pages/tailor.js';
import { applicationsPage, trackerBoardPage } from './pages/applications.js';
import { alertsPage } from './pages/alerts.js';
import { settingsPage } from './pages/settings.js';
import { logsPage } from './pages/logs.js';

const NAV = [
  { path: '/', label: 'Dashboard', icon: 'home', page: dashboardPage },
  { path: '/find-jobs', label: 'Find Jobs', icon: 'search', page: findJobsPage },
  { path: '/tailor-resume', label: 'Tailor Resume', icon: 'file', page: tailorPage },
  { path: '/applications', label: 'Applications', icon: 'send', page: applicationsPage },
  { path: '/job-tracker', label: 'Job Tracker', icon: 'list', page: trackerBoardPage },
  { path: '/job-alerts', label: 'Job Alerts', icon: 'bell', page: alertsPage },
  { path: '/logs', label: 'Logs', icon: 'terminal', page: logsPage },
  { path: '/settings', label: 'Settings', icon: 'settings', page: settingsPage },
];

const app = document.getElementById('app');

// Resolves once the server has accepted a username/password (the session cookie is set by then).
function showLogin() {
  return new Promise((resolve) => {
    const form = loginView({ onSignedIn: () => resolve() });
    mount(app, form);
    form.querySelector('input')?.focus();
  });
}

function buildShell(profile) {
  const links = NAV.map((n) => h('a', { class: 'nav-link', href: n.path, 'data-link': true, 'data-path': n.path }, icon(n.icon, 20), h('span', {}, n.label)));
  const main = h('main', { id: 'main', class: 'main', tabindex: '-1' });
  const scrim = h('div', { class: 'scrim', onClick: () => shell.classList.remove('nav-open') });
  const themeBtn = h('button', { class: 'btn-icon', 'aria-label': 'Toggle dark mode', title: 'Toggle theme', onClick: () => theme.toggle() });
  const syncTheme = () => mount(themeBtn, icon(theme.effective() === 'dark' ? 'sun' : 'moon', 20));
  syncTheme();
  window.addEventListener('themechange', syncTheme);

  const shell = h('div', { class: 'shell' },
    h('aside', { class: 'sidebar', 'aria-label': 'Primary' },
      h('a', { class: 'brand', href: '/', 'data-link': true }, h('span', { class: 'brand-mark' }, icon('briefcase', 22)), h('span', { class: 'brand-text' }, 'Job Application AI Agent')),
      h('nav', { class: 'nav' }, ...links),
      h('div', { class: 'sidebar-foot' },
        h('div', { class: 'profile' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(profile.name)),
          h('div', { class: 'profile-text' }, h('strong', {}, profile.name || 'Your profile'), h('small', {}, profile.email))),
        profile.auth_required && h('button', { class: 'signout', onClick: async () => { await signOut(); location.reload(); } }, icon('logout', 18), h('span', {}, 'Logout')))),
    h('div', { class: 'main-col' },
      h('header', { class: 'topbar' },
        h('button', { class: 'btn-icon menu-toggle', 'aria-label': 'Open navigation', onClick: () => shell.classList.toggle('nav-open') }, icon('menu', 22)),
        h('div', { class: 'topbar-right' },
          h('time', { class: 'topbar-date', datetime: new Date().toISOString().slice(0, 10) },
            new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })), themeBtn)),
      main),
    scrim);
  mount(app, shell);
  return { main, links, shell };
}

async function boot() {
  auth.onUnauthorized = () => location.reload(); // the session ended -> boot() shows the login screen
  try {
    await ensureSession({ showLogin });
  } catch { /* server unreachable: /api/me below reports it */ }

  let profile;
  try { profile = await api.get('/api/me'); } catch (e) {
    mount(app, h('div', { class: 'login' }, errorState(e.message, () => location.reload())));
    return;
  }

  const { main, links, shell } = buildShell(profile);
  const render = () => {
    const { path, query } = currentRoute();
    const entry = NAV.find((n) => n.path === path);
    links.forEach((a) => a.classList.toggle('active', a.dataset.path === (entry ? entry.path : null)));
    a11yCurrent(links, entry);
    shell.classList.remove('nav-open');
    document.title = `${entry ? entry.label : 'Not found'} · Job Application AI Agent`;
    main.replaceChildren();
    if (!entry) {
      mount(main, h('div', { class: 'state state-empty' }, h('strong', {}, 'Page not found'), h('a', { class: 'btn btn-primary', href: '/', 'data-link': true }, 'Back to the dashboard')));
    } else {
      entry.page(main, { query, profile });
    }
    main.focus({ preventScroll: true });
  };
  onRoute(render);
  render();
}

function a11yCurrent(links, entry) {
  links.forEach((a) => (a.dataset.path === entry?.path ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
}

boot();
