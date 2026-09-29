// Pure helpers for the Master Resume editor (web/js/pages/masterResume.js). No DOM, no I/O.
// The server (scripts/master_resume.py) is authoritative: it re-validates and serialises to
// resume/base_resume.md. These mirror its rules so errors show before a round trip.

export const SECTIONS = [
  { key: 'header', title: 'Header' },
  { key: 'summary', title: 'Professional Summary' },
  { key: 'skills', title: 'Technical Skills' },
  { key: 'experience', title: 'Professional Experience' },
  { key: 'education', title: 'Education' },
  { key: 'certifications', title: 'Certifications' },
];

// The export's section names (scripts/format_resume_doc.py SECTION_TITLES).
export const PREVIEW_TITLES = {
  summary: 'PROFESSIONAL SUMMARY', skills: 'TECHNICAL SKILLS', experience: 'PROFESSIONAL EXPERIENCE',
  education: 'EDUCATION', certifications: 'CERTIFICATIONS',
};

const YEAR_RE = /\b(19|20)\d{2}\b/;
const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.]+/;
const PHONE_RE = /(?<![\w-])\+?\d[\d\s().-]{8,}\d(?![\w-])/g;
// An email, or a run of 10+ digits (a phone) -- '2015 - 2018' is a date range, not a phone.
export const looksLikeContact = (v) => EMAIL_RE.test(v || '')
  || [...String(v || '').matchAll(PHONE_RE)].some((m) => m[0].replace(/\D/g, '').length >= 10);

export const clone = (r) => JSON.parse(JSON.stringify(r));

export const blank = {
  skill: () => ({ label: '', items: '' }),
  role: () => ({ employer: '', title: '', dates: '', location: '', bullets: [''] }),
  education: () => ({ degree: '', institution: '', dates: '' }),
};

const sectionValue = (r, key) => (key === 'header' ? { name: r.name, headline: r.headline } : r[key]);

// Which sections differ from the last saved master -> the "Edited" markers.
export function changedSections(draft, saved) {
  if (!draft || !saved) return [];
  return SECTIONS.map((s) => s.key).filter((k) => JSON.stringify(sectionValue(draft, k)) !== JSON.stringify(sectionValue(saved, k)));
}

export const isDirty = (draft, saved) => changedSections(draft, saved).length > 0;

// Move list[i] by `dir` (-1 up, +1 down). Returns a new array; out-of-range moves are no-ops.
export function move(list, i, dir) {
  const j = i + dir;
  if (j < 0 || j >= list.length) return list.slice();
  const out = list.slice();
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

const trim = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();

// Same rules (and messages) as master_resume.validate(). Returns [{section, index, field, message}].
export function validate(r) {
  const errs = [];
  const err = (section, message, index = null, field = null) => errs.push({ section, index, field, message });
  const single = (section, value, index, field, label) => {
    const v = trim(value);
    if (!v) err(section, `${label} cannot be empty.`, index, field);
    else if (v.startsWith('#') || v.startsWith('- ')) err(section, `${label} can't start with '#' or '- ' (that would break the resume structure).`, index, field);
  };

  single('header', r.name, null, 'name', 'Name');
  single('header', r.headline, null, 'headline', 'Headline');
  if (!String(r.summary || '').trim()) err('summary', 'Professional summary cannot be empty.', null, 'summary');
  else if (String(r.summary).split('\n').some((l) => /^\s*(#|- )/.test(l))) err('summary', "Summary lines can't start with '#' or '- '.", null, 'summary');

  if (!r.skills.length) err('skills', 'Add at least one skill group.');
  r.skills.forEach((s, i) => {
    single('skills', s.label, i, 'label', 'Skill group name');
    if (s.label.includes(':')) err('skills', "Skill group name can't contain ':'.", i, 'label');
    if (!trim(s.items)) err('skills', 'Skill group needs at least one skill.', i, 'items');
  });

  if (!r.experience.length) err('experience', 'Add at least one employer.');
  r.experience.forEach((e, i) => {
    single('experience', e.employer, i, 'employer', 'Employer');
    single('experience', e.title, i, 'title', 'Job title');
    if (YEAR_RE.test(e.title)) err('experience', "Job title can't contain a year (years belong in the dates).", i, 'title');
    if (!trim(e.dates)) err('experience', 'Experience date is required.', i, 'dates');
    else if (!YEAR_RE.test(e.dates)) err('experience', "Dates must include a year, e.g. 'Jan 2022 – Jun 2024'.", i, 'dates');
    if (e.dates.includes('|')) err('experience', "Dates can't contain '|'.", i, 'dates');
    if (!trim(e.location)) err('experience', 'Location is required.', i, 'location');
    else if (e.location.includes('|')) err('experience', "Location can't contain '|'.", i, 'location');
    if (!e.bullets.length) err('experience', 'Add at least one bullet.', i, 'bullets');
    e.bullets.forEach((b, j) => { if (!trim(b)) err('experience', `Bullet ${j + 1} cannot be empty.`, i, `bullets.${j}`); });
  });

  r.education.forEach((ed, i) => {
    single('education', ed.degree, i, 'degree', 'Degree');
    single('education', ed.institution, i, 'institution', 'Institution');
    if (ed.institution.includes('|')) err('education', "Institution can't contain '|'.", i, 'institution');
    if (trim(ed.dates) && !YEAR_RE.test(ed.dates)) err('education', 'Dates must include a year.', i, 'dates');
  });
  r.certifications.forEach((c, i) => { if (!trim(c)) err('certifications', 'Certification cannot be empty.', i); });

  const values = [['header', r.name], ['header', r.headline], ['summary', r.summary],
    ...r.skills.map((s) => ['skills', `${s.label} ${s.items}`]),
    ...r.experience.map((e) => ['experience', [e.employer, e.title, e.dates, e.location, ...e.bullets].join(' ')]),
    ...r.education.map((ed) => ['education', Object.values(ed).join(' ')]),
    ...r.certifications.map((c) => ['certifications', c])];
  const hit = values.find(([, v]) => looksLikeContact(v));
  if (hit) err(hit[0], "Contact details (email/phone) can't be stored in the public master resume -- they come from RESUME_CONTACT_LINE at export.");
  return errs;
}

// Errors for one field (or, with field === undefined, the section/entry-level ones).
export function errorsFor(errors, section, index = null, field = null) {
  return errors.filter((e) => e.section === section && (e.index ?? null) === index && (e.field ?? null) === field).map((e) => e.message);
}

// The contact row the export shows: RESUME_CONTACT_LINE, with the public profile line merged in
// when the contact line doesn't already carry it (scripts/contact.py with_contact, simplified).
export function previewContact(contactLine, profileLines = []) {
  if (!contactLine) return profileLines.join(' | ');
  if (/linkedin\.com\//i.test(contactLine)) return contactLine;
  return [contactLine, ...profileLines].filter(Boolean).join(' | ');
}
