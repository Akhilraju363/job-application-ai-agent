// Minimal History-API router. The server serves index.html for every extension-less path,
// so /tailor-resume, /applications?status=Applied etc. are real, reloadable URLs.
let listener = () => {};
// A page with unsaved edits registers a guard: async () => true to leave, false to stay.
// It is cleared on every successful navigation, so it only ever applies to the page that set it.
let leaveGuard = null;
let here = '';   // the URL currently rendered (to return to if a Back is cancelled)

export function setLeaveGuard(fn) { leaveGuard = fn || null; }

async function mayLeave() {
  if (!leaveGuard) return true;
  if (!(await leaveGuard())) return false;
  leaveGuard = null;
  return true;
}

export function currentRoute() {
  return { path: location.pathname.replace(/\/+$/, '') || '/', query: new URLSearchParams(location.search) };
}

export async function navigate(to, { replace = false } = {}) {
  const url = new URL(to, location.origin);
  if (!(await mayLeave())) return;
  if (url.pathname + url.search === location.pathname + location.search) { listener(); return; }
  history[replace ? 'replaceState' : 'pushState']({}, '', url.pathname + url.search);
  here = url.pathname + url.search;
  listener();
  window.scrollTo(0, 0);
}

export function onRoute(cb) {
  listener = cb;
  here = location.pathname + location.search;
  window.addEventListener('popstate', async () => {
    const there = location.pathname + location.search;
    if (leaveGuard) {
      history.pushState({}, '', here);   // stay put while the page asks
      if (!(await mayLeave())) return;
      history.pushState({}, '', there);
    }
    here = there;
    cb();
  });
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[data-link]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(a.getAttribute('href'));
  });
}
