// ResumePreview: rendered resume + verification result + actions (download PDF/DOCX/MD,
// regenerate, edit, save to tracker). Resume text only ever enters the DOM via textContent.
import { h, mount } from '../dom.js';
import { api, download, pollTask } from '../api.js';
import { icon } from '../icons.js';
import { badge, toast, withBusy } from '../ui.js';
import { groupBullets, parseResume } from '../lib/resumeMarkdown.js';
import { atsValidation } from './atsValidation.js';

const KIND_LABEL = { generated: 'Generated', regenerated: 'Regenerated', edited: 'Edited', conservative: 'Reorder-only' };

export function resumePaper(markdown) {
  const nodes = groupBullets(parseResume(markdown)).map((b) => {
    switch (b.type) {
      case 'name': return h('h1', {}, b.text);
      case 'tagline': return h('p', { class: 'paper-tagline' }, b.text);
      case 'section': return h('h2', {}, b.text);
      case 'role': return h('h3', {}, b.text);
      case 'date': return h('p', { class: 'paper-date' }, b.text);
      case 'bullets': return h('ul', {}, ...b.items.map((t) => h('li', {}, t)));
      default: return h('p', {}, b.text);
    }
  });
  return h('article', { class: 'paper', 'aria-label': 'Resume preview' }, ...nodes);
}

function verification(v) {
  const val = v.validation;
  const icons = { pass: ['check', 'green'], warn: ['alert', 'orange'], fail: ['x', 'red'] };
  return h('div', { class: 'verify' },
    h('div', { class: 'verify-head' },
      val.ok ? badge('Verified: no fabrication detected', 'green') : badge('Blocked: failed verification', 'red'),
      v.kind && badge(KIND_LABEL[v.kind] || v.kind, v.kind === 'conservative' ? 'orange' : 'neutral')),
    val.notice && h('p', { class: 'notice', role: 'note' }, val.notice),
    val.problems.length > 0 && h('div', { class: 'problems', role: 'alert' },
      h('strong', {}, 'Why this version is blocked'),
      h('ul', {}, ...val.problems.map((p) => h('li', {}, p))),
      h('small', { class: 'muted' }, 'Fix these in Edit, or Regenerate. Blocked versions can’t be exported or saved to the tracker.')),
    val.warnings?.length > 0 && h('ul', { class: 'warnings' }, ...val.warnings.map((w) => h('li', {}, icon('alert', 14), w))),
    val.llm_problems?.length > 0 && h('details', {}, h('summary', {}, `What the model tried to add (${val.llm_problems.length})`),
      h('ul', {}, ...val.llm_problems.map((p) => h('li', {}, p)))),
    atsValidation(val.ats));
}

/**
 * state: { rec }. `onRecord(rec)` is called when the backend returns a changed record
 * (edit / export marker / tracker), `onRegenerate()` starts a regeneration task.
 */
