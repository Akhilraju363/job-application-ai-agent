// TailorResumeForm: JD input. Client-side checks mirror the server's (which is the real
// gate); file upload is text-only and read in the browser.
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { toast, withBusy } from '../ui.js';

const MIN_JD = 80, MAX_JD = 30000, MAX_FILE = 200 * 1024;

export function tailorForm({ onSubmit }) {
  const field = (id, label, props = {}) => h('label', { class: 'field', for: id }, h('span', {}, label), h('input', { id, class: 'input', autocomplete: 'off', ...props }));
  const title = field('jd-title', 'Job Title', { placeholder: 'e.g. Full Stack Java Developer', maxlength: 160, required: true });
  const company = field('jd-company', 'Company', { placeholder: 'e.g. Acme Corp', maxlength: 160, required: true });
  const url = field('jd-url', 'Job URL (optional)', { type: 'url', placeholder: 'https://…', maxlength: 2000 });
  const source = h('label', { class: 'field', for: 'jd-source' }, h('span', {}, 'Job Source'),
    h('select', { id: 'jd-source', class: 'input' }, ...['LinkedIn', 'Naukri', 'Other'].map((o) => h('option', { value: o }, o))));
  const desc = h('textarea', { id: 'jd-desc', class: 'input textarea', rows: 14, placeholder: 'Paste the full job description here…', 'aria-describedby': 'jd-count' });
  const count = h('small', { id: 'jd-count', class: 'muted' }, '0 characters');
  const error = h('p', { class: 'error-text', role: 'alert', hidden: true });
  const banner = h('p', { class: 'notice', hidden: true });
  const file = h('input', { type: 'file', accept: '.txt,.md,text/plain,text/markdown', hidden: true, 'aria-label': 'Upload job description file' });
  const go = h('button', { class: 'btn btn-primary btn-lg', type: 'submit' }, icon('wand', 18), 'Generate Tailored Resume');

  const input = (el) => el.querySelector('input');
  const sourceSel = source.querySelector('select');
  const values = () => ({ title: input(title).value.trim(), company: input(company).value.trim(), url: input(url).value.trim(), source: sourceSel.value, description: desc.value.trim() });
  const refresh = () => { count.textContent = `${desc.value.length.toLocaleString()} / ${MAX_JD.toLocaleString()} characters`; };
  desc.addEventListener('input', refresh);
  const showError = (m) => { error.textContent = m || ''; error.hidden = !m; };

  function set(v, note) {
    input(title).value = v.title || ''; input(company).value = v.company || '';
    input(url).value = v.url || ''; desc.value = v.description || '';
    const wanted = v.source || 'Other';
    if (![...sourceSel.options].some((o) => o.value === wanted)) sourceSel.append(h('option', { value: wanted }, wanted));
    sourceSel.value = wanted;
    banner.textContent = note || ''; banner.hidden = !note;
    showError(''); refresh();
  }

  file.addEventListener('change', async () => {
    const f = file.files[0];
    file.value = '';
    if (!f) return;
    if (!/\.(txt|md)$/i.test(f.name)) return toast('Only .txt or .md job description files are supported', 'error');
    if (f.size > MAX_FILE) return toast('That file is too large (max 200 KB)', 'error');
    const text = await f.text();
    if (text.includes('\u0000')) return toast('That doesn’t look like a text file', 'error');
    desc.value = text; refresh(); toast(`Loaded ${f.name}`, 'success');
  });

  const form = h('form', { class: 'card tailor-form', novalidate: true, onSubmit: async (e) => {
    e.preventDefault();
    const v = values();
    if (!v.title || !v.company) return showError('Job title and company are required.');
    if (v.description.length < MIN_JD) return showError(`Paste the job description (at least ${MIN_JD} characters).`);
    if (v.description.length > MAX_JD) return showError(`That job description is too long (max ${MAX_JD.toLocaleString()} characters).`);
    if (v.url && !/^https?:\/\/\S+$/i.test(v.url)) return showError('Job URL must start with http:// or https://');
    showError('');
    await withBusy(go, () => onSubmit(v));
  } },
  h('h3', {}, 'Job description'), banner,
  h('div', { class: 'field-row' }, title, company), h('div', { class: 'field-row' }, url, source),
  h('label', { class: 'field', for: 'jd-desc' }, h('span', {}, 'Job Description'), desc, count),
  error,
  h('div', { class: 'form-actions' },
    h('button', { class: 'btn btn-outline', type: 'button', onClick: async () => {
      try { desc.value = await navigator.clipboard.readText(); refresh(); } catch { toast('Clipboard access was blocked — paste with Ctrl+V instead', 'error'); }
    } }, icon('clipboard', 16), 'Paste JD'),
    h('button', { class: 'btn btn-outline', type: 'button', onClick: () => file.click() }, icon('upload', 16), 'Upload JD'),
    h('button', { class: 'btn btn-outline', type: 'button', onClick: () => set({}) }, icon('trash', 16), 'Clear'),
    file, go));

  return { el: form, values, set, showError };
}
