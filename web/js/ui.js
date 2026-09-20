// Shared UI primitives: async sections with loading / error / empty states + retry,
// toasts, dialogs, drawer, dropdown menu, badges.
import { h, mount } from './dom.js';
import { icon } from './icons.js';

export const spinner = (cls = '') => h('span', { class: `spinner ${cls}`.trim(), role: 'status', 'aria-label': 'Loading' });

export function skeleton(kind = 'lines') {
  if (kind === 'chart') return h('div', { class: 'skeleton-chart' }, ...Array.from({ length: 5 }, () => h('div', { class: 'skeleton sk-bar' })));
  if (kind === 'table') return h('div', { class: 'sk-stack' }, ...Array.from({ length: 5 }, () => h('div', { class: 'skeleton sk-row' })));
  return h('div', { class: 'sk-stack' }, ...Array.from({ length: 3 }, () => h('div', { class: 'skeleton sk-line' })));
}

export function emptyState({ title, text, action, iconName = 'file' }) {
  return h('div', { class: 'state state-empty' },
    h('div', { class: 'state-icon' }, icon(iconName, 22)),
    h('strong', {}, title), text && h('p', {}, text), action || null);
}

export function errorState(message, onRetry) {
  return h('div', { class: 'state state-error', role: 'alert' },
    h('div', { class: 'state-icon' }, icon('alert', 22)),
    h('strong', {}, 'Something went wrong'), h('p', {}, message),
    onRetry && h('button', { class: 'btn btn-outline btn-sm', onClick: onRetry }, icon('refresh', 14), 'Retry'));
}

/**
 * Render an API-driven section into `host` with loading / error / empty states.
 *   load()        -> Promise<data>
 *   render(data)  -> Node
 *   isEmpty(data) -> bool         (optional)
 *   empty         -> {title, text, action}
 * Returns { reload }. A stale response (a newer reload started) is ignored.
 */
export function loadable(host, { load, render, isEmpty, empty, skeleton: sk = 'lines' }) {
  let run = 0;
  const reload = async () => {
    const mine = ++run;
    mount(host, skeleton(sk));
    host.setAttribute('aria-busy', 'true');
    try {
      const data = await load();
      if (mine !== run) return;
      mount(host, isEmpty && isEmpty(data) ? emptyState(empty || { title: 'Nothing here yet' }) : render(data, reload));
    } catch (e) {
      if (mine !== run) return;
      mount(host, errorState(e.message || 'Request failed', reload));
    } finally {
      if (mine === run) host.removeAttribute('aria-busy');
    }
  };
  reload();
  return { reload };
}

export function badge(text, tone = 'neutral', extra = '') {
  return h('span', { class: `badge badge-${tone} ${extra}`.trim() }, text);
}

// -- toasts ------------------------------------------------------------------
let toastHost;
export function toast(message, tone = 'info', ms = 4500) {
  if (!toastHost) {
    toastHost = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const el = h('div', { class: `toast toast-${tone}` }, message);
  toastHost.append(el);
  setTimeout(() => el.remove(), ms);
}

// -- overlays ----------------------------------------------------------------
function overlay(content, { side = false, label = '', onClose } = {}) {
  const prevFocus = document.activeElement;
  const panel = h('div', { class: side ? 'drawer' : 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': label, tabindex: '-1' }, content);
  const back = h('div', { class: 'overlay' }, panel);
  const close = () => {
    document.removeEventListener('keydown', onKey);
    back.remove();
    prevFocus?.focus?.();
    onClose?.();
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(back);
  panel.focus();
  return { close, panel };
}

export function openDrawer(content, opts) { return overlay(content, { ...opts, side: true }); }
export function openDialog(content, opts) { return overlay(content, opts); }

export function confirmDialog({ title, body, confirmLabel = 'Continue', tone = 'primary' }) {
  return new Promise((resolve) => {
    let ov;
    // resolve first: close() fires onClose, which resolves(false) for Escape/backdrop dismissal
    const done = (v) => { resolve(v); ov.close(); };
    ov = openDialog(h('div', { class: 'dialog-body' },
      h('h3', {}, title), h('p', { class: 'muted' }, body),
      h('div', { class: 'dialog-actions' },
        h('button', { class: 'btn btn-outline', onClick: () => done(false) }, 'Cancel'),
        h('button', { class: `btn btn-${tone}`, onClick: () => done(true) }, confirmLabel))),
    { label: title, onClose: () => resolve(false) });
  });
}

// -- dropdown menu -----------------------------------------------------------
let openMenu = null;
document.addEventListener('click', () => { openMenu?.(); });

export function menu(triggerLabel, items) {
  const list = h('div', { class: 'menu', role: 'menu', hidden: true });
  const btn = h('button', { class: 'btn-icon', 'aria-haspopup': 'menu', 'aria-label': triggerLabel, title: triggerLabel }, icon('more', 18));
  const close = () => { list.hidden = true; if (openMenu === close) openMenu = null; };
  for (const it of items) {
    if (it.divider) { list.append(h('div', { class: 'menu-divider' })); continue; }
    if (it.heading) { list.append(h('div', { class: 'menu-heading' }, it.heading)); continue; }
    const attrs = { role: 'menuitem', class: 'menu-item' };
    const kids = [it.icon && icon(it.icon, 15), it.label];
    if (it.href) list.append(h('a', { ...attrs, href: it.href, target: '_blank', rel: 'noopener noreferrer' }, ...kids));
    else list.append(h('button', { ...attrs, disabled: it.disabled, onClick: (e) => { e.stopPropagation(); close(); it.onClick?.(); } }, ...kids));
  }
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasHidden = list.hidden;
    openMenu?.();
    if (wasHidden) { list.hidden = false; openMenu = close; }
  });
  return h('div', { class: 'menu-wrap' }, btn, list);
}

export async function withBusy(button, fn) {
  button.disabled = true;
  const sp = spinner('spinner-inline');
  button.prepend(sp);
  try { return await fn(); } finally { sp.remove(); button.disabled = false; }
}