export function resumePreview(initialRec, { onRegenerate, onRecord }) {
  const host = h('div', { class: 'preview' });
  const s = { rec: initialRec, n: initialRec.versions.at(-1).n, editing: false, draft: '', note: '' };
  const version = () => s.rec.versions.find((v) => v.n === s.n) || s.rec.versions.at(-1);

  const update = (rec, n) => { s.rec = rec; s.n = n ?? rec.versions.at(-1).n; s.editing = false; onRecord?.(rec); render(); };

  async function exportFile(fmt, btn) {
    const v = version();
    try {
      await withBusy(btn, async () => {
        if (!v.exports?.[fmt]) {
          const { task_id } = await api.post(`/api/resumes/${s.rec.id}/export`, { format: fmt, version: v.n });
          s.note = 'Building the document via Google Docs…';
          render();
          await pollTask(task_id).promise;
          s.rec = await api.get(`/api/resumes/${s.rec.id}`);
          s.note = '';
        }
        await download(`/api/resumes/${s.rec.id}/download?format=${fmt}&version=${v.n}`, `resume.${fmt}`);
      });
    } catch (e) { toast(e.message, 'error'); s.note = ''; }
    render();
  }

  async function saveToTracker(btn) {
    try {
      const r = await withBusy(btn, () => api.post(`/api/resumes/${s.rec.id}/save-to-tracker`, { version: version().n }));
      toast(r.result === 'added' ? 'Saved to the Job Tracker' : 'Already in the Job Tracker', 'success');
      update(await api.get(`/api/resumes/${s.rec.id}`), s.n);
    } catch (e) { toast(e.message, 'error'); }
  }

  async function saveEdit(btn) {
    try {
      const rec = await withBusy(btn, () => api.put(`/api/resumes/${s.rec.id}`, { markdown: s.draft }));
      toast(rec.versions.at(-1).validation.ok ? 'Saved as a new version' : 'Saved, but it fails verification — see details', rec.versions.at(-1).validation.ok ? 'success' : 'error');
      update(rec);
    } catch (e) { toast(e.message, 'error'); }
  }

  function render() {
    const v = version();
    const ok = v.validation.ok;
    const rec = s.rec;
    const pipelineScore = rec.job.pipeline_score;
    const score = pipelineScore ?? rec.match.fit_score;
    const trackerNote = rec.tracker && `Saved to the tracker (v${rec.tracker.version})`;

    mount(host,
      h('div', { class: 'preview-head' },
        h('h3', {}, 'Generated Resume'),
        rec.versions.length > 1 && h('label', { class: 'version-select' }, 'Version ',
          h('select', { 'aria-label': 'Resume version', onChange: (e) => { s.n = Number(e.target.value); s.editing = false; render(); } },
            ...rec.versions.map((x) => h('option', { value: x.n, selected: x.n === s.n }, `v${x.n} · ${KIND_LABEL[x.kind] || x.kind}`))))),
      verification(v),
      score < 8 && h('p', { class: 'notice notice-warn', role: 'note' },
        `Fit score ${score}/10 is below the pipeline’s 8+ cutoff. The automated pipeline would not have tailored this job — you’re overriding that manually.`),
      s.editing
        ? h('textarea', { class: 'editor', 'aria-label': 'Edit resume markdown', spellcheck: 'false', rows: 24, value: s.draft, onInput: (e) => { s.draft = e.target.value; } })
        : resumePaper(v.markdown),
      s.note && h('p', { class: 'muted', role: 'status' }, s.note),
      trackerNote && h('p', { class: 'muted' }, icon('check', 14), ' ', trackerNote),
      h('div', { class: 'preview-actions' },
        s.editing
          ? [h('button', { class: 'btn btn-primary', onClick: (e) => saveEdit(e.currentTarget) }, icon('check', 16), 'Save changes'),
             h('button', { class: 'btn btn-outline', onClick: () => { s.editing = false; render(); } }, 'Cancel')]
          : [h('button', { class: 'btn btn-primary', disabled: !ok, title: ok ? '' : 'Blocked until it passes verification', onClick: (e) => exportFile('pdf', e.currentTarget) }, icon('download', 16), 'Download PDF'),
             h('button', { class: 'btn btn-outline', disabled: !ok, onClick: (e) => exportFile('docx', e.currentTarget) }, icon('download', 16), 'Download DOCX'),
             h('button', { class: 'btn btn-outline', onClick: (e) => withBusy(e.currentTarget, () => download(`/api/resumes/${s.rec.id}/download?format=md&version=${v.n}`, 'resume.md')).catch((err) => toast(err.message, 'error')) }, 'Markdown'),
             h('button', { class: 'btn btn-outline', onClick: () => onRegenerate() }, icon('refresh', 16), 'Regenerate'),
             h('button', { class: 'btn btn-outline', onClick: () => { s.editing = true; s.draft = v.markdown; render(); } }, icon('edit', 16), 'Edit'),
             h('button', { class: 'btn btn-green', disabled: !ok, onClick: (e) => saveToTracker(e.currentTarget) }, icon('bookmark', 16), rec.tracker ? 'Saved — update' : 'Save to Tracker')]));
  }

  render();
  return host;
}
