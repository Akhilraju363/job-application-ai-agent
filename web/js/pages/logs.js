// "Logs": authenticated viewer over the backend's rotating application logs (GET /api/logs).
// Read-only; the backend enforces which files are readable and redacts secrets.
import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { badge, errorState, emptyState, skeleton, openDrawer } from '../ui.js';
import { LEVEL_FILTERS, ID_FIELDS, buildLogQuery, levelTone, formatLogTime, logIds, extraFields, pageLabel } from '../lib/logs.js';

const PAGE = 50;
const REFRESH_CHOICES = [['0', 'Off'], ['15', 'Every 15s'], ['30', 'Every 30s']];

export function logsPage(root) {
  const state = { level: 'All', component: '', date: '', request_id: '', task_id: '', resume_id: '', job_id: '', offset: 0 };
  let timer = null;
  let run = 0;
  const results = h('div', { class: 'card' });
  const pager = h('div', { class: 'toolbar' });

  const stopTimer = () => { if (timer) { clearInterval(timer); timer = null; } };

  async function load() {
    const mine = ++run;
    mount(results, skeleton('table'));
    try {
      const data = await api.get('/api/logs' + buildLogQuery({ ...state, limit: PAGE }));
      if (mine !== run) return;
      fillComponents(data.components || []);
      mount(results, render(data));
      mount(pager, ...pagerControls(data));
    } catch {
      if (mine !== run) return;
      mount(results, errorState('Unable to load application logs.', load));
      mount(pager);
    }
  }

  const levelTabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Filter by level' },
    ...LEVEL_FILTERS.map((lv) => h('button', {
      class: `tab${lv === state.level ? ' active' : ''}`, role: 'tab', 'aria-selected': lv === state.level ? 'true' : 'false',
      onClick: (e) => {
        state.level = lv; state.offset = 0;
        levelTabs.querySelectorAll('.tab').forEach((b) => { const on = b === e.currentTarget; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
        load();
      },
    }, lv === 'All' ? 'All' : lv[0] + lv.slice(1).toLowerCase())));

  const component = h('select', { class: 'input', 'aria-label': 'Component', onChange: (e) => { state.component = e.target.value; state.offset = 0; load(); } },
    h('option', { value: '' }, 'All components'));
  function fillComponents(names) {
    if (component.options.length > 1) return;
    for (const n of names) component.append(h('option', { value: n }, n));
  }
  const date = h('input', { class: 'input', type: 'date', 'aria-label': 'Date', onChange: (e) => { state.date = e.target.value; state.offset = 0; load(); } });
  const idInputs = ID_FIELDS.map(([key, label]) => h('input', {
    class: 'input', placeholder: label, 'aria-label': label, maxlength: 64, autocomplete: 'off',
    onKeydown: (e) => { if (e.key === 'Enter') e.target.blur(); },
    onChange: (e) => { state[key] = e.target.value.trim(); state.offset = 0; load(); },
  }));

  const refreshSel = h('select', { class: 'input', 'aria-label': 'Auto refresh', onChange: (e) => {
    stopTimer();
    const s = Number(e.target.value);
    if (s) timer = setInterval(() => { if (document.body.contains(results)) load(); else stopTimer(); }, s * 1000);
  } }, ...REFRESH_CHOICES.map(([v, l]) => h('option', { value: v }, l)));

  function render(data) {
    if (!data.entries.length) {
      return emptyState({ title: 'No log entries', text: 'Nothing matches these filters yet. Logs appear as the dashboard and pipeline run.', iconName: 'list' });
    }
    return h('div', { class: 'table-wrap' }, h('table', { class: 'table logs-table' },
      h('thead', {}, h('tr', {}, ...['Time', 'Level', 'Component', 'Message', 'IDs'].map((t) => h('th', {}, t)))),
      h('tbody', {}, ...data.entries.map((e) => h('tr', { class: 'log-row', tabindex: '0', onClick: () => detail(e), onKeydown: (ev) => { if (ev.key === 'Enter') detail(e); } },
        h('td', { class: 'log-time', title: e.timestamp }, formatLogTime(e.timestamp)),
        h('td', {}, badge(e.level, levelTone(e.level))),
        h('td', {}, e.component),
        h('td', { class: 'log-message' }, e.message),
        h('td', {}, h('div', { class: 'chips' }, ...logIds(e).map(([label, v]) => h('span', { class: 'chip chip-soft', title: label }, v)))))))));
  }

  function pagerControls(data) {
    const newer = h('button', { class: 'btn btn-outline btn-sm', disabled: state.offset === 0, onClick: () => { state.offset = Math.max(0, state.offset - PAGE); load(); } }, 'Newer');
    const older = h('button', { class: 'btn btn-outline btn-sm', disabled: !data.has_more, onClick: () => { state.offset += PAGE; load(); } }, 'Older');
    return [h('span', { class: 'muted' }, `Showing ${pageLabel({ offset: state.offset, count: data.entries.length })}`
      + (data.truncated ? ' · older entries not scanned' : '')), h('div', { class: 'row-actions' }, newer, older)];
  }

  function detail(e) {
    const kv = (label, value) => value ? h('div', { class: 'kv' }, h('small', { class: 'muted' }, label), h('div', { class: 'kv-value' }, value)) : null;
    const dlg = openDrawer(h('div', { class: 'drawer-body' }, h('div', {},
      h('div', { class: 'drawer-head' }, h('div', {}, h('h3', {}, 'Log entry'), badge(e.level, levelTone(e.level))),
        h('button', { class: 'btn-icon', 'aria-label': 'Close', onClick: () => dlg.close() }, icon('x', 18))),
      kv('Timestamp', e.timestamp), kv('Component', e.component), kv('Message', e.message),
      ...ID_FIELDS.map(([k, label]) => kv(label, e[k])),
      ...extraFields(e).map(([k, v]) => kv(k, v)),
      e.exception ? h('div', { class: 'kv' }, h('small', { class: 'muted' }, 'Exception / traceback'), h('pre', { class: 'log' }, e.exception)) : null)),
    { label: 'Log entry details' });
  }

  const refresh = h('button', { class: 'btn btn-outline', onClick: load }, icon('refresh', 16), 'Refresh');
  mount(root,
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Logs'), h('p', { class: 'muted' }, 'Application logs from the dashboard and pipeline. Secrets are never recorded.')),
      h('div', { class: 'row-actions' }, refreshSel, refresh)),
    h('div', { class: 'card logs-filters' }, levelTabs, h('div', { class: 'logs-filter-grid' }, component, date, ...idInputs)),
    results, pager);
  load();
  return stopTimer; // a page teardown hook, should the router ever call it
}
