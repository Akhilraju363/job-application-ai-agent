// Minimal History-API router. The server serves index.html for every extension-less path,
// so /tailor-resume, /applications?status=Applied etc. are real, reloadable URLs.
let listener = () => {};

export function currentRoute() {
  return { path: location.pathname.replace(/\/+$/, '') || '/', query: new URLSearchParams(location.search) };
}

export function navigate(to, { replace = false } = {}) {
  const url = new URL(to, location.origin);
  if (url.pathname + url.search === location.pathname + location.search) { listener(); return; }
  history[replace ? 'replaceState' : 'pushState']({}, '', url.pathname + url.search);
  listener();
  window.scrollTo(0, 0);
}

export function onRoute(cb) {
  listener = cb;
  window.addEventListener('popstate', () => cb());
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[data-link]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(a.getAttribute('href'));
  });
}
