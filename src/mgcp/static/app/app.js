/* Router, data layer and shared primitives.
 *
 * No build step: plain ES modules, d3 from a CDN. State lives in the URL hash
 * so a view is linkable and a reload lands where you were.
 */

import { VIEWS } from './views.js';

// ------------------------------------------------------------------ data ----
// One cache per endpoint. Refetch holds the previous render rather than
// flashing a skeleton, so there is no layout jump.

const cache = new Map();

export async function api(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const res = await fetch(path, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`${path} → ${res.status} ${res.statusText}`);
  const body = await res.json();
  cache.set(path, body);
  return body;
}

export function invalidate() { cache.clear(); }

// ------------------------------------------------------------ primitives ----

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const pct = (v, digits = 0) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`);
export const num = (v) => (v === null || v === undefined ? '—' : v.toLocaleString());

export function shortDate(iso) {
  if (!iso) return '—';
  return String(iso).slice(0, 10);
}

/** A stat tile. The number IS the chart — never a one-bar bar chart. */
export function tile({ label, value, sub, meter, hero = false }) {
  return `<div class="card tile${hero ? ' hero' : ''}">
    <div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ''}
    ${meter !== undefined ? `<div class="meter"><span style="width:${Math.min(100, meter * 100)}%"></span></div>` : ''}
  </div>`;
}

export function legend(items) {
  return `<div class="legend">${items.map((i) =>
    `<span><i style="background:${i.color}"></i>${esc(i.name)}</span>`).join('')}</div>`;
}

/** Status is glyph + word. Hue alone fails CVD for good↔critical (ΔE 4.1). */
export function status(kind, text) {
  const glyph = { good: '✓', warn: '▲', bad: '●' }[kind] || '•';
  return `<span class="status ${kind}"><span class="glyph" aria-hidden="true">${glyph}</span>${esc(text)}</span>`;
}

/**
 * A sortable table. Every chart gets one of these as its twin so no value is
 * reachable only by hovering.
 */
export function table(rows, columns, { sortBy, desc = true, max } = {}) {
  if (!rows.length) return '<div class="empty">Nothing to show.</div>';
  let data = rows.slice();
  if (sortBy) {
    data.sort((a, b) => {
      const av = a[sortBy], bv = b[sortBy];
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const r = av > bv ? 1 : -1;
      return desc ? -r : r;
    });
  }
  if (max) data = data.slice(0, max);
  const head = columns.map((c) =>
    `<th class="${c.num ? 'num' : ''}" data-key="${c.key}"
        ${sortBy === c.key ? `aria-sort="${desc ? 'descending' : 'ascending'}"` : ''}>${esc(c.label)}</th>`).join('');
  const body = data.map((r) => `<tr>${columns.map((c) =>
    `<td class="${c.num ? 'num' : ''}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('');
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** The table twin of a chart, collapsed by default. */
export function twin(html, label = 'Table view') {
  return `<details class="twin"><summary>${esc(label)}</summary>${html}</details>`;
}

export function card(title, note, body, cls = '') {
  return `<section class="card ${cls}">
    <h2>${esc(title)}</h2>
    ${note ? `<p class="note">${note}</p>` : ''}
    ${body}
  </section>`;
}

/** Wire click-to-sort on any table rendered by table(). */
export function wireSort(root, rows, columns, mount, opts = {}) {
  let { sortBy, desc = true } = opts;
  const paint = () => {
    mount.innerHTML = table(rows, columns, { sortBy, desc, max: opts.max });
    mount.querySelectorAll('th[data-key]').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.key;
        if (key === sortBy) desc = !desc; else { sortBy = key; desc = true; }
        paint();
      });
    });
  };
  paint();
}

// ---------------------------------------------------------------- router ----

function currentRoute() {
  const id = (location.hash || '').replace(/^#\/?/, '') || VIEWS[0].id;
  return VIEWS.find((v) => v.id === id) || VIEWS[0];
}

let teardown = null;

async function render() {
  const view = currentRoute();
  const main = document.getElementById('view');

  document.querySelectorAll('#nav button').forEach((b) => {
    if (b.dataset.id === view.id) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  document.title = `${view.title} · MGCP`;

  if (teardown) { try { teardown(); } catch { /* nothing to undo */ } teardown = null; }
  main.style.opacity = '0.55';

  try {
    teardown = await view.render(main) || null;
  } catch (err) {
    main.innerHTML = `<div class="callout"><b>${esc(view.title)} could not load.</b><br>
      ${esc(err.message)}<br><br>
      If this says <code>already accessed by another instance of Qdrant client</code>, a second
      MGCP process holds the store: local-mode Qdrant permits one client per path.</div>`;
  } finally {
    main.style.opacity = '1';
    const stamp = new Date();
    document.getElementById('freshness').textContent =
      `read ${String(stamp.getHours()).padStart(2, '0')}:${String(stamp.getMinutes()).padStart(2, '0')}`;
  }
}

function buildNav() {
  const nav = document.getElementById('nav');
  nav.innerHTML = VIEWS.map((v) =>
    `<button type="button" data-id="${v.id}">${esc(v.title)}</button>`).join('');
  nav.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => { location.hash = `#/${b.dataset.id}`; });
  });
}

function wireTheme() {
  const saved = localStorage.getItem('mgcp-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  document.getElementById('theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('mgcp-theme', next); } catch { /* private mode */ }
    render();
  });
}

function waitForD3() {
  return new Promise((resolve) => {
    if (window.d3) return resolve();
    const t = setInterval(() => { if (window.d3) { clearInterval(t); resolve(); } }, 30);
  });
}

/* Say when the server is older than the files it serves.
 *
 * The assets are read from disk per request and the Python once per process, so
 * a dashboard left running serves new JavaScript against its own old API. The
 * first symptom is a view failing on a field that API does not return, which
 * reads as a defect in the view. This sits outside #view, so it survives the
 * view error it explains. */
async function warnIfServerIsStale() {
  const box = document.getElementById('stale');
  if (!box) return;
  let health;
  try { health = await api('/api/health'); } catch { return; }
  if (!health || !health.assets_stale) return;
  box.innerHTML = `<div class="callout"><b>This server is older than the page it is
    serving.</b><br>The assets on disk have changed since this process started, so a view may
    ask for an API field it does not have. Restart it:
    <code>python -m mgcp.web_server</code></div>`;
}

window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', async () => {
  buildNav();
  wireTheme();
  warnIfServerIsStale();
  await waitForD3();
  render();
});
