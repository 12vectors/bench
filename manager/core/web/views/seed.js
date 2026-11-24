/* Seed Brain, step one (task 192): `/brain/seed`, reached from an empty
   Brain or from Settings → Setup guide (`?from=setup`), and returning to
   whichever it came from.

   What the page shows is the server's reading (`GET /api/brain/seed`):
   the application and work repositories the board resolved, the Brain
   destination, and every candidate source by name and size — or why the
   mapping is refused. A person chooses sources (whole files or line
   slices), a treatment for each, exclusions and handbook sections, sees
   the exact reads that choice would make, and presses Inspect
   (`POST /api/brain/seed/inspect`), which reads exactly those and answers
   the version 1 seed manifest. Neither runs project code, a check or an
   agent, fetches anything or writes anything; generating a draft and
   applying it are later steps, said as not available here rather than
   drawn as controls that do nothing.

   Every string from the server — paths above all, which name files in
   somebody else's repository — goes through `esc`. */
import { S } from '../app/state.js';
import { fetch, post } from '../lib/api.js';
import { esc } from '../lib/dom.js';

const ROOT_WORDS = { app: 'Application', work: 'Work repository' };
const STATE_WORDS = { read: 'Read', absent: 'Absent', inaccessible: 'Inaccessible',
  excluded: 'Excluded' };
const GIT_WORDS = { committed: 'as committed', modified: 'modified, not committed',
  untracked: 'not in git' };

// the page's one seed session: kept across repaints and a visit elsewhere
// in the Brain, dropped by nothing but a reload or a fresh reading
const sd = { slot: null, loading: false, picked: null, exclude: '', sections: null,
  filter: '', busy: false, result: null, error: null, gen: 0 };
let repaint = () => {};
let wired = false;

function setSeedRepaint(fn) { repaint = fn; }

