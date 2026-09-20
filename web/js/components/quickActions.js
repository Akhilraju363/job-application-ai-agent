import { h } from '../dom.js';
import { icon } from '../icons.js';
import { navigate } from '../router.js';
import { runPipelineFlow } from './pipelineRunner.js';

// Every action runs or opens a real workflow: Find New Jobs launches the existing
// scrape -> score scripts; the others open the page that hosts the workflow.
export function quickActions({ onJobsChanged }) {
  const action = (tone, iconName, title, sub, onClick) =>
    h('button', { class: `quick quick-${tone}`, onClick },
      h('span', { class: 'quick-icon' }, icon(iconName, 20)),
      h('span', { class: 'quick-text' }, h('strong', {}, title), h('small', {}, sub)),
      icon('arrow', 16, 'quick-arrow'));
  return h('div', { class: 'quick-list' },
    action('primary', 'search', 'Find New Jobs', 'Scrape latest job listings', () => runPipelineFlow('find', onJobsChanged)),
    action('blue', 'file', 'Tailor Resume', 'Generate JD-specific resume', () => navigate('/tailor-resume')),
    action('green', 'send', 'Track Applications', 'View and update job status', () => navigate('/applications')),
    action('purple', 'bell', 'Manage Job Alerts', 'Set your job preferences', () => navigate('/job-alerts')));
}
