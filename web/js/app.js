// App shell: sidebar + header + router. Every page renders into <main>.
// Layout and behaviour: docs/ui/UI_ARCHITECTURE.md, UI_RESPONSIVE.md, UI_ACCESSIBILITY.md.
import { h, mount } from './dom.js';
import { api, auth } from './api.js';
import { ensureSession, signOut } from './lib/authClient.js';
import { loginView } from './components/loginForm.js';
import { icon } from './icons.js';
import { errorState, menu } from './ui.js';
import { currentRoute, onRoute } from './router.js';
import { theme } from './theme.js';
import { initials } from './lib/format.js';
import { dashboardPage } from './pages/dashboard.js';
import { findJobsPage } from './pages/findJobs.js';
import { tailorPage } from './pages/tailor.js';
import { masterResumePage } from './pages/masterResume.js';
import { applicationsPage, trackerBoardPage } from './pages/applications.js';
import { alertsPage } from './pages/alerts.js';
import { settingsPage } from './pages/settings.js';
import { logsPage } from './pages/logs.js';

const NAV = [
  { path: '/', label: 'Dashboard', icon: 'home', page: dashboardPage, group: 'Workspace' },
  { path: '/find-jobs', label: 'Find Jobs', icon: 'search', page: findJobsPage, group: 'Workspace' },
  { path: '/tailor-resume', label: 'Tailor Resume', icon: 'wand', page: tailorPage, group: 'Workspace' },
  { path: '/master-resume', label: 'Master Resume', icon: 'user', page: masterResumePage, group: 'Workspace' },
  { path: '/applications', label: 'Applications', icon: 'send', page: applicationsPage, group: 'Tracking' },
  { path: '/job-tracker', label: 'Job Tracker', icon: 'list', page: trackerBoardPage, group: 'Tracking' },
  { path: '/job-alerts', label: 'Job Alerts', icon: 'bell', page: alertsPage, group: 'Tracking' },
  { path: '/settings', label: 'Settings', icon: 'settings', page: settingsPage, group: 'System' },
  { path: '/logs', label: 'Logs', icon: 'terminal', page: logsPage, group: 'System' },
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

function navLinks() {
  const out = [];
  let group = null;
  for (const n of NAV) {
    if (n.group !== group) { group = n.group; out.push(h('div', { class: 'nav-group-label', 'aria-hidden': 'true' }, group)); }
    // title = the tooltip when the sidebar is collapsed to icons (tablet)
    out.push(h('a', { class: 'nav-link', href: n.path, 'data-link': true, 'data-path': n.path, title: n.label }, icon(n.icon, 18), h('span', {}, n.label)));
  }
  return out;
}

function buildShell(profile) {
  const items = navLinks();
  const links = items.filter((el) => el.dataset?.path);
  const main = h('main', { id: 'main', class: 'main', tabindex: '-1' });
  const context = h('div', { class: 'topbar-context' });
  const scrim = h('div', { class: 'scrim', onClick: () => closeNav() });
  const themeBtn = h('button', { class: 'btn-icon', 'aria-label': 'Toggle dark mode', title: 'Toggle theme', onClick: () => theme.toggle() });
  const syncTheme = () => mount(themeBtn, icon(theme.effective() === 'dark' ? 'sun' : 'moon', 18));
  syncTheme();
  window.addEventListener('themechange', syncTheme);

  const account = menu('Account', [
    { heading: profile.name || 'Your profile' },
    { label: 'Master Resume', icon: 'user', onClick: () => links.find((a) => a.dataset.path === '/master-resume')?.click() },
    { label: 'Settings', icon: 'settings', onClick: () => links.find((a) => a.dataset.path === '/settings')?.click() },
    ...(profile.auth_required ? [{ divider: true }, { label: 'Sign out', icon: 'logout', onClick: async () => { await signOut(); location.reload(); } }] : []),
  ]);
  account.querySelector('button')?.replaceChildren(h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(profile.name)));

  const menuBtn = h('button', { class: 'btn-icon menu-toggle', 'aria-label': 'Open navigation', 'aria-expanded': 'false', 'aria-controls': 'sidebar', onClick: () => (shell.classList.contains('nav-open') ? closeNav() : openNav()) }, icon('menu', 20));
  function openNav() { shell.classList.add('nav-open'); menuBtn.setAttribute('aria-expanded', 'true'); links[0]?.focus(); }
  function closeNav() { if (!shell.classList.contains('nav-open')) return; shell.classList.remove('nav-open'); menuBtn.setAttribute('aria-expanded', 'false'); }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && shell.classList.contains('nav-open')) { closeNav(); menuBtn.focus(); } });

  const shell = h('div', { class: 'shell' },
    h('a', { class: 'skip-link', href: '#main', onClick: (e) => { e.preventDefault(); main.focus(); } }, 'Skip to content'),
    h('aside', { class: 'sidebar', id: 'sidebar', 'aria-label': 'Primary' },
      h('a', { class: 'brand', href: '/', 'data-link': true }, h('span', { class: 'brand-mark' }, icon('briefcase', 18)), h('span', { class: 'brand-text' }, 'Job Application AI Agent')),
      h('nav', { class: 'nav', 'aria-label': 'Main' }, ...items),
      h('div', { class: 'sidebar-foot' },
        h('div', { class: 'profile' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(profile.name)),
          h('div', { class: 'profile-text' }, h('strong', {}, profile.name || 'Your profile'), h('small', {}, profile.email || (profile.auth_required ? 'Signed in' : 'Local mode')))),
        profile.auth_required && h('button', { class: 'signout', onClick: async () => { await signOut(); location.reload(); } }, icon('logout', 18), h('span', {}, 'Sign out')))),
    h('div', { class: 'main-col' },
      h('header', { class: 'topbar' },
        menuBtn, context,
        h('div', { class: 'topbar-right' },
          h('time', { class: 'topbar-date', datetime: new Date().toISOString().slice(0, 10) },
            new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })),
          themeBtn, account)),
      main),
    scrim);
  mount(app, shell);
  return { main, links, shell, context, closeNav };
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

  const { main, links, context, closeNav } = buildShell(profile);
  let firstRender = true;
  const render = () => {
    const { path, query } = currentRoute();
    const entry = NAV.find((n) => n.path === path);
    links.forEach((a) => a.classList.toggle('active', a.dataset.path === (entry ? entry.path : null)));
    a11yCurrent(links, entry);
    closeNav();
    mount(context, entry ? [h('span', {}, entry.group), icon('chevron', 14, 'crumb-sep'), h('strong', {}, entry.label)] : h('strong', {}, 'Not found'));
    document.title = `${entry ? entry.label : 'Not found'} · Job Application AI Agent`;
    main.replaceChildren();
    main.classList.remove('page-enter');
    void main.offsetWidth; // restart the fade for every navigation
    main.classList.add('page-enter');
    if (!entry) {
      mount(main, h('div', { class: 'state state-empty' }, h('strong', {}, 'Page not found'), h('a', { class: 'btn btn-primary', href: '/', 'data-link': true }, 'Back to the dashboard')));
    } else {
      entry.page(main, { query, profile });
    }
    // After a navigation, move focus to the new content; on first load leave it at the top of the
    // document so the first Tab reaches "Skip to content" and the sidebar.
    if (!firstRender) main.focus({ preventScroll: true });
    firstRender = false;
  };
  onRoute(render);
  render();
}

function a11yCurrent(links, entry) {
  links.forEach((a) => (a.dataset.path === entry?.path ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
}

boot();