const keyOf = (c) => `${c.root}:${c.path}`;
const bytes = (n) => n == null ? '—' : n < 1024 ? `${n} B`
  : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / 1048576).toFixed(1)} MiB`;
const short = (s, n = 12) => s ? String(s).slice(0, n) : '—';

async function load() {
  if (sd.loading) return;
  sd.loading = true;
  const gen = ++sd.gen;
  let slot;
  try {
    const res = await fetch('/api/brain/seed');
    const data = await res.json().catch(() => ({}));
    slot = { data: data.seed || null, error: data.error || (res.ok ? null : `the board answered ${res.status}`) };
  } catch (e) {
    slot = { data: null, error: 'the board could not be asked' };
  }
  sd.loading = false;
  if (gen !== sd.gen) return;
  sd.slot = slot;
  const d = slot.data;
  if (d && !d.refused && sd.picked === null) {
    // what is selected to begin with: the defaults the server marked, and
    // nothing excluded. A person's choice is never replaced by a re-read
    sd.picked = new Map();
    for (const root of ['app', 'work']) {
      for (const c of d.candidates[root] || []) {
        if (c.default && c.state === 'candidate') sd.picked.set(keyOf(c), { root: c.root,
          path: c.path, treatment: c.treatment, lines: '' });
      }
    }
  }
  if (d && sd.sections === null) sd.sections = new Set(d.sections.map(s => s.id));
  repaint(true);
}

function originBack() {
  return S.brainSeed === 'setup'
    ? '<p class="b-back"><a href="/settings/setup" data-route>Back to Settings › Setup guide</a></p>'
    : '<p class="b-back"><a href="/brain" data-route>Back to Brain</a></p>';
}

function repoFacts(label, r) {
  if (!r) return '';
  const commit = r.commit ? `<code>${esc(short(r.commit))}</code>` : '<span class="b-none">no commit</span>';
  const dirty = r.dirty == null ? 'uncommitted work unknown'
    : r.dirty ? `${r.dirty} path${r.dirty === 1 ? '' : 's'} with uncommitted work` : 'clean';
  return `<dt>${esc(label)}</dt><dd><code class="sd-path">${esc(r.root)}</code><br>` +
    `${r.repository ? `repository <b>${esc(r.name || '')}</b> · ${esc(r.branch || 'detached')} @ ${commit} · ${esc(dirty)}`
      : '<span class="b-none">no repository</span>'}` +
    `<br><span class="b-note">${esc(r.role || '')}</span></dd>`;
}

function identityHtml(d) {
  const dest = d.destination || {};
  const brain = dest.brain || {};
  const hb = dest.handbook || {};
  return '<section class="b-panel" aria-labelledby="sd-h-connect">' +
    '<h2 id="sd-h-connect">1 · Repositories and destination</h2>' +
    '<p class="b-note">Read back from what this board resolved, not from what was asked for.</p>' +
    '<dl class="sd-facts">' +
    repoFacts('Application', d.app) + repoFacts('Work', d.work) +
    `<dt>Tool</dt><dd><code class="sd-path">${esc(d.tool.root)}</code><br>` +
    `<span class="b-note">${esc(d.tool.role)}</span></dd>` +
    `<dt>Layout</dt><dd>${esc(d.layout)} · ${esc(d.separation)}<br><span class="b-note">` +
    `${esc(d.separationWhy)}</span></dd>` +
    `<dt>Destination</dt><dd><code>${esc(brain.path || 'brain/')}</code> ${esc(brain.state || '')}` +
    `${brain.state === 'present' ? ` · ${brain.entries} entr${brain.entries === 1 ? 'y' : 'ies'}` : ''}` +
    ` · revision <code>${esc(brain.revision || 'none')}</code><br>` +
    `<code>${esc(hb.path || '')}</code> ${esc(hb.state || '')} · revision <code>${esc(hb.revision || 'none')}</code>` +
    '<br><span class="b-note">Nothing is written here by this page; a later, separate Apply is ' +
    'the only writer.</span></dd></dl></section>';
}

function refusedHtml(d) {
  return '<section class="b-panel b-bad" role="alert" aria-labelledby="sd-h-refused">' +
    '<h2 id="sd-h-refused">Seeding cannot use these repositories</h2>' +
    '<p>Nothing is listed or inspected, no repository is created, and nothing falls back ' +
    'to the application\'s git.</p><ul class="sd-problems">' +
    d.problems.map(p => `<li><b>${esc(p.why)}</b><br><span class="b-note">Remedy: ` +
      `${esc(p.remedy)}</span> <code>${esc(p.code)}</code></li>`).join('') + '</ul></section>';
}

function matches(c) {
  const q = sd.filter.trim().toLowerCase();
  return !q || c.path.toLowerCase().includes(q);
}

function treatmentSelect(k, value, treatments, label) {
  return `<select data-streat="${esc(k)}" data-skey="t:${esc(k)}" aria-label="${esc(label)}">` +
    Object.keys(treatments).map(t =>
      `<option value="${esc(t)}"${t === value ? ' selected' : ''}>${esc(t)}</option>`).join('') +
    '</select>';
}

function candidateRow(c) {
  const k = keyOf(c);
  const id = `sd-c-${encodeURIComponent(k).replace(/%/g, '_')}`;
  if (c.state !== 'candidate') {
    return `<li class="sd-row sd-off"><span class="sd-path">${esc(c.path)}</span> ` +
      `<span class="b-chip">${esc(STATE_WORDS[c.state] || c.state)}</span>` +
      `<br><span class="b-note">${esc(c.why || '')}</span></li>`;
  }
  const on = sd.picked.has(k);
  return `<li class="sd-row"><input type="checkbox" id="${esc(id)}" data-spick="${esc(k)}" ` +
    `data-skey="p:${esc(k)}"${on ? ' checked' : ''}> <label for="${esc(id)}">` +
    `<span class="sd-path">${esc(c.path)}</span></label> <span class="b-note">${bytes(c.bytes)}` +
    `${c.tracked === false ? ' · not in git' : ''}</span></li>`;
}

function rootHtml(d, root) {
  const all = d.candidates[root] || [];
  const groups = {};
  for (const c of all) (groups[c.category] = groups[c.category] || []).push(c);
  const order = Object.keys(d.categories).filter(k => groups[k]);
  const body = order.map(cat => {
    const list = groups[cat].filter(matches);
    const usable = list.filter(c => c.state === 'candidate');
    const off = list.filter(c => c.state !== 'candidate');
    const chosen = usable.filter(c => sd.picked.has(keyOf(c))).length;
    const open = chosen > 0 || (sd.filter.trim() && list.length);
    return `<details class="sd-cat"${open ? ' open' : ''}><summary>${esc(d.categories[cat].label)} ` +
      `<span class="b-count">${chosen} of ${usable.length} chosen</span>` +
      `${off.length ? ` <span class="b-note">${off.length} excluded</span>` : ''}</summary>` +
      (usable.length ? `<ul class="sd-list">${usable.map(candidateRow).join('')}</ul>`
        : '<p class="b-none">Nothing here can be chosen.</p>') +
      (off.length ? `<details class="sd-excl"><summary>Excluded, and why (${off.length})</summary>` +
        `<ul class="sd-list">${off.map(candidateRow).join('')}</ul></details>` : '') +
      '</details>';
  }).join('');
  const trunc = d.truncated && d.truncated[root]
    ? `<p class="b-warn">The listing stopped at ${d.bounds.listedPerRoot} files; narrow with the ` +
      'filter or an exclusion. What is not listed is not read.</p>' : '';
  return `<h3 class="sd-root">${esc(ROOT_WORDS[root])}</h3>${trunc}` +
    (body || '<p class="b-none">No files.</p>');
}

function parseLines(text) {
  const m = String(text || '').trim().match(/^(\d+)\s*[-–]\s*(\d+)$/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function plannedHtml(d) {
  const rows = [...sd.picked.entries()];
  const total = rows.reduce((n, [k]) => {
    const [root] = k.split(':');
    const c = (d.candidates[root] || []).find(x => keyOf(x) === k);
    return n + ((c && c.bytes) || 0);
  }, 0);
  const sections = d.sections.map(s => {
    const id = `sd-s-${s.id}`;
    return `<li><input type="checkbox" id="${esc(id)}" data-ssection="${esc(s.id)}" ` +
      `data-skey="s:${esc(s.id)}"${sd.sections.has(s.id) ? ' checked' : ''}> ` +
      `<label for="${esc(id)}"><b>${esc(s.title)}</b></label> <span class="b-note">${esc(s.about)}</span></li>`;
  }).join('');
  const list = rows.length ? `<ul class="sd-list sd-planned">` + rows.map(([k, p]) => {
    const bad = p.lines.trim() && !parseLines(p.lines);
    return `<li class="sd-row"><span class="sd-path">${esc(ROOT_WORDS[p.root])} · ${esc(p.path)}</span>` +
      `<span class="sd-ctl">${treatmentSelect(k, p.treatment, d.treatments, `treatment for ${p.path}`)}` +
      `<input type="text" inputmode="numeric" placeholder="whole file" size="9" value="${esc(p.lines)}" ` +
      `data-slines="${esc(k)}" data-skey="l:${esc(k)}" aria-label="lines of ${esc(p.path)} to read, ` +
      `for example 1-40"${bad ? ' aria-invalid="true"' : ''}></span>` +
      (bad ? '<br><span class="b-missing">Write a slice as first-last, for example 1-40.</span>' : '') +
      '</li>';
  }).join('') + '</ul>'
    : '<p class="b-none">Nothing is chosen yet. Choose sources above.</p>';
  const over = rows.length > d.bounds.selected;
  return '<section class="b-panel" aria-labelledby="sd-h-plan">' +
    '<h2 id="sd-h-plan">3 · Exactly what Inspect would read</h2>' +
    `<p>${rows.length} source${rows.length === 1 ? '' : 's'} · about ${bytes(total)} on disk ` +
    `(at most ${bytes(d.bounds.fileBytes)} a file and ${bytes(d.bounds.totalBytes)} in all). ` +
    'Nothing beyond this list is read.</p>' +
    (over ? `<p class="b-warn">At most ${d.bounds.selected} sources are inspected at once.</p>` : '') +
    list +
    '<details class="sd-treat"><summary>What each treatment means</summary><dl class="sd-facts">' +
    Object.entries(d.treatments).map(([t, w]) => `<dt>${esc(t)}</dt><dd>${esc(w)}</dd>`).join('') +
    '</dl></details>' +
    '<h3 class="sd-root">Handbook sections in scope</h3>' +
    `<ul class="sd-list sd-sections">${sections}</ul>` +
    '<h3 class="sd-root"><label for="sd-exclude">Your exclusions</label></h3>' +
    '<p class="b-note">One glob per line, matched against each path, such as <code>docs/old/*</code>. ' +
    'They narrow; nothing un-excludes what bench always leaves out.</p>' +
    `<textarea id="sd-exclude" rows="3" data-sexclude data-skey="x">${esc(sd.exclude)}</textarea>` +
    '<div class="sd-acts">' +
    `<button type="button" class="b-btn b-primary${sd.busy ? ' busy' : ''}" data-sact="inspect"` +
    `${!rows.length || over || !sd.sections.size || sd.busy ? ' disabled' : ''}` +
    `${sd.busy ? ' aria-busy="true"' : ''}>${sd.busy ? 'Inspecting…' : `Inspect ${rows.length} selected`}</button>` +
    '<button type="button" class="b-btn" data-sact="defaults">Choose the suggested sources</button>' +
    '<button type="button" class="b-btn" data-sact="clear">Clear the choice</button></div>' +
    '<p class="b-note">Inspecting reads and hashes these files. It runs no project code, script, ' +
    'package manager, check or agent, fetches nothing and writes nothing.</p></section>';
}

