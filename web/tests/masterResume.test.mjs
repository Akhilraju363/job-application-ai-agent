// Master Resume page (web/js/pages/masterResume.js): section-by-section editing of
// resume/base_resume.md through GET/PUT /api/master-resume (see tests/test_master_resume_api.py
// for the server side). Zero dependencies:  node --test web/tests/masterResume.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { installFakeDom, forbidBrowserStorage } from './fakeDom.mjs';

installFakeDom();
forbidBrowserStorage();

const { masterResumePage, SAVED_MESSAGE, CONTACT_NOTE } = await import('../js/pages/masterResume.js');
const lib = await import('../js/lib/masterResume.js');

const CONTACT = 'name@example.com | +91 90000 00000 | linkedin.com/in/akhil-dalali-320204233 | Bengaluru, India';
const RESUME = {
  name: 'AKHIL DALALI', headline: 'Software Engineer | Java | Spring Boot | Angular | AWS',
  profile_lines: ['linkedin.com/in/akhil-dalali-320204233'],
  summary: 'Results-driven Software Engineer with 4+ years of experience.',
  skills: [{ label: 'Languages', items: 'Java (Core & Advanced), Python, TypeScript, SQL' },
    { label: 'Backend', items: 'Spring Boot, Hibernate, JPA' }, { label: 'Frontend', items: 'Angular, RxJS' }],
  experience: [
    { employer: 'Zyter Technologies India Private Limited', title: 'Software Engineer — Healthcare Domain (NextGen)', dates: 'Jan 2026 – Present', location: 'Bengaluru, India',
      bullets: ['Developed Angular web applications.', 'Built Python-based backend services.', 'Built reusable Angular components.'] },
    { employer: 'LTIMindtree', title: 'Software Engineer — Backend Development', dates: 'Jan 2022 – Jun 2024', location: 'Chennai, India',
      bullets: ['Designed RESTful APIs with Spring Boot.', 'Created database indexes.'] },
  ],
  education: [{ degree: 'Bachelor of Commerce in Computer Applications', institution: 'Sri Venkateswara University', dates: '2015 – 2018' }],
  certifications: ['AWS Certifications', 'Analyzing and Visualizing Data with Microsoft Power BI', 'Oracle Database for Developer'],
};
const PAYLOAD = (over = {}) => ({
  resume: JSON.parse(JSON.stringify(RESUME)), version: 'aaaaaaaaaaaa', updated_at: '2026-09-29T10:00:00Z',
  editable: true, read_only_reason: null, contact: { configured: true, line: CONTACT }, ...over,
});

const respond = (status, body = {}) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function fakeFetch(...responses) {
  const calls = [];
  const queue = [...responses];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined });
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return typeof next === 'function' ? next(url, init) : next;
  };
  return calls;
}

async function open(...responses) {
  const calls = fakeFetch(respond(200, PAYLOAD()), ...responses);
  const root = document.createElement('div');
  const page = masterResumePage(root);
  await page.ready;
  return { root, page, calls };
}

const all = (root, pred) => root.findAll(pred);
const byData = (root, attr, value) => all(root, (e) => e.attributes[attr] === value);
const fieldEl = (root, key) => byData(root, 'data-field', key)[0];
async function type(el, value) { el.value = value; await el.dispatch('input'); }
const text = (el) => el.textContent;
const lastBodyButton = (label) => document.body.findAll((e) => e.tagName === 'BUTTON' && text(e) === label).at(-1);
const bodyChoice = (choice) => document.body.findAll((e) => e.attributes['data-choice'] === choice).at(-1);
const lastToast = (tone) => document.body.findAll((e) => (e.attributes.class || '').includes(`toast-${tone}`)).at(-1);
const preview = (root) => all(root, (e) => (e.attributes.class || '').includes('paper-master'))[0];

// ---- navigation ----------------------------------------------------------------------------

test('Master Resume is in the sidebar navigation, right after Tailor Resume', () => {
  const src = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
  const nav = src.slice(src.indexOf('const NAV'), src.indexOf('];', src.indexOf('const NAV')));
  assert.match(nav, /\{ path: '\/master-resume', label: 'Master Resume', icon: '\w+', page: masterResumePage[,}]/);
  const labels = [...nav.matchAll(/label: '([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(labels, ['Dashboard', 'Find Jobs', 'Tailor Resume', 'Master Resume', 'Applications', 'Job Tracker',
    'Job Alerts', 'Settings', 'Logs'], 'sidebar order from docs/ui/UI_PAGES.md');
});

