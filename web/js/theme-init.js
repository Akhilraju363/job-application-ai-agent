// Runs synchronously in <head> (CSP forbids inline scripts) so the saved theme is applied
// before first paint. Keep in sync with theme.js.
(function () {
  var t = null;
  try { t = localStorage.getItem('jobagent.theme'); } catch (e) { /* blocked */ }
  if (t !== 'light' && t !== 'dark') {
    t = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', t);
})();
