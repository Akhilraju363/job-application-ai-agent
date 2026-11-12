import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { pageHeader, toast } from '../ui.js';

export function naukriPage(root) {
  const status = h('p', { class: 'muted', role: 'status' }, 'Checking Drive handoff…');
  const result = h('p', { class: 'muted small', role: 'status' });
  const error = h('p', { class: 'error-text', role: 'alert', hidden: true });
  const file = h('input', { class: 'input', type: 'file', accept: 'application/json,.json', 'aria-label': 'Naukri jobs JSON file' });
  const button = h('button', { class: 'btn btn-primary', type: 'button', disabled: true, onClick: async () => {
    error.hidden = true;
    try {
      const jobs = JSON.parse(await file.files[0].text());
      if (!Array.isArray(jobs)) throw new Error('The file must contain a JSON list of jobs.');
      const imported = await api.post('/api/naukri/import', { jobs });
      result.textContent = `${imported.job_count} jobs imported${imported.unchanged ? ' (file unchanged)' : ''}.`;
      toast('Naukri handoff imported', 'success');
      await refresh();
    } catch (e) {
      const records = e.details?.invalid_records || [];
      error.textContent = records.length
        ? `${e.message}: ${records.map((r) => `record ${r.record} missing ${r.missing.join(', ')}`).join('; ')}`
        : e.message;
      error.hidden = false;
    }
  } }, 'Validate and import');
  file.addEventListener('change', () => { button.disabled = !file.files?.length; result.textContent = ''; });
  async function refresh() {
    try {
      const data = await api.get('/api/naukri/status');
      status.textContent = `${data.label}${data.job_count ? ` · ${data.job_count} jobs` : ''}${data.last_successful_import ? ` · Last successful import: ${data.last_successful_import}` : ''}`;
    } catch (e) { status.textContent = e.message; }
  }
  mount(root, pageHeader({ title: 'Naukri handoff', subtitle: 'Import the JSON export from Auto_job_apply. The validated file is stored in your personal Google Drive for local and Modal runs.' }),
    h('section', { class: 'card' }, h('h3', {}, 'Drive handoff status'), status,
      h('p', { class: 'muted small' }, 'Expected export: exports/naukri_jobs.json. Each record needs title, company, link and description; optional fields are preserved.'),
      h('label', { class: 'field' }, h('span', {}, 'Naukri jobs JSON'), file), button, error, result));
  refresh();
}
