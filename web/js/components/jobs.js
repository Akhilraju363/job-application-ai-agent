// LatestJobsTable + row actions + job detail drawer. Shared by the dashboard and Find Jobs.
import { h } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { badge, loadable, menu, openDrawer, toast } from '../ui.js';
import { navigate } from '../router.js';
import { matchTone, relDay, safeHref, statusTone } from '../lib/format.js';

export const TRACKER_STATUSES = ['Not Applied', 'Applied', 'Interviewing', 'Offer', 'Rejected'];

export const matchBadge = (pct) => (pct == null ? h('span', { class: 'muted' }, '—') : badge(`${pct}%`, matchTone(pct), 'badge-match'));

export async function saveJob(job, onChange) {
  try {
    const r = await api.post(`/api/jobs/${job.key}/save`, {});
    toast(r.result === 'added' ? 'Saved to your tracker' : 'Already in your tracker', 'success');
    onChange?.();
  } catch (e) { toast(e.message, 'error'); }
}

export async function setJobStatus(job, status, onChange) {
  try {
    if (!job.tracker_status) await api.post(`/api/jobs/${job.key}/save`, {});
    await api.patch('/api/tracker/status', { link: job.link, status });
    toast(`Marked ${status}`, 'success');
    onChange?.();
  } catch (e) { toast(e.message, 'error'); }
}

export function openJob(job) {
  const contents = h('div', { class: 'drawer-body' });
  const ov = openDrawer(contents, { label: `${job.title} at ${job.company}` });
  loadable(contents, {
    load: () => api.get(`/api/jobs/${job.key}`),
    render: (d) => jobDetail(d, ov.close),
  });
}

function chips(items, tone) {
  return items.length ? h('div', { class: 'chips' }, ...items.map((t) => h('span', { class: `chip chip-${tone}` }, t))) : h('span', { class: 'muted' }, 'None listed');
}

function jobDetail(j, close) {
  const href = safeHref(j.link);
  const resume = safeHref(j.resume_link);
  return h('div', {},
    h('div', { class: 'drawer-head' },
      h('div', {}, h('h2', {}, j.title), h('p', { class: 'muted' }, `${j.company}${j.location ? ' · ' + j.location : ''} · ${j.source}`)),
      h('button', { class: 'btn-icon', 'aria-label': 'Close', onClick: close }, icon('x', 18))),
    h('div', { class: 'detail-meta' }, matchBadge(j.match_pct), badge(j.state, statusTone(j.state)),
      j.score != null && h('span', { class: 'muted' }, `Pipeline score ${j.score}/10 (${j.qualified ? 'meets' : 'below'} the 8+ cutoff)`),
      h('span', { class: 'muted' }, `Posted ${relDay(j.posted_date || j.found_at) || 'unknown'}`)),
    j.reasoning && h('section', {}, h('h4', {}, 'Why this score'), h('p', {}, j.reasoning)),
    h('section', {}, h('h4', {}, 'Matched requirements'), chips(j.matched, 'green')),
    h('section', {}, h('h4', {}, 'Missing requirements'), chips(j.missing, 'red')),
    j.company_notes && h('section', {}, h('h4', {}, 'Company notes'), h('p', { class: 'prewrap' }, j.company_notes)),
    h('div', { class: 'detail-actions' },
      h('button', { class: 'btn btn-primary', onClick: () => { close(); navigate(`/tailor-resume?job=${j.key}`); } }, icon('wand', 16), 'Tailor Resume'),
      href && h('a', { class: 'btn btn-outline', href, target: '_blank', rel: 'noopener noreferrer' }, icon('ext', 16), 'Open job posting'),
      resume && h('a', { class: 'btn btn-outline', href: resume, target: '_blank', rel: 'noopener noreferrer' }, icon('file', 16), 'Pipeline resume')),
    h('section', {}, h('h4', {}, 'Job description'), j.description ? h('div', { class: 'jd-text prewrap' }, j.description) : h('p', { class: 'muted' }, 'No description was scraped.')));
}

export function jobActions(job, onChange) {
  const href = safeHref(job.link);
  return h('div', { class: 'row-actions' },
    h('button', { class: 'btn btn-soft btn-sm', onClick: () => openJob(job) }, 'View'),
    h('button', { class: 'btn btn-outline btn-sm', title: 'Generate a JD-specific resume for this job', onClick: () => navigate(`/tailor-resume?job=${job.key}`) }, 'Tailor'),
    menu(`More actions for ${job.title}`, [
      { label: 'Open job posting', icon: 'ext', href },
      { label: job.tracker_status ? 'In tracker' : 'Save to tracker', icon: 'bookmark', disabled: !!job.tracker_status, onClick: () => saveJob(job, onChange) },
      { divider: true }, { heading: 'Update status' },
      ...TRACKER_STATUSES.map((s) => ({ label: s + (job.tracker_status === s ? ' ✓' : ''), onClick: () => setJobStatus(job, s, onChange) })),
    ].filter((i) => i.href !== null)));
}

export function jobsTable(jobs, onChange) {
  const th = (t) => h('th', { scope: 'col' }, t);
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, ...['Job Title', 'Company', 'Match', 'Location', 'Posted', 'Source', 'Status', 'Action'].map(th))),
    h('tbody', {}, ...jobs.map((j) => h('tr', {},
      h('td', { class: 'cell-title' }, h('button', { class: 'link-btn', onClick: () => openJob(j) }, j.title || 'Untitled')),
      h('td', {}, j.company || '—'), h('td', {}, matchBadge(j.match_pct)), h('td', {}, j.location || '—'),
      h('td', { class: 'nowrap' }, relDay(j.posted_date || j.found_at) || '—'), h('td', {}, j.source),
      h('td', {}, badge(j.state, statusTone(j.state))), h('td', {}, jobActions(j, onChange)))))));
}

