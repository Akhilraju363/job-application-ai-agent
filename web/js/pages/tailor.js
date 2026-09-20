// /tailor-resume -- the core workflow. Two entry points, one backend service:
//   A) manual JD  -> POST /api/tailor
//   B) scraped job (?job=<key>, from Find Jobs / the dashboard) -> POST /api/jobs/<key>/tailor
// Both start the same server-side task (tailoring_service.tailor); this page just shows the
// task's real stages and then the stored result.
import { h, mount } from '../dom.js';
import { api, pollTask } from '../api.js';
import { badge, emptyState, errorState, loadable, toast } from '../ui.js';
import { relDay } from '../lib/format.js';
import { stageList } from '../components/progress.js';
import { tailorForm } from '../components/tailorForm.js';
import { jdAnalysisPanel } from '../components/jdAnalysis.js';
import { resumePreview } from '../components/resumePreview.js';

const norm = (s) => (s || '').replace(/\r\n?/g, '\n').trim();

export function tailorPage(root, { query }) {
  const analysisHost = h('div', { class: 'card analysis-card', 'aria-live': 'polite' });
  const previewHost = h('section', { class: 'card preview-card' });
  const historyHost = h('div', {});
  const state = { jobKey: null, jobDescription: null, lastSubmit: null };

  const form = tailorForm({ onSubmit: (v) => generate(v) });

  const idle = () => mount(analysisHost, h('h3', {}, 'JD analysis'),
    emptyState({ title: 'Paste a job description to begin', text: 'You’ll see the match score, matching and missing skills, and what the resume will emphasise.', iconName: 'wand' }));
  const idlePreview = () => mount(previewHost, h('h3', {}, 'Generated Resume'),
    emptyState({ title: 'No resume generated yet', text: 'Only skills, employers and experience already in your master resume are used — nothing is invented.', iconName: 'file' }));

  function showProgress(stages) {
    mount(analysisHost, h('h3', {}, 'Processing'), stageList(stages),
      h('p', { class: 'muted small' }, 'Steps reflect what the server is actually doing. A local model can take a few minutes.'));
  }

  function showRecord(rec) {
    mount(analysisHost, jdAnalysisPanel(rec));
    mount(previewHost, resumePreview(rec, {
      onRegenerate: () => regenerate(rec.id),
      onRecord: (r) => mount(analysisHost, jdAnalysisPanel(r)),
    }));
    history.replaceState({}, '', `/tailor-resume?resume=${rec.id}`);
    historyList.reload();
  }

  async function runTask(taskId) {
    let stages = [];
    showProgress(stages);
    try {
      const done = await pollTask(taskId, (t) => { stages = t.stages; showProgress(stages); }).promise;
      const rec = await api.get(`/api/resumes/${done.result.resume_id}`);
      showRecord(rec);
      const v = rec.versions.at(-1);
      if (done.result.reused) {
        toast('Reused the existing resume for this job, JD and master resume. Use Regenerate for a new version.', 'info', 8000);
        return;
      }
      toast(v.validation.ok ? 'Tailored resume ready' : 'Generated, but blocked by verification — see details', v.validation.ok ? 'success' : 'error');
    } catch (e) {
      mount(analysisHost, h('h3', {}, 'JD analysis'), errorState(e.message, state.lastSubmit ? () => state.lastSubmit() : null));
    }
  }

  async function generate(v) {
    state.lastSubmit = () => generate(v);
    try {
      // Entry point B if the JD is still the scraped job's own text; otherwise it's a manual JD.
      const scraped = state.jobKey && norm(v.description) === state.jobDescription;
      const { task_id } = scraped
        ? await api.post(`/api/jobs/${state.jobKey}/tailor`, {})
        : await api.post('/api/tailor', { title: v.title, company: v.company, url: v.url, source: v.source, description: v.description });
      await runTask(task_id);
    } catch (e) {
      form.showError(e.message);
      toast(e.message, 'error');
    }
  }

  async function regenerate(id) {
    try {
      const { task_id } = await api.post(`/api/resumes/${id}/regenerate`, {});
      await runTask(task_id);
    } catch (e) { toast(e.message, 'error'); }
  }

  const historyList = loadable(historyHost, {
    load: () => api.get('/api/resumes'),
    isEmpty: (d) => d.resumes.length === 0,
    empty: { title: 'No resumes generated yet', iconName: 'file' },
    render: (d) => h('ul', { class: 'history' }, ...d.resumes.map((r) =>
      h('li', {}, h('button', { class: 'history-item', onClick: () => open(r.id) },
        h('span', {}, h('strong', {}, r.title), h('small', { class: 'muted' }, ` · ${r.company}`)),
        h('span', { class: 'history-meta' }, badge(`${r.overall}%`, r.overall >= 80 ? 'green' : r.overall >= 60 ? 'orange' : 'red'),
          !r.ok && badge('blocked', 'red'), h('small', { class: 'muted' }, relDay(r.created_at))))))),
  });

  async function open(id) {
    mount(analysisHost, h('h3', {}, 'JD analysis'), h('div', { class: 'sk-stack' }, h('div', { class: 'skeleton sk-line' }), h('div', { class: 'skeleton sk-line' })));
    try {
      const rec = await api.get(`/api/resumes/${id}`);
      form.set({ title: rec.job.title, company: rec.job.company, url: rec.job.link?.startsWith('manual:') ? '' : rec.job.link, source: rec.job.source, description: rec.job.description },
        'Loaded a saved resume. Editing the job description here and generating again creates a new resume.');
      showRecord(rec);
    } catch (e) { mount(analysisHost, h('h3', {}, 'JD analysis'), errorState(e.message, () => open(id))); }
  }

  mount(root,
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Tailor Resume'),
      h('p', { class: 'muted' }, 'Paste any job description and get a resume tailored to it — built only from your master resume.'))),
    h('div', { class: 'tailor-grid' }, form.el, analysisHost),
    previewHost,
    h('section', { class: 'card' }, h('div', { class: 'card-head' }, 'Recent resumes'), historyHost));

  idle(); idlePreview();

  const jobKey = query.get('job');
  const resumeId = query.get('resume');
  if (resumeId) open(resumeId);
  else if (jobKey) {
    api.get(`/api/jobs/${jobKey}`).then((j) => {
      state.jobKey = j.key; state.jobDescription = norm(j.description);
      form.set({ title: j.title, company: j.company, url: j.link, source: j.source, description: j.description },
        `Tailoring a scraped job (pipeline score ${j.score ?? 'n/a'}/10). This uses the same tailoring service as a pasted JD.`);
      if (!j.description) form.showError('This job has no scraped description — paste one to continue.');
    }).catch((e) => mount(analysisHost, h('h3', {}, 'JD analysis'), errorState(e.message)));
  }
}

