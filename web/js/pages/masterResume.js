// Master Resume: view and edit resume/base_resume.md -- the single source of truth for every
// tailored resume -- one section at a time. Edits stay in memory until an explicit Save; the
// server validates, serialises back to the canonical Markdown and writes only that file.
// Resume text only ever enters the DOM via textContent / input values (see dom.js).
import { h, mount } from '../dom.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { badge, confirmDialog, errorState, openDialog, pageHeader, skeleton, toast, withBusy } from '../ui.js';
import { setLeaveGuard } from '../router.js';
import {
  PREVIEW_TITLES, SECTIONS, blank, changedSections, clone, errorsFor, isDirty, move, previewContact, validate,
} from '../lib/masterResume.js';

export const SAVED_MESSAGE = 'Master resume saved successfully.';
export const IMPACT_WARNING = 'Changing the master resume can affect future tailored resumes and may cause existing '
  + 'jobs to be re-tailored when the master version changes.';
export const CONTACT_NOTE = 'Contact information is managed separately and is not stored in the public master resume.';

const fmtTime = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };

export function masterResumePage(root) {
  const s = { data: null, saved: null, draft: null, errors: [], status: 'saved', showPreview: true };
  const editorHost = h('div', { class: 'master-editor' });
  const previewHost = h('div', { class: 'master-preview-body' });
  const metaHost = h('div', { class: 'master-meta' });
  const navHost = h('nav', { class: 'master-nav', 'aria-label': 'Master resume sections' });
  const cards = {};

  const readOnly = () => !s.data?.editable;
  const dirty = () => isDirty(s.draft, s.saved);

  // ---- leaving with unsaved edits -------------------------------------------------------
  const beforeUnload = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } };
  if (typeof window !== 'undefined') window.addEventListener('beforeunload', beforeUnload);
  const leaveGuard = async () => {
    if (!dirty()) return done();
    const choice = await unsavedDialog();
    if (choice === 'save') return (await save({ confirm: false })) ? done() : false;
    if (choice === 'discard') { discard(); return done(); }
    return false;
  };
  function done() {
    if (typeof window !== 'undefined') window.removeEventListener('beforeunload', beforeUnload);
    return true;
  }
  setLeaveGuard(leaveGuard);

  function unsavedDialog() {
    return new Promise((resolve) => {
      let ov;
      const pick = (v) => { resolve(v); ov.close(); };
      ov = openDialog(h('div', { class: 'dialog-body' },
        h('h3', {}, 'Unsaved changes'),
        h('p', { class: 'muted' }, `You have unsaved edits in: ${changedSections(s.draft, s.saved).map(titleOf).join(', ')}.`),
        h('div', { class: 'dialog-actions' },
          h('button', { class: 'btn btn-outline', 'data-choice': 'cancel', onClick: () => pick('cancel') }, 'Cancel'),
          h('button', { class: 'btn btn-outline', 'data-choice': 'discard', onClick: () => pick('discard') }, 'Discard Changes'),
          h('button', { class: 'btn btn-primary', 'data-choice': 'save', disabled: readOnly(), onClick: () => pick('save') }, 'Save Changes'))),
      { label: 'Unsaved changes', onClose: () => resolve('cancel') });
    });
  }

  // ---- state changes -------------------------------------------------------------------
  const titleOf = (key) => SECTIONS.find((x) => x.key === key)?.title || key;

  function edited() {                     // a text field changed: no re-render (keeps focus)
    s.status = dirty() ? 'unsaved' : 'saved';
    refresh();
  }
  function restructure(fn) {              // add / remove / reorder: rebuild the editor
    fn(s.draft);
    s.errors = s.errors.length ? validate(s.draft) : [];
    edited();
    renderEditor();
  }
  function discard() {
    s.draft = clone(s.saved);
    s.errors = [];
    s.status = 'saved';
    renderEditor();
    refresh();
  }

  async function save({ confirm = true } = {}) {
    if (readOnly()) { toast(s.data.read_only_reason, 'error', 9000); return false; }
    s.errors = validate(s.draft);
    if (s.errors.length) {
      s.status = 'invalid';
      renderEditor();
      refresh();
      showFirstError();
      toast('Fix the highlighted fields and save again.', 'error');
      return false;
    }
    if (confirm && !(await confirmDialog({
      title: 'Save the master resume?',
      body: `${IMPACT_WARNING} The master resume is the source of truth for every tailored resume.`,
      confirmLabel: 'Save Master Resume',
    }))) return false;
    s.status = 'saving';
    refresh();
    try {
      const out = await api.put('/api/master-resume', { resume: s.draft, expected_version: s.data.version });
      load(out);
      toast(out.changed ? `${SAVED_MESSAGE} Version ${out.version}.` : 'No changes to save.', 'success');
      return true;
    } catch (e) {
      s.errors = e.code === 'validation_failed' && Array.isArray(e.details) ? e.details : [];
      s.status = s.errors.length ? 'invalid' : (dirty() ? 'unsaved' : 'saved');
      renderEditor();
      refresh();
      showFirstError();
      toast(e.message, 'error', 9000);
      return false;
    }
  }

  function showFirstError() {
    const first = editorHost.querySelector?.('[aria-invalid="true"], .field-errors');
    first?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    if (first?.tagName === 'INPUT' || first?.tagName === 'TEXTAREA') first.focus?.({ preventScroll: true });
  }

  function load(data) {
    s.data = data;
    s.saved = clone(data.resume);
    s.draft = clone(data.resume);
    s.errors = [];
    s.status = 'saved';
    renderEditor();
    refresh();
  }

  // ---- fields --------------------------------------------------------------------------
  const errList = (msgs) => msgs.length > 0 && h('ul', { class: 'field-errors', role: 'alert' }, ...msgs.map((m) => h('li', {}, m)));

  function field(label, get, set, { section, index = null, name, multiline = false, rows = 3, hint } = {}) {
    const msgs = errorsFor(s.errors, section, index, name);
    const props = {
      class: `input${multiline ? ' textarea textarea-auto' : ''}${msgs.length ? ' input-error' : ''}`,
      value: get(), disabled: readOnly(), 'aria-invalid': msgs.length ? 'true' : null, 'data-field': `${section}.${index ?? ''}.${name}`,
      onInput: (e) => { set(e.target.value); edited(); },
    };
    const input = multiline ? h('textarea', { ...props, rows }) : h('input', { ...props, type: 'text' });
    return h('label', { class: 'field' }, h('span', {}, label), hint && h('small', { class: 'muted' }, hint), input, errList(msgs));
  }

  const iconBtn = (name, label, onClick, disabled = false) => h('button', {
    type: 'button', class: 'btn-icon', 'aria-label': label, title: label, disabled: disabled || readOnly(), onClick,
  }, icon(name, 16));
  const moveBtns = (list, i, setList, what) => [
    iconBtn('up', `Move ${what} up`, () => restructure((d) => setList(d, move(list(d), i, -1))), i === 0),
    iconBtn('chevron', `Move ${what} down`, () => restructure((d) => setList(d, move(list(d), i, 1))), i === list(s.draft).length - 1),
  ];
  const addBtn = (label, onClick) => h('button', { type: 'button', class: 'btn btn-outline btn-sm', disabled: readOnly(), onClick }, icon('plus', 14), label);

  // ---- section cards -------------------------------------------------------------------
  function card(key, ...body) {
    const msgs = errorsFor(s.errors, key);
    const mark = h('span', { class: 'edited-badge', hidden: true }, badge('Edited', 'orange'));
    const el = h('section', { class: 'card master-card', id: `master-${key}`, 'data-section': key, 'aria-labelledby': `master-${key}-title` },
      h('div', { class: 'master-card-head' },
        h('h2', { id: `master-${key}-title` }, titleOf(key)), mark),
      errList(msgs), ...body);
    cards[key] = { el, mark };
    return el;
  }

  function headerCard() {
    const d = s.draft;
    return card('header',
      field('Name', () => d.name, (v) => { d.name = v; }, { section: 'header', name: 'name' }),
      field('Headline', () => d.headline, (v) => { d.headline = v; }, { section: 'header', name: 'headline',
        hint: 'The same on every tailored resume -- a job description never changes it; the job role only names the exported file.' }),
      d.profile_lines?.length > 0 && h('div', { class: 'field' }, h('span', {}, 'Public profile line'),
        h('p', { class: 'master-readonly' }, d.profile_lines.join(' | ')),
        h('small', { class: 'muted' }, 'Kept as stored. Email and phone come from RESUME_CONTACT_LINE at export.')));
  }

  function summaryCard() {
    const d = s.draft;
    return card('summary', field('Summary', () => d.summary, (v) => { d.summary = v; }, { section: 'summary', name: 'summary', multiline: true, rows: 7 }));
  }

  function skillsCard() {
    const list = (d) => d.skills;
    const setList = (d, v) => { d.skills = v; };
    return card('skills',
      h('ol', { class: 'master-list' }, ...s.draft.skills.map((g, i) => h('li', { class: 'master-item', 'data-kind': 'skill-group' },
        h('div', { class: 'master-item-row' },
          field('Group', () => g.label, (v) => { g.label = v; }, { section: 'skills', index: i, name: 'label' }),
          h('div', { class: 'master-item-actions' }, ...moveBtns(list, i, setList, 'skill group'),
            iconBtn('trash', `Remove skill group ${g.label || i + 1}`, () => restructure((d) => d.skills.splice(i, 1))))),
        field('Skills', () => g.items, (v) => { g.items = v; }, { section: 'skills', index: i, name: 'items', multiline: true, rows: 2, hint: 'Comma-separated, one group per card.' }),
        errList(errorsFor(s.errors, 'skills', i))))),
      addBtn('Add skill group', () => restructure((d) => d.skills.push(blank.skill()))));
  }

  function roleCard(e, i) {
    const bullets = (d) => d.experience[i].bullets;
    const setBullets = (d, v) => { d.experience[i].bullets = v; };
    const roles = (d) => d.experience;
    const setRoles = (d, v) => { d.experience = v; };
    const f = (label, name) => field(label, () => e[name], (v) => { e[name] = v; }, { section: 'experience', index: i, name });
    return h('article', { class: 'master-role', 'data-kind': 'employer', 'aria-label': e.employer || `Employer ${i + 1}` },
      h('div', { class: 'master-item-row master-role-head' },
        h('h3', {}, e.employer || `Employer ${i + 1}`),
        h('div', { class: 'master-item-actions' }, ...moveBtns(roles, i, setRoles, 'employer'),
          iconBtn('trash', `Remove employer ${e.employer || i + 1}`, async () => {
            if (await confirmDialog({ title: 'Remove this employer?', body: `${e.employer || 'This employer'} and its bullets will be removed from the draft. Nothing is saved until you click Save Master Resume.`, confirmLabel: 'Remove', tone: 'primary' })) {
              restructure((d) => d.experience.splice(i, 1));
            }
          }))),
      f('Employer', 'employer'), f('Job title', 'title'),
      h('div', { class: 'field-row' }, f('Dates', 'dates'), f('Location', 'location')),
      h('div', { class: 'field' }, h('span', {}, 'Bullets'),
        h('ol', { class: 'master-list master-bullets' }, ...e.bullets.map((b, j) => h('li', { class: 'master-item master-bullet', 'data-kind': 'bullet' },
          h('div', { class: 'master-item-row' },
            field(`Bullet ${j + 1}`, () => e.bullets[j], (v) => { e.bullets[j] = v; }, { section: 'experience', index: i, name: `bullets.${j}`, multiline: true, rows: 2 }),
            h('div', { class: 'master-item-actions' }, ...moveBtns(bullets, j, setBullets, 'bullet'),
              iconBtn('trash', `Remove bullet ${j + 1}`, () => restructure((d) => d.experience[i].bullets.splice(j, 1)))))))),
        errList(errorsFor(s.errors, 'experience', i, 'bullets')),
        addBtn('Add bullet', () => restructure((d) => d.experience[i].bullets.push('')))),
      errList(errorsFor(s.errors, 'experience', i)));
  }

  function experienceCard() {
    return card('experience', ...s.draft.experience.map(roleCard),
      addBtn('Add employer', () => restructure((d) => d.experience.push(blank.role()))));
  }

  function educationCard() {
    const list = (d) => d.education;
    const setList = (d, v) => { d.education = v; };
    return card('education',
      h('ol', { class: 'master-list' }, ...s.draft.education.map((ed, i) => h('li', { class: 'master-item', 'data-kind': 'education' },
        h('div', { class: 'master-item-row' },
          field('Degree', () => ed.degree, (v) => { ed.degree = v; }, { section: 'education', index: i, name: 'degree' }),
          h('div', { class: 'master-item-actions' }, ...moveBtns(list, i, setList, 'education entry'),
            iconBtn('trash', `Remove education entry ${i + 1}`, () => restructure((d) => d.education.splice(i, 1))))),
        h('div', { class: 'field-row' },
          field('Institution', () => ed.institution, (v) => { ed.institution = v; }, { section: 'education', index: i, name: 'institution' }),
          field('Dates', () => ed.dates, (v) => { ed.dates = v; }, { section: 'education', index: i, name: 'dates' }))))),
      addBtn('Add education', () => restructure((d) => d.education.push(blank.education()))));
  }

  function certificationsCard() {
    const list = (d) => d.certifications;
    const setList = (d, v) => { d.certifications = v; };
    return card('certifications',
      h('ol', { class: 'master-list' }, ...s.draft.certifications.map((c, i) => h('li', { class: 'master-item', 'data-kind': 'certification' },
        h('div', { class: 'master-item-row' },
          field(`Certification ${i + 1}`, () => s.draft.certifications[i], (v) => { s.draft.certifications[i] = v; }, { section: 'certifications', index: i, name: null }),
          h('div', { class: 'master-item-actions' }, ...moveBtns(list, i, setList, 'certification'),
            iconBtn('trash', `Remove certification ${i + 1}`, () => restructure((d) => d.certifications.splice(i, 1)))))))),
      addBtn('Add certification', () => restructure((d) => d.certifications.push(''))));
  }

  function renderEditor() {
    if (!s.draft) return;
    mount(editorHost,
      !s.data.editable && h('p', { class: 'notice notice-warn', role: 'note' }, icon('alert', 14), ' ', s.data.read_only_reason),
      headerCard(), summaryCard(), skillsCard(), experienceCard(), educationCard(), certificationsCard(),
      h('div', { class: 'master-actions' },
        h('button', { class: 'btn btn-outline', 'data-action': 'discard', disabled: readOnly(), onClick: async () => {
          if (!dirty()) return;
          if (await confirmDialog({ title: 'Discard changes?', body: 'Restore the last saved master resume and drop your edits.', confirmLabel: 'Discard Changes' })) discard();
        } }, 'Discard Changes'),
        h('button', { class: 'btn btn-primary', 'data-action': 'save', disabled: readOnly(), onClick: (e) => withBusy(e.currentTarget, () => save()) },
          icon('check', 16), 'Save Master Resume')));
  }

  // ---- status, nav markers and preview (cheap; runs on every keystroke) -------------------
  const STATUS = { saved: ['All changes saved', 'green'], unsaved: ['Unsaved changes', 'orange'], saving: ['Saving…', 'neutral'], invalid: ['Fix errors to save', 'red'] };

  function refresh() {
    if (!s.draft) return;
    const changed = changedSections(s.draft, s.saved);
    const errs = s.errors.length ? s.errors : validate(s.draft);
    const [label, tone] = STATUS[s.status];
    mount(metaHost,
      h('span', { class: 'master-meta-item' }, 'Version ', h('code', { 'data-meta': 'version' }, s.data.version)),
      h('span', { class: 'master-meta-item' }, 'Last updated ', h('time', { datetime: s.data.updated_at }, fmtTime(s.data.updated_at))),
      h('span', { 'data-meta': 'status' }, badge(label, tone)),
      h('span', { 'data-meta': 'validation' }, errs.length ? badge(`${errs.length} validation issue${errs.length > 1 ? 's' : ''}`, 'red') : badge('Valid', 'green')));
    mount(navHost, ...SECTIONS.map(({ key, title }) => h('a', {
      class: `master-nav-link${changed.includes(key) ? ' is-edited' : ''}`, href: `#master-${key}`, 'data-section': key,
      onClick: (e) => { e.preventDefault(); cards[key]?.el.scrollIntoView?.({ behavior: 'smooth', block: 'start' }); },
    }, title, changed.includes(key) && h('span', { class: 'dot dot-orange', 'aria-label': 'edited' }))));
    for (const [key, { el, mark }] of Object.entries(cards)) {   // "clearly see which sections are being edited"
      mark.hidden = !changed.includes(key);
      el.setAttribute('data-edited', changed.includes(key) ? 'true' : 'false');
    }
    mount(previewHost, masterPaper(s.draft, s.data.contact));
  }

  // ---- page ----------------------------------------------------------------------------
  const previewCard = h('aside', { class: 'card master-preview', 'aria-label': 'Preview' },
    h('div', { class: 'master-card-head' }, h('h2', {}, 'Preview'),
      h('button', { type: 'button', class: 'btn btn-outline btn-sm', 'data-action': 'toggle-preview', onClick: (e) => {
        s.showPreview = !s.showPreview;
        previewHost.hidden = !s.showPreview;
        e.currentTarget.textContent = s.showPreview ? 'Hide preview' : 'Show preview';
      } }, 'Hide preview')),
    previewHost);

  const body = h('div', { class: 'master-layout' }, navHost, editorHost, previewCard);
  mount(root,
    pageHeader({ title: 'Master Resume', meta: metaHost,
      subtitle: 'Your master resume is the single source of truth for your career information. All tailored resumes are generated from this resume.' }),
    body);

  mount(editorHost, skeleton());
  const ready = api.get('/api/master-resume').then(load).catch((e) => {
    setLeaveGuard(null);
    mount(body, errorState(e.message, () => masterResumePage(root)));
  });
  return { ready, state: s, leaveGuard, save, discard };
}