function resultHtml() {
  if (sd.error) {
    return `<section class="b-panel b-bad" role="alert"><h2>Inspection refused</h2><p>${esc(sd.error)}</p></section>`;
  }
  const m = sd.result;
  if (!m) return '';
  const counts = Object.entries(m.coverage.counts).map(([k, n]) =>
    `<span class="b-chip">${esc(STATE_WORDS[k] || k)} ${n}</span>`).join(' ');
  const gaps = m.coverage.gaps.length
    ? '<ul class="sd-list">' + m.coverage.gaps.map(g =>
      `<li><b>${esc((m.destination.sections.find(s => s.id === g.section) || {}).title || g.section)}</b> — ` +
      `${esc(g.why)}</li>`).join('') + '</ul>'
    : '<p>Every chosen section has at least one source that was read.</p>';
  const rows = m.items.map(i =>
    `<tr><td><span class="sd-path">${esc(ROOT_WORDS[i.root])} · ${esc(i.path)}` +
    `${i.lines ? ` · lines ${i.lines[0]}–${i.lines[1]}` : ''}</span>` +
    `${i.why ? `<br><span class="b-note">${esc(i.why)}</span>` : ''}</td>` +
    `<td>${esc(STATE_WORDS[i.state] || i.state)}</td><td>${bytes(i.bytes)}</td>` +
    `<td><code>${esc(short(i.sha256))}</code></td><td>${esc(GIT_WORDS[i.git] || '—')}</td>` +
    `<td>${esc(i.treatment)}</td></tr>`).join('');
  return '<section class="b-panel" aria-labelledby="sd-h-result" tabindex="-1" id="sd-result">' +
    '<h2 id="sd-h-result">4 · Inspection result</h2>' +
    '<p role="status"><b>Inspection complete is not a draft.</b> ' + counts +
    ` · ${bytes(m.coverage.bytes)} read</p>` +
    `<h3 class="sd-root">Coverage gaps</h3><p class="b-note">${esc(m.coverage.note)}</p>${gaps}` +
    '<div class="sd-table"><table><caption class="b-note">What was read, by item</caption><thead><tr>' +
    '<th scope="col">Source</th><th scope="col">State</th><th scope="col">Bytes</th>' +
    '<th scope="col">sha256</th><th scope="col">Git</th><th scope="col">Treatment</th></tr></thead>' +
    `<tbody>${rows}</tbody></table></div>` +
    `<p>Manifest version ${esc(m.v)}, digest <code class="sd-path">${esc(m.digest)}</code>, ` +
    `application @ <code>${esc(short(m.repositories.app.commit))}</code>, work @ ` +
    `<code>${esc(short(m.repositories.work.commit))}</code>.</p>` +
    `<p class="b-warn"><b>This authorizes ${esc(m.authorizes)}.</b></p>` +
    '<p class="b-note">Generating a draft from this manifest, and applying it, are later steps ' +
    'that are not available on this board yet.</p>' +
    '<details class="sd-json"><summary>The manifest, as JSON</summary>' +
    `<pre class="sd-pre">${esc(JSON.stringify(m, null, 2))}</pre></details></section>`;
}

