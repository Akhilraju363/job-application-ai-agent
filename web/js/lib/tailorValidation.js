// Client-side checks for the Tailor Resume form (pure, so it's unit-testable). The server
// (jd_analysis.normalize_job) is the real gate and repeats them.
// Job Title: required, any capitalisation. Company: optional (the server stores
// "Company Not Specified" when it's blank -- never a guessed employer).
export const MIN_JD = 80, MAX_JD = 30000;

/** @returns {string} the first error message, or '' when the input is acceptable. */
export function validateTailorInput(v) {
  if (!String(v.title ?? '').trim()) return 'Job title is required.';
  const desc = String(v.description ?? '');
  if (desc.length < MIN_JD) return `Paste the job description (at least ${MIN_JD} characters).`;
  if (desc.length > MAX_JD) return `That job description is too long (max ${MAX_JD.toLocaleString()} characters).`;
  if (v.url && !/^https?:\/\/\S+$/i.test(v.url)) return 'Job URL must start with http:// or https://';
  return '';
}
