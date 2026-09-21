// The sign-in screen. Plain username + password; the browser keeps only the HttpOnly session cookie.
import { h } from '../dom.js';
import { signIn } from '../lib/authClient.js';

/**
 * loginView({ onSignedIn, signInFn })
 *   onSignedIn()  called after the server accepted the credentials (the session cookie is already set)
 *   signInFn      injectable for tests; defaults to the real /api/auth/login call
 * States: initial -> loading (button disabled) -> error (invalid / rate limited / network) | success.
 */
export function loginView({ onSignedIn, signInFn = signIn } = {}) {
  const error = h('p', { class: 'error-text', role: 'alert', hidden: true });
  const user = h('input', {
    class: 'input', id: 'login-username', name: 'username', type: 'text', autocomplete: 'username',
    autocapitalize: 'none', spellcheck: 'false', required: true, maxlength: 256,
  });
  const pass = h('input', {
    class: 'input', id: 'login-password', name: 'password', type: 'password', autocomplete: 'current-password',
    required: true, maxlength: 1024,
  });
  const button = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Sign In');
  let busy = false;

  const showError = (message) => { error.textContent = message; error.hidden = false; };

  async function submit(e) {
    e?.preventDefault?.();
    if (busy) return;
    busy = true;
    error.hidden = true;
    button.disabled = true;
    button.textContent = 'Signing in…';
    let result;
    try {
      result = await signInFn(user.value, pass.value);
    } catch {
      result = { ok: false, message: 'Sign-in failed. Please try again.' };
    }
    if (result.ok) {
      pass.value = ''; // don't leave the password sitting in the field
      onSignedIn?.(result);
      return;
    }
    busy = false;
    button.disabled = false;
    button.textContent = 'Sign In';
    pass.value = '';
    showError(result.message);
    pass.focus?.();
  }

  const field = (id, label, input) => h('label', { class: 'field', for: id }, h('span', {}, label), input);
  return h('form', { class: 'card login', onSubmit: submit, 'aria-label': 'Sign in', novalidate: true },
    h('h1', {}, 'Job Application AI Agent'),
    h('p', { class: 'muted' }, 'Sign in to your dashboard'),
    field('login-username', 'Username', user),
    field('login-password', 'Password', pass),
    error, button);
}