// ---- loading and structure ---------------------------------------------------------------

test('the page loads the master with its explanation and version metadata', async () => {
  const { root, calls } = await open();
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [['GET', '/api/master-resume']]);
  assert.equal(text(all(root, (e) => e.tagName === 'H1')[0]), 'Master Resume');
  assert.match(text(root), /single source of truth for your career information/);
  assert.equal(text(byData(root, 'data-meta', 'version')[0]), 'aaaaaaaaaaaa');
  assert.match(text(byData(root, 'data-meta', 'status')[0]), /All changes saved/);
  assert.match(text(byData(root, 'data-meta', 'validation')[0]), /Valid/);
});

test('every section is its own card, in master order', async () => {
  const { root } = await open();
  const cards = all(root, (e) => e.tagName === 'SECTION' && e.attributes['data-section']);
  assert.deepEqual(cards.map((c) => c.attributes['data-section']), ['header', 'summary', 'skills', 'experience', 'education', 'certifications']);
  assert.equal(fieldEl(root, 'header..name').value, 'AKHIL DALALI');
  assert.equal(fieldEl(root, 'header..headline').value, RESUME.headline);
});

test('the summary has its own multiline editor', async () => {
  const { root } = await open();
  const el = fieldEl(root, 'summary..summary');
  assert.equal(el.tagName, 'TEXTAREA');
  assert.equal(el.value, RESUME.summary);
});

test('each skill group is separate, with its own name and skills fields', async () => {
  const { root } = await open();
  const groups = byData(root, 'data-kind', 'skill-group');
  assert.equal(groups.length, 3);
  assert.deepEqual(RESUME.skills.map((_, i) => fieldEl(root, `skills.${i}.label`).value), ['Languages', 'Backend', 'Frontend']);
  assert.equal(fieldEl(root, 'skills.1.items').value, 'Spring Boot, Hibernate, JPA');
});

test('each employer is a separate card with employer, title, dates and location fields', async () => {
  const { root } = await open();
  assert.equal(byData(root, 'data-kind', 'employer').length, 2);
  for (const [name, want] of [['employer', 'LTIMindtree'], ['title', 'Software Engineer — Backend Development'], ['dates', 'Jan 2022 – Jun 2024'], ['location', 'Chennai, India']]) {
    assert.equal(fieldEl(root, `experience.1.${name}`).value, want);
  }
});

test('each experience bullet is a separate editable item', async () => {
  const { root } = await open();
  assert.equal(byData(root, 'data-kind', 'bullet').length, 5);
  assert.equal(fieldEl(root, 'experience.0.bullets.2').value, 'Built reusable Angular components.');
  assert.equal(fieldEl(root, 'experience.0.bullets.2').tagName, 'TEXTAREA');
});

test('bullets can be added, reordered and removed', async () => {
  const { root, page } = await open();
  const btn = (label) => all(root, (e) => e.attributes['aria-label'] === label)[0];
  await btn('Move bullet down').dispatch('click');   // first "down" belongs to bullet 1 of Zyter
  assert.deepEqual(page.state.draft.experience[0].bullets.slice(0, 2), ['Built Python-based backend services.', 'Developed Angular web applications.']);
  await btn('Remove bullet 3').dispatch('click');
  assert.equal(page.state.draft.experience[0].bullets.length, 2);
  await all(root, (e) => e.tagName === 'BUTTON' && text(e) === 'Add bullet')[0].dispatch('click');
  assert.equal(page.state.draft.experience[0].bullets.length, 3);
  assert.equal(byData(root, 'data-kind', 'bullet').length, 5);
});

test('education fields are separate', async () => {
  const { root } = await open();
  assert.equal(byData(root, 'data-kind', 'education').length, 1);
  assert.equal(fieldEl(root, 'education.0.degree').value, 'Bachelor of Commerce in Computer Applications');
  assert.equal(fieldEl(root, 'education.0.institution').value, 'Sri Venkateswara University');
  assert.equal(fieldEl(root, 'education.0.dates').value, '2015 – 2018');
});

test('each certification is a separate item', async () => {
  const { root } = await open();
  assert.equal(byData(root, 'data-kind', 'certification').length, 3);
  assert.equal(fieldEl(root, 'certifications.2.null').value, 'Oracle Database for Developer');
});

// ---- editing, preview, save ----------------------------------------------------------------