function seedHtml() {
  if (!sd.slot) {
    load();
    return { loading: true, html: originBack() +
      '<p class="b-loading" role="status" aria-busy="true">Reading the repositories…</p>' };
  }
  const d = sd.slot.data;
  const head = originBack() + '<div class="b-head"><h1 class="b-title" tabindex="-1">Seed Brain from ' +
    'this project</h1><p class="b-lede">Choose which files a project handbook may be drafted ' +
    'from, see exactly what would be read, and inspect them. This page reads; it never ' +
    'generates or applies a draft.</p></div>';
  if (!d) {
    return { loading: false, html: head + `<div class="b-panel b-bad" role="alert"><p>` +
      `${esc(sd.slot.error || 'the seed reading could not be had')}</p>` +
      '<button type="button" class="b-btn" data-sact="reread">Read again</button></div>' };
  }
  if (d.refused) {
    return { loading: false, html: head + identityHtml(d) + refusedHtml(d) +
      '<div class="sd-acts"><button type="button" class="b-btn" data-sact="reread">Read again</button></div>' };
  }
  const fixed = '<details class="sd-excl"><summary>Always left out, whatever is chosen</summary>' +
    `<ul class="sd-list">${d.fixedExclusions.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`;
  const choose = '<section class="b-panel" aria-labelledby="sd-h-choose">' +
    '<h2 id="sd-h-choose">2 · Choose sources</h2>' +
    '<p class="b-note">Names and sizes only: nothing below has been opened. Source text is ' +
    'evidence, never instructions or permission.</p>' +
    '<p><label for="sd-filter">Filter by path</label> <input type="search" id="sd-filter" ' +
    `data-sfilter data-skey="f" value="${esc(sd.filter)}"></p>` + fixed +
    rootHtml(d, 'app') + rootHtml(d, 'work') +
    '<div class="sd-acts"><button type="button" class="b-btn" data-sact="reread">Read the ' +
    'repositories again</button></div></section>';
  return { loading: false, html: head + identityHtml(d) + choose + plannedHtml(d) + resultHtml() };
}

