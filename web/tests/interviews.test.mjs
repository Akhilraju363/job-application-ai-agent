import test from 'node:test';
import assert from 'node:assert/strict';
import { installFakeDom, FakeElement } from './fakeDom.mjs';

installFakeDom();
const { interviewsPage } = await import('../js/pages/interviews.js');

test('Interviews: every interview row exposes visible Edit and Delete buttons', async () => {
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      interviews: [{
        id: 'abcdef1234567890', company: 'Acme', job_title: 'Java Engineer',
        interview_date: '2026-10-23', interview_time: '10:00',
        interview_round: 'Technical', mode: 'Online', status: 'Scheduled',
      }],
    }),
  });
  const root = new FakeElement('div');
  interviewsPage(root);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const buttons = root.findAll((e) => e.tagName === 'BUTTON').map((e) => e.textContent);
  assert.ok(buttons.includes('Edit'));
  assert.ok(buttons.includes('Delete'));
  assert.equal(root.find((e) => e.classList.contains('calendar-grid')), null);
  assert.equal(root.find((e) => e.classList.contains('calendar-card')), null);
});

test('Interviews: legacy rounds remain visible and Other has no custom-name field', async () => {
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      interviews: [{
        id: 'legacy1234567890', company: 'Legacy Co', job_title: 'Engineer',
        interview_date: '2026-10-24', interview_time: '11:00',
        interview_round: 'Panel Round', mode: 'Online', status: 'Scheduled',
      }],
    }),
  });
  const root = new FakeElement('div');
  interviewsPage(root);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(root.textContent, /Panel Round/);
  const add = root.find((e) => e.tagName === 'BUTTON' && e.textContent.includes('Add interview'));
  await add.dispatch('click');
  const custom = document.body.find((e) => e.getAttribute('name') === 'custom_interview_round');
  assert.equal(custom, null);
});

test('Interviews: search filters and clears results without replacing the input', async () => {
  const rows = [
    { id: 'abcdef1234567890', company: 'Acme', job_title: 'Java Engineer', interview_date: '2026-10-23',
      interview_time: '10:00', interview_round: 'Technical', mode: 'Online', status: 'Scheduled' },
    { id: 'abcdef1234567891', company: 'Beta', job_title: 'Angular Engineer', interview_date: '2026-10-24',
      interview_time: '11:00', interview_round: 'HR', mode: 'Online', status: 'Completed' },
  ];
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({ interviews: url.includes('status=Completed') ? [rows[1]] : rows }),
    };
  };
  const root = new FakeElement('div');
  interviewsPage(root);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const input = root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search');
  assert.ok(input);
  assert.match(root.textContent, /Acme/);
  assert.match(root.textContent, /Beta/);

  input.value = 'beta';
  await input.dispatch('input');
  assert.equal(root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search'), input);
  assert.match(root.textContent, /Beta/);
  assert.doesNotMatch(root.textContent, /Acme/);

  input.value = 'no match';
  await input.dispatch('input');
  assert.equal(root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search'), input);
  assert.match(root.textContent, /No interviews match your search/);

  input.value = '';
  await input.dispatch('input');
  assert.equal(root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search'), input);
  assert.match(root.textContent, /Acme/);
  assert.match(root.textContent, /Beta/);

  const status = root.find((e) => e.tagName === 'SELECT' && e.getAttribute('aria-label') === 'Interview status');
  status.value = 'Completed';
  await status.dispatch('change');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(requests.some((url) => url.includes('status=Completed')));
  assert.equal(root.find((e) => e.tagName === 'INPUT' && e.attributes.type === 'search'), input);
  assert.match(root.textContent, /Beta/);
  assert.doesNotMatch(root.textContent, /Acme/);
});