test('edits mark the section as edited and update the preview live', async () => {
  const { root } = await open();
  await type(fieldEl(root, 'summary..summary'), 'Detail-oriented engineer with Java experience.');
  assert.match(text(preview(root)), /Detail-oriented engineer with Java experience\./);
  assert.equal(byData(root, 'data-section', 'summary').find((e) => e.tagName === 'SECTION').attributes['data-edited'], 'true');
  assert.equal(byData(root, 'data-section', 'skills').find((e) => e.tagName === 'SECTION').attributes['data-edited'], 'false');
  assert.match(text(byData(root, 'data-meta', 'status')[0]), /Unsaved changes/);
});

test('the preview uses the export layout and shows the contact line without saving it', async () => {
  const { root } = await open();
  const p = preview(root);
  const headings = p.findAll((e) => e.tagName === 'H2').map(text);
  assert.deepEqual(headings, ['PROFESSIONAL SUMMARY', 'TECHNICAL SKILLS', 'PROFESSIONAL EXPERIENCE', 'EDUCATION', 'CERTIFICATIONS']);
  assert.equal(text(p.find((e) => e.attributes['data-preview'] === 'contact')), CONTACT);
  assert.match(text(p), new RegExp(CONTACT_NOTE.replace(/[.]/g, '\\.')));
  assert.equal(text(p.find((e) => e.tagName === 'STRONG')), 'Languages:');
});

test('a missing contact configuration is a visible warning in the preview', async () => {
  fakeFetch(respond(200, PAYLOAD({ contact: { configured: false, line: null } })));
  const root = document.createElement('div');
  await masterResumePage(root).ready;
  assert.match(text(preview(root)), /RESUME_CONTACT_LINE is not configured/);
});

test('Save asks for confirmation (impact warning), PUTs the sections and reports the new version', async () => {
  const saved = PAYLOAD({ version: 'bbbbbbbbbbbb', changed: true, previous_version: 'aaaaaaaaaaaa' });
  saved.resume.certifications.push('Test Certification');
  const { root, page, calls } = await open(respond(200, saved));
  await all(root, (e) => e.tagName === 'BUTTON' && text(e) === 'Add certification')[0].dispatch('click');
  await type(fieldEl(root, 'certifications.3.null'), 'Test Certification');
  const saving = page.save();
  await new Promise((r) => setTimeout(r, 0));
  assert.match(text(document.body.findAll((e) => e.tagName === 'P').at(-1)), /may cause existing jobs to be re-tailored/);
  await lastBodyButton('Save Master Resume').dispatch('click');
  assert.equal(await saving, true);
  const put = calls.find((c) => c.method === 'PUT');
  assert.equal(put.url, '/api/master-resume');
  assert.deepEqual(Object.keys(put.body).sort(), ['expected_version', 'resume']);
  assert.equal(put.body.expected_version, 'aaaaaaaaaaaa');
  assert.equal(put.body.resume.certifications.at(-1), 'Test Certification');
  assert.match(text(lastToast('success')), new RegExp(`${SAVED_MESSAGE.replace('.', '\\.')} Version bbbbbbbbbbbb`));
  assert.equal(text(byData(root, 'data-meta', 'version')[0]), 'bbbbbbbbbbbb');
  assert.match(text(byData(root, 'data-meta', 'status')[0]), /All changes saved/);
});

test('contact information is never sent to be written into the master', async () => {
  const { root, page, calls } = await open(respond(200, PAYLOAD({ changed: false })));
  await type(fieldEl(root, 'summary..summary'), `${RESUME.summary} Updated.`);
  const saving = page.save();
  await new Promise((r) => setTimeout(r, 0));
  await lastBodyButton('Save Master Resume').dispatch('click');
  await saving;
  const body = JSON.stringify(calls.find((c) => c.method === 'PUT').body);
  assert.ok(!body.includes('name@example.com') && !body.includes('90000 00000') && !body.includes('"contact"'), body);
});

test('client-side validation errors show next to the field and block the request', async () => {
  const { root, page, calls } = await open();
  await type(fieldEl(root, 'experience.1.title'), '');
  await type(fieldEl(root, 'certifications.0.null'), '');
  assert.equal(await page.save(), false);
  assert.equal(calls.filter((c) => c.method === 'PUT').length, 0);
  const errors = all(root, (e) => (e.attributes.class || '') === 'field-errors').map(text);
  assert.ok(errors.includes('Job title cannot be empty.'), errors);
  assert.ok(errors.includes('Certification cannot be empty.'), errors);
  assert.equal(fieldEl(root, 'experience.1.title').attributes['aria-invalid'], 'true');
  assert.match(text(byData(root, 'data-meta', 'validation')[0]), /2 validation issues/);
});

