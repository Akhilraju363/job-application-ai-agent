// Shared client-side pagination controls (Applications, Find Jobs). The slicing itself is
// paginate()/pageList() in lib/trackerView.js; styles are .pager / .page-btn / .page-size-control.
import { h } from '../dom.js';
import { PAGE_SIZES, pageList } from '../lib/trackerView.js';

export function pageSizeSelect({ value, onChange }) {
  const sel = h('select', { class: 'input input-sm page-size-select', 'aria-label': 'Rows per page', onChange: (e) => onChange(Number(e.target.value)) },
    ...PAGE_SIZES.map((n) => h('option', { value: String(n), selected: n === value }, String(n))));
  return h('label', { class: 'sort-control page-size-control' }, h('span', { class: 'sort-label', 'aria-hidden': 'true' }, 'Rows per page'), sel);
}

// Previous · 1 2 3 … 9 · Next. Native buttons: disabled ends are really disabled; the current page has aria-current.
export function pager({ page, pages }, go, label) {
  const nav = (key, text, target, disabled) => h('button', { class: 'btn btn-outline btn-sm', type: 'button', 'data-page-nav': key,
    disabled, onClick: () => go(target, key) }, text);
  return h('nav', { class: 'pager', 'aria-label': label },
    nav('prev', 'Previous', page - 1, page <= 1),
    h('div', { class: 'pager-pages' }, ...pageList(page, pages).map((n) => (n == null
      ? h('span', { class: 'pager-gap', 'aria-hidden': 'true' }, '…')
      : h('button', { class: `btn btn-sm page-btn ${n === page ? 'btn-primary' : 'btn-ghost'}`, type: 'button', 'data-page-nav': String(n),
        'aria-label': `Page ${n}`, 'aria-current': n === page ? 'page' : null, onClick: () => go(n, String(n)) }, String(n))))),
    nav('next', 'Next', page + 1, page >= pages));
}

// After a page button re-renders the table, keep keyboard focus on the same control (or the current page when it is now disabled).
export function refocusPager(host, focusKey) {
  const el = host.querySelector?.(`[data-page-nav="${focusKey}"]`);
  (el && !el.disabled ? el : host.querySelector?.('[aria-current="page"]'))?.focus?.();
}
