import { h, mount } from '../dom.js';
import { api, qs } from '../api.js';
import { icon } from '../icons.js';
import { confirmDialog, emptyState, loadable, openDrawer, pageHeader, statusBadge, toast, withBusy } from '../ui.js';

const STATUS = ['Scheduled', 'Completed', 'Rescheduled', 'Cancelled', 'No Show'];
const MODES = ['Online', 'Phone', 'In-person'];
const ROUNDS = ['Technical Round', 'Managerial Round', 'HR Round', 'Coding Round', 'System Design Round', 'Final Round', 'Other'];
const REMINDERS = [['', 'No reminder'], ['1440', '24 hours before'], ['60', '1 hour before'], ['30', '30 minutes before']];
const parseDateOnly = (value) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
};
const dateLabel = (value) => {
  const date = parseDateOnly(value);
  return date ? date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
};

function field(label, input, required = false) {
  return h('label', { class: 'field' }, h('span', {}, label, required && h('b', { class: 'required' }, ' *')), input);
}

function interviewForm({ initial = {}, onSaved, onCancel }) {
  const value = (key) => initial[key] || '';
  const company = h('input', { class: 'input', name: 'company', value: value('company'), maxlength: 200, required: true });
  const title = h('input', { class: 'input', name: 'job_title', value: value('job_title'), maxlength: 200, required: true });
  const day = h('input', { class: 'input', type: 'date', name: 'interview_date', value: value('interview_date'), required: true });
  const time = h('input', { class: 'input', type: 'time', name: 'interview_time', value: value('interview_time'), required: true });
  const savedRound = value('interview_round');
  const round = h('select', { class: 'input', name: 'interview_round', required: true },
    h('option', { value: '', selected: !savedRound }, 'Select interview round'),
    ...[...new Set([...ROUNDS, ...(savedRound && !ROUNDS.includes(savedRound) ? [savedRound] : [])])].map((x) =>
      h('option', { value: x, selected: x === savedRound }, x)));
  const applicationId = h('input', { class: 'input', name: 'application_id', value: value('application_id'), maxlength: 200 });
  const status = h('select', { class: 'input', name: 'status' }, ...STATUS.map((x) => h('option', { value: x, selected: x === (value('status') || 'Scheduled') }, x)));
  const outcome = h('select', { class: 'input', name: 'outcome' }, ...['Pending', 'Selected', 'Rejected', 'Next Round'].map((x) =>
    h('option', { value: x, selected: x === (value('outcome') || 'Pending') }, x)));
  const mode = h('select', { class: 'input', name: 'mode' }, ...MODES.map((x) => h('option', { value: x, selected: x === (value('mode') || 'Online') }, x)));
  const url = h('input', { class: 'input', type: 'url', name: 'meeting_url', value: value('meeting_url'), placeholder: 'https://...' });
  const location = h('input', { class: 'input', name: 'location', value: value('location') });
  const interviewer = h('input', { class: 'input', name: 'interviewer_name', value: value('interviewer_name') });
  const contact = h('input', { class: 'input', type: 'email', name: 'interviewer_contact', value: value('interviewer_contact') });
  const topics = h('textarea', { class: 'input', name: 'preparation_topics', rows: 3, value: value('preparation_topics') });
  const notes = h('textarea', { class: 'input', name: 'notes', rows: 3, value: value('notes') });
  const reminder = h('select', { class: 'input', name: 'reminder_minutes_before' }, ...REMINDERS.map(([x, label]) =>
    h('option', { value: x, selected: String(initial.reminder_minutes_before || '') === x }, label)));
  const error = h('p', { class: 'error-text', role: 'alert', hidden: true });
  const save = h('button', { class: 'btn btn-primary', type: 'submit' }, icon('check', 16), initial.id ? 'Save changes' : 'Save interview');
  const form = h('form', { class: 'drawer-body interview-form', onSubmit: async (e) => {
    e.preventDefault(); error.hidden = true;
    const body = Object.fromEntries(new FormData(form));
    body.reminder_enabled = body.reminder_minutes_before !== '';
    body.reminder_minutes_before = body.reminder_minutes_before ? Number(body.reminder_minutes_before) : null;
    try {
      await withBusy(save, () => initial.id ? api.patch(`/api/interviews/${initial.id}`, body) : api.post('/api/interviews', body));
      toast(initial.id ? 'Interview updated' : 'Interview saved', 'success');
      onSaved();
    } catch (ex) { error.textContent = ex.message; error.hidden = false; }
  } },
    h('div', { class: 'drawer-head' }, h('div', {}, h('h2', {}, initial.id ? 'Edit interview' : 'Add interview'),
      h('p', { class: 'muted' }, 'Interview data is stored separately from application status.')),
      h('button', { class: 'btn-icon', type: 'button', 'aria-label': 'Close', onClick: onCancel }, icon('x', 18))),
    h('div', { class: 'field-row' }, field('Company', company, true), field('Job title', title, true)),
    field('Associated application ID', applicationId),
    h('div', { class: 'field-row' }, field('Date', day, true), field('Time', time, true)),
    field('Interview round', round, true), initial.id && h('div', { class: 'field-row' }, field('Status', status), field('Outcome', outcome)),
    field('Mode', mode), field('Meeting URL', url), field('Location', location),
    h('div', { class: 'field-row' }, field('Interviewer name', interviewer), field('Interviewer email', contact)),
    field('Preparation topics', topics), field('Notes', notes), field('Reminder', reminder),
    h('p', { class: 'muted small' }, 'Reminder preferences are saved; delivery is not enabled by the current scheduler.'),
    error, h('div', { class: 'form-actions' }, h('button', { class: 'btn btn-outline', type: 'button', onClick: onCancel }, 'Cancel'), save));
  return form;
}

