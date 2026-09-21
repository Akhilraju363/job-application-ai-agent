// A tiny DOM stand-in so the login screen can be rendered and driven under plain `node --test`
// (no jsdom, no dependencies). It implements only what web/js/dom.js and the login form touch.

class FakeBase {}

export class FakeText extends FakeBase {
  constructor(data) { super(); this.data = String(data); }
  get textContent() { return this.data; }
}

export class FakeElement extends FakeBase {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.disabled = false;
    this.focused = false;
  }

  setAttribute(k, v) { this.attributes[k] = String(v); }
  removeAttribute(k) { delete this.attributes[k]; }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  get hidden() { return 'hidden' in this.attributes; }
  set hidden(v) { if (v) this.attributes.hidden = ''; else delete this.attributes.hidden; }

  append(...nodes) { for (const n of nodes) this.children.push(n); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  get textContent() { return this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = [new FakeText(v)]; }

  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async dispatch(type, event = {}) {
    const e = { type, target: this, currentTarget: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...event };
    for (const fn of this.listeners[type] || []) await fn(e);
    return e;
  }
  focus() { this.focused = true; }

  // depth-first search helpers
  findAll(pred, out = []) {
    for (const c of this.children) {
      if (c instanceof FakeElement) { if (pred(c)) out.push(c); c.findAll(pred, out); }
    }
    return out;
  }
  find(pred) { return this.findAll(pred)[0] || null; }
  querySelector(tag) { return this.find((e) => e.tagName === tag.toUpperCase()); }
}

export function installFakeDom() {
  globalThis.Node = FakeBase;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_ns, tag) => new FakeElement(tag),
    createTextNode: (text) => new FakeText(text),
  };
}

// Any use of browser storage fails the test that triggered it: credentials must never be written there.
export function forbidBrowserStorage() {
  const trap = new Proxy({}, { get(_t, prop) { throw new Error(`browser storage touched: ${String(prop)}`); } });
  globalThis.sessionStorage = trap;
  globalThis.localStorage = trap;
}