async function inspect() {
  const d = sd.slot && sd.slot.data;
  if (!d || sd.busy) return;
  const sources = [];
  for (const p of sd.picked.values()) {
    const item = { root: p.root, path: p.path, treatment: p.treatment };
    if (p.lines.trim()) {
      const lines = parseLines(p.lines);
      if (!lines) { sd.error = `the slice for ${p.path} is not first-last`; sd.result = null; repaint(true); return; }
      item.lines = lines;
    }
    sources.push(item);
  }
  sd.busy = true;
  sd.error = null;
  repaint(true);
  const res = await post('/api/brain/seed/inspect', {
    sources, sections: d.sections.map(s => s.id).filter(id => sd.sections.has(id)),
    exclude: sd.exclude.split('\n').map(x => x.trim()).filter(Boolean) });
  sd.busy = false;
  if (res.ok) { sd.result = res.data.manifest; sd.error = null; }
  else { sd.result = null; sd.error = res.error; }
  repaint(true);
  document.getElementById(sd.error ? 'sd-h-plan' : 'sd-result')?.focus();
}

function chooseDefaults() {
  const d = sd.slot && sd.slot.data;
  if (!d) return;
  sd.picked = new Map();
  for (const root of ['app', 'work']) {
    for (const c of d.candidates[root] || []) {
      if (c.default && c.state === 'candidate') sd.picked.set(keyOf(c), { root: c.root,
        path: c.path, treatment: c.treatment, lines: '' });
    }
  }
}

function seedAct(name) {
  if (name === 'inspect') { inspect(); return; }
  if (name === 'reread') {
    // a fresh reading; the choice made so far is kept
    sd.slot = null;
    sd.result = null;
    sd.error = null;
  } else if (name === 'defaults') chooseDefaults();
  else if (name === 'clear') sd.picked = new Map();
  // a changed choice is a different inspection: the old result is not its
  if (name !== 'reread') { sd.result = null; sd.error = null; }
  repaint(true);
}

function candidateFor(k) {
  const d = sd.slot && sd.slot.data;
  if (!d) return null;
  const [root] = k.split(':');
  return (d.candidates[root] || []).find(c => keyOf(c) === k) || null;
}

/* The page's own fields. Kept here; nothing is sent until Inspect. */
function seedField(f, ev) {
  const ds = f.dataset || {};
  if (ds.spick !== undefined) {
    if (ev.type !== 'change') return true;
    const c = candidateFor(ds.spick);
    if (f.checked && c) sd.picked.set(ds.spick, { root: c.root, path: c.path, treatment: c.treatment, lines: '' });
    else sd.picked.delete(ds.spick);
  } else if (ds.streat !== undefined) {
    const p = sd.picked.get(ds.streat);
    if (p) p.treatment = f.value;
  } else if (ds.slines !== undefined) {
    const p = sd.picked.get(ds.slines);
    if (p) p.lines = f.value;
    if (ev.type === 'input') return true;
  } else if (ds.ssection !== undefined) {
    if (ev.type !== 'change') return true;
    if (f.checked) sd.sections.add(ds.ssection); else sd.sections.delete(ds.ssection);
  } else if (ds.sexclude !== undefined) {
    sd.exclude = f.value;
    if (ev.type === 'input') return true;
  } else if (ds.sfilter !== undefined) {
    // the list follows the filter as it is typed; it reads nothing
    if (sd.filter === f.value) return true;
    sd.filter = f.value;
    repaint(true);
    return true;
  } else return false;
  sd.result = null;
  sd.error = null;
  repaint(true);
  return true;
}

function wireSeed(el) {
  if (wired) return;
  wired = true;
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-sact]');
    if (!b || !el.contains(b) || b.disabled) return;
    seedAct(b.dataset.sact);
  });
}

export { seedField, seedHtml, setSeedRepaint, wireSeed };
