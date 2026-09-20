import { h, mount } from '../dom.js';
import { api, pollTask } from '../api.js';
import { confirmDialog, openDialog, toast } from '../ui.js';
import { navigate } from '../router.js';
import { stageList } from './progress.js';

const COPY = {
  find: {
    title: 'Find new jobs?',
    body: 'This runs the existing pipeline steps: scrape LinkedIn through Apify (a paid Apify call; skipped if '
      + 'jobs were scraped in the last 6 hours), then score each job against your resume with your LLM provider. '
      + 'Nothing is applied to.',
    label: 'Find New Jobs',
  },
  full: {
    title: 'Run the full pipeline?',
    body: 'Scrape, score, tailor every job scoring 8+, research the companies, and log them to your tracker Sheet '
      + '(the same steps as the daily Modal run). Resumes are uploaded to your Drive folder. Nothing is applied to.',
    label: 'Run pipeline',
  },
};

/** Confirm, start the real pipeline scripts server-side, and show their live progress. */
export async function runPipelineFlow(mode, onDone) {
  const c = COPY[mode];
  if (!(await confirmDialog({ title: c.title, body: c.body, confirmLabel: c.label }))) return;

  let start;
  try {
    start = await api.post('/api/actions/find-jobs', { mode });
  } catch (e) {
    toast(e.message, 'error');
    return;
  }

  const body = h('div', { class: 'dialog-body' });
  const ov = openDialog(body, { label: c.title });
  let poll;
  const render = (t) => {
    const finished = t.status === 'done' || t.status === 'error';
    mount(body,
      h('h3', {}, t.status === 'error' ? 'Run failed' : t.status === 'done' ? 'Run complete' : 'Running the pipeline…'),
      stageList(t.stages),
      t.status === 'error' && h('p', { class: 'error-text', role: 'alert' }, t.error?.message),
      t.logs.length > 0 && h('pre', { class: 'log', tabindex: '0', 'aria-label': 'Run log' }, t.logs.join('\n')),
      h('div', { class: 'dialog-actions' },
        h('button', { class: 'btn btn-outline', onClick: () => { poll.stop(); ov.close(); } },
          finished ? 'Close' : 'Hide (keeps running)'),
        t.status === 'done' && h('button', { class: 'btn btn-primary', onClick: () => { ov.close(); navigate('/find-jobs'); } }, 'View jobs')));
    const log = body.querySelector('.log');
    if (log) log.scrollTop = log.scrollHeight;
  };
  poll = pollTask(start.task_id, render, { interval: 1000 });
  poll.promise.then(() => { toast('Job search finished', 'success'); onDone?.(); }).catch(() => onDone?.());
}
