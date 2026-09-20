// Single source of truth for theme state (Light / Dark / System), persisted in localStorage.
// web/js/theme-init.js applies the stored value before first paint to avoid a flash.
const KEY = 'jobagent.theme';
const mq = window.matchMedia?.('(prefers-color-scheme: dark)');

function apply() { document.documentElement.dataset.theme = theme.effective(); }

export const theme = {
  preference() { try { return localStorage.getItem(KEY) || 'system'; } catch { return 'system'; } },
  effective() {
    const p = this.preference();
    return p === 'light' || p === 'dark' ? p : (mq?.matches ? 'dark' : 'light');
  },
  set(p) {
    try { p === 'system' ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, p); } catch { /* storage blocked: still apply for this session */ }
    apply();
    window.dispatchEvent(new Event('themechange'));
  },
  toggle() { this.set(this.effective() === 'dark' ? 'light' : 'dark'); },
};

mq?.addEventListener?.('change', () => { if (theme.preference() === 'system') { apply(); window.dispatchEvent(new Event('themechange')); } });
apply();