test('server validation errors are shown next to the relevant field', async () => {
  const details = [{ section: 'experience', index: 0, field: 'dates', message: 'Experience date is required.' }];
  const { root, page } = await open(respond(422, { error: { code: 'validation_failed', message: 'Fix the highlighted fields and save again.', details } }));
  await type(fieldEl(root, 'summary..summary'), 'Changed summary text.');
  const saving = page.save();
  await new Promise((r) => setTimeout(r, 0));
  await lastBodyButton('Save Master Resume').dispatch('click');
  assert.equal(await saving, false);
  assert.ok(all(root, (e) => (e.attributes.class || '') === 'field-errors').map(text).includes('Experience date is required.'));
  assert.equal(page.state.draft.summary, 'Changed summary text.', 'the edits are kept');
});

test('a contact detail typed into a field is rejected before saving', async () => {
  const { root, page, calls } = await open();
  await type(fieldEl(root, 'summary..summary'), 'Reach me at someone@example.com');
  assert.equal(await page.save(), false);
  assert.equal(calls.filter((c) => c.method === 'PUT').length, 0);
  assert.match(all(root, (e) => (e.attributes.class || '') === 'field-errors').map(text).join(' '), /Contact details/);
});

// ---- discard + unsaved changes ---------------------------------------------------------------

test('Discard Changes restores the last saved master', async () => {
  const { root, page } = await open();
  await type(fieldEl(root, 'header..headline'), 'Java Developer');
  page.discard();
  assert.equal(fieldEl(root, 'header..headline').value, RESUME.headline);
  assert.deepEqual(page.state.draft, page.state.saved);
  assert.match(text(byData(root, 'data-meta', 'status')[0]), /All changes saved/);
});

test('leaving with unsaved edits asks: Cancel stays, Discard leaves', async () => {
  const { root, page } = await open();
  assert.equal(await page.leaveGuard(), true, 'nothing edited -> leave freely');
  await type(fieldEl(root, 'skills.0.items'), 'Java, Python');
  let asking = page.leaveGuard();
  await new Promise((r) => setTimeout(r, 0));
  assert.match(text(document.body.findAll((e) => e.tagName === 'P').at(-1)), /Technical Skills/);
  await bodyChoice('cancel').dispatch('click');
  assert.equal(await asking, false);
  assert.equal(page.state.draft.skills[0].items, 'Java, Python', 'Cancel keeps the edit');
  asking = page.leaveGuard();
  await new Promise((r) => setTimeout(r, 0));
  await bodyChoice('discard').dispatch('click');
  assert.equal(await asking, true);
  assert.deepEqual(page.state.draft, page.state.saved);
});

test('leaving with unsaved edits can Save Changes first', async () => {
  const { root, page, calls } = await open(respond(200, PAYLOAD({ version: 'cccccccccccc', changed: true })));
  await type(fieldEl(root, 'education.0.dates'), '2015 – 2019');
  const asking = page.leaveGuard();
  await new Promise((r) => setTimeout(r, 0));
  await bodyChoice('save').dispatch('click');
  assert.equal(await asking, true);
  assert.equal(calls.filter((c) => c.method === 'PUT').length, 1);
});

test('the hosted (read-only) dashboard shows why and disables saving', async () => {
  fakeFetch(respond(200, PAYLOAD({ editable: false, read_only_reason: 'The hosted dashboard can’t save the master resume.' })));
  const root = document.createElement('div');
  await masterResumePage(root).ready;
  assert.match(text(root), /hosted dashboard can’t save/);
  assert.equal(all(root, (e) => e.attributes['data-action'] === 'save')[0].disabled, true);
  assert.equal(fieldEl(root, 'summary..summary').disabled, true);
});

// ---- pure helpers -----------------------------------------------------------------------------

test('helpers: changed sections, move, contact detection', () => {
  const a = JSON.parse(JSON.stringify(RESUME));
  const b = JSON.parse(JSON.stringify(RESUME));
  b.certifications.push('X');
  assert.deepEqual(lib.changedSections(b, a), ['certifications']);
  assert.deepEqual(lib.move([1, 2, 3], 0, 1), [2, 1, 3]);
  assert.deepEqual(lib.move([1, 2, 3], 0, -1), [1, 2, 3]);
  assert.equal(lib.looksLikeContact('2015 - 2018'), false);
  assert.equal(lib.looksLikeContact('+91 98765 43210'), true);
  assert.deepEqual(lib.validate(a), []);
});