export function openInterviewForm(initial, reload) {
  let close;
  const panel = openDrawer(interviewForm({ initial, onSaved: () => { close(); reload(); }, onCancel: () => close() }),
    { label: initial?.id ? 'Edit interview' : 'Add interview' });
  close = panel.close;
}

function interviewCard(row, actions) {
  return h('article', { class: 'interview-item' },
    h('div', { class: 'interview-date' }, h('strong', {}, dateLabel(row.interview_date)), h('span', {}, row.interview_time || '—')),
    h('div', { class: 'interview-details' }, h('strong', {}, row.company), h('span', {}, row.job_title),
      h('small', {}, `${row.interview_round} · ${row.mode}`)),
    h('div', { class: 'interview-actions' }, statusBadge(row.status),
      h('div', { class: 'inline-actions' }, h('button', { class: 'btn btn-outline btn-sm', 'aria-label': `Edit ${row.company}`, onClick: () => actions.edit(row) }, icon('edit', 14), 'Edit'),
      h('button', { class: 'btn btn-danger btn-sm', 'aria-label': `Delete ${row.company}`, onClick: () => actions.remove(row) }, icon('trash', 14), 'Delete'))));
}

export function interviewsPage(root) {
  const host = h('div', {});
  const resultsHost = h('div', {});
  let statusFilter = '';
  let search = '';
  let loadedData = null;
  const actions = {
    edit: (row) => openInterviewForm(row, list.reload),
    remove: async (row) => { if (await confirmDialog({ title: 'Delete interview?',
      body: `Delete the interview for ${row.company} — ${row.job_title} on ${dateLabel(row.interview_date)}? This only deletes this interview record; applications and other interviews are not affected.`,
      confirmLabel: 'Delete', tone: 'red' })) {
      try { await api.delete(`/api/interviews/${row.id}`); toast('Interview deleted', 'success'); list.reload(); } catch (e) { toast(e.message, 'error'); }
    } },
  };
  const renderResults = (data) => {
    const unique = [...new Map(data.interviews.filter((r) => r?.id).map((r) => [r.id, r])).values()];
    const query = search.trim().toLowerCase();
    const rows = unique.filter((r) => !query || `${r.company} ${r.job_title}`.toLowerCase().includes(query));
    const listSection = h('section', { class: 'card' },
      h('div', { class: 'card-head-row' },
        h('strong', {}, 'All interviews'),
        h('span', { class: 'muted small' }, `${rows.length} total`)),
      rows.length
        ? h('div', { class: 'interview-list' }, ...rows.map((r) => interviewCard(r, actions)))
        : emptyState({ title: 'No interviews',
          text: search ? 'No interviews match your search.' : 'Add an interview to start tracking your conversations.',
          iconName: 'calendar' }));
    return listSection;
  };
  const list = loadable(resultsHost, {
    skeleton: 'lines',
    load: () => api.get(`/api/interviews${qs({ status: statusFilter })}`),
    isEmpty: () => false,
    render: (data) => { loadedData = data; return renderResults(data); },
  });
  const searchInput = h('input', { class: 'input', type: 'search', placeholder: 'Search company or role', 'aria-label': 'Search interviews',
    value: search, onInput: (e) => {
      search = e.target.value;
      if (loadedData) mount(resultsHost, renderResults(loadedData));
    } });
  const toolbar = h('div', { class: 'interview-toolbar' },
    searchInput,
    h('select', { class: 'input', 'aria-label': 'Interview status', onChange: (e) => { statusFilter = e.target.value; list.reload(); } },
      h('option', { value: '', selected: !statusFilter }, 'All statuses'),
      ...STATUS.map((s) => h('option', { value: s, selected: s === statusFilter }, s))));
  mount(root,
    pageHeader({ title: 'Interviews', subtitle: 'Schedule, prepare for and review interviews without changing application status.',
      actions: h('button', { class: 'btn btn-primary', onClick: () => openInterviewForm({}, list.reload) }, icon('plus', 16), 'Add interview') }),
    toolbar, resultsHost);
}