// The master in the export layout (scripts/format_resume_doc.py): centred name / headline /
// contact row, ruled uppercase section headings, bold employer + job title, bold skill labels.
export function masterPaper(r, contact = {}) {
  const row = previewContact(contact?.line, r.profile_lines || []);
  const section = (key, ...kids) => [h('h2', {}, PREVIEW_TITLES[key]), ...kids];
  return h('article', { class: 'paper paper-master', 'aria-label': 'Master resume preview' },
    h('p', { class: 'paper-name', role: 'heading', 'aria-level': '2' }, r.name),
    h('p', { class: 'paper-tagline' }, r.headline),
    row && h('p', { class: 'paper-contact', 'data-preview': 'contact' }, row),
    h('p', { class: contact?.configured ? 'paper-note' : 'paper-note paper-note-warn', role: 'note' },
      contact?.configured ? CONTACT_NOTE
        : `RESUME_CONTACT_LINE is not configured, so exports would have no email or phone. ${CONTACT_NOTE}`),
    ...section('summary', ...String(r.summary || '').split(/\n\s*\n/).filter((p) => p.trim()).map((p) => h('p', {}, p))),
    ...section('skills', ...r.skills.map((g) => h('p', { class: 'paper-skill' }, h('strong', {}, `${g.label}:`), ` ${g.items}`))),
    ...section('experience', ...r.experience.flatMap((e) => [
      h('h3', {}, e.employer), e.title && h('p', { class: 'paper-position' }, e.title),
      h('p', { class: 'paper-dates' }, [e.dates, e.location].filter(Boolean).join(' | ')),
      h('ul', {}, ...e.bullets.map((b) => h('li', {}, b)))])),
    ...section('education', ...r.education.flatMap((ed) => [
      h('h3', {}, ed.degree), h('p', { class: 'paper-dates' }, [ed.institution, ed.dates].filter(Boolean).join(' | '))])),
    ...section('certifications', h('ul', {}, ...r.certifications.map((c) => h('li', {}, c)))));
}
