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

   Step two (task 193) follows an inspection: **Review what generation
   would use** asks the board (`POST /api/brain/seed/preflight`, a read)
   for the harness, model, profile, exact reads, capabilities, budget and
   the approval digest; **Generate draft** — armed, then fired — sends that
   approval (`POST /api/brain/seed/generate`) and the private draft's
   progress, questions and sections are read back from
   `/api/brain/drafts`. Continue, Cancel and Discard are the drafting
   routes. Nothing here applies a draft: that is the next step (#194).

   Every string from the server — paths above all, which name files in
   somebody else's repository — goes through `esc`, and drafted Markdown
   through the page's own safe renderer. */
import { S } from '../app/state.js';
import { fetch, post } from '../lib/api.js';
import { esc } from '../lib/dom.js';
import { md } from '../lib/md.js';

const ROOT_WORDS = { app: 'Application', work: 'Work repository' };
const STATE_WORDS = { read: 'Read', absent: 'Absent', inaccessible: 'Inaccessible',
  excluded: 'Excluded' };
const GIT_WORDS = { committed: 'as committed', modified: 'modified, not committed',
  untracked: 'not in git', unknown: 'git state unknown' };

// the page's one seed session: kept across repaints and a visit elsewhere
// in the Brain, dropped by nothing but a reload or a fresh reading
const sd = { slot: null, loading: false, picked: null, exclude: '', sections: null,
  filter: '', busy: false, result: null, error: null, gen: 0,
  // step two: the preflight shown, the armed Generate, the private draft
  pre: null, preBusy: false, preError: null, note: '', armed: false, genBusy: false,
  genError: null, draft: null, answers: {}, contNote: '', stepBusy: false, poll: null };
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
  // a reload or a restart resumes the newest private seed draft, read back
  // from the board — the page holds nothing the board does not
  if (!sd.draft) {
    try {
      const res = await fetch('/api/brain/drafts');
      const list = await res.json().catch(() => ({}));
      const open = (list.seeds || []).find(x => !['discarded', 'cancelled', 'expired'].includes(x.status));
      if (open && gen === sd.gen) { sd.draft = open; watch(); }
    } catch (e) { /* the draft list is a convenience; the page works without it */ }
  }
  repaint(true);
}

async function readDraft() {
  if (!sd.draft) return;
  try {
    const res = await fetch(`/api/brain/drafts?id=${encodeURIComponent(sd.draft.id)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.draft) sd.draft = data.draft;
  } catch (e) { /* asked again on the next beat */ }
  repaint(true);
  watch();
}

// while a round runs, read it again every couple of seconds; nothing else
function watch() {
  if (sd.poll) { clearTimeout(sd.poll); sd.poll = null; }
  if (sd.draft && sd.draft.status === 'running') sd.poll = setTimeout(readDraft, 1500);
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
    `${c.tracked === false ? ' · not in git' : ''}${c.sliceOnly ? ' · a slice of lines only' : ''}` +
    '</span></li>';
}

// a picked source that cannot be inspected as it stands, and why — or null
function sliceProblem(k, p) {
  const c = candidateFor(k);
  if (p.lines.trim() && !parseLines(p.lines)) return 'Write a slice as first-last, for example 1-40.';
  if (c && c.sliceOnly && !p.lines.trim()) {
    return 'This file is over the size read whole: choose a slice of its lines, for example 1-200.';
  }
  return null;
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
    // a slice reads at most the bound for one file
    return n + Math.min((c && c.bytes) || 0, d.bounds.fileBytes);
  }, 0);
  const problems = rows.filter(([k, p]) => sliceProblem(k, p)).length;
  const sections = d.sections.map(s => {
    const id = `sd-s-${s.id}`;
    return `<li><input type="checkbox" id="${esc(id)}" data-ssection="${esc(s.id)}" ` +
      `data-skey="s:${esc(s.id)}"${sd.sections.has(s.id) ? ' checked' : ''}> ` +
      `<label for="${esc(id)}"><b>${esc(s.title)}</b></label> <span class="b-note">${esc(s.about)}</span></li>`;
  }).join('');
  const list = rows.length ? `<ul class="sd-list sd-planned">` + rows.map(([k, p]) => {
    const bad = sliceProblem(k, p);
    const c = candidateFor(k);
    const hint = `sd-lh-${encodeURIComponent(k).replace(/%/g, '_')}`;
    return `<li class="sd-row"><span class="sd-path">${esc(ROOT_WORDS[p.root])} · ${esc(p.path)}</span>` +
      `<span class="sd-ctl">${treatmentSelect(k, p.treatment, d.treatments, `treatment for ${p.path}`)}` +
      `<input type="text" inputmode="numeric" placeholder="${c && c.sliceOnly ? 'lines, e.g. 1-200' : 'whole file'}" ` +
      `size="9" value="${esc(p.lines)}" data-slines="${esc(k)}" data-skey="l:${esc(k)}" ` +
      `aria-label="lines of ${esc(p.path)} to read, for example 1-40"` +
      `${bad ? ` aria-invalid="true" aria-describedby="${esc(hint)}"` : ''}></span>` +
      (bad ? `<br><span class="b-missing" id="${esc(hint)}">${esc(bad)}</span>` : '') +
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
    (problems ? `<p class="b-warn">${problems} source${problems === 1 ? ' needs' : 's need'} a ` +
      'slice before it can be inspected.</p>' : '') +
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
    `${!rows.length || over || problems || !sd.sections.size || sd.busy ? ' disabled' : ''}` +
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
    '<p class="b-note">Generating a private draft from this manifest is the next step, below. ' +
    'Applying a draft is a later step that is not available on this board yet.</p>' +
    '<details class="sd-json"><summary>The manifest, as JSON</summary>' +
    `<pre class="sd-pre">${esc(JSON.stringify(m, null, 2))}</pre></details></section>`;
}

/* ── step two: a private, evidence-linked draft (task 193) ───────────── */

const DRAFT_WORDS = { running: 'Drafting', questions: 'Waiting for your answers',
  proposed: 'Private draft ready', failed: 'Failed', cancelled: 'Cancelled',
  discarded: 'Discarded', expired: 'Expired' };
const AUTH_WORDS = { 'documented-policy': 'documented policy',
  'inferred-convention': 'inferred convention — not an approved agreement',
  'proposed-agreement': 'proposed agreement — not agreed' };

function list(items) {
  return items && items.length ? `<ul class="sd-list">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
    : '<p class="b-none">Nothing.</p>';
}

function preflightHtml() {
  const p = sd.pre;
  if (sd.preError) {
    return `<div class="b-panel b-bad" role="alert"><p>${esc(sd.preError)}</p></div>`;
  }
  if (!p) return '';
  const model = p.selection && p.selection.model && p.selection.model.value;
  const caps = p.capabilities.map(c => `<li><b>${esc(c.name)}</b>: ` +
    `${esc(c.declared)}${c.required ? ` (requires ${esc(c.required)})` : ''} — ` +
    `${c.ok === true ? 'met' : c.ok === false ? '<span class="b-missing">not met</span>' : esc(c.note || '')}</li>`).join('');
  const reads = p.reads.map(r => `<li><span class="sd-path">${esc(r.ref)}</span> ` +
    `<span class="b-note">${esc(r.treatment)} · ${bytes(r.bytes)} · sha256 <code>${esc(r.sha256)}</code></span></li>`).join('');
  const b = p.budget;
  return '<div class="sd-pre-flight">' +
    (p.blockers.length ? `<div class="b-panel b-bad" role="alert"><p><b>Generate is not available.</b></p>` +
      `${list(p.blockers)}</div>` : '') +
    '<dl class="sd-facts">' +
    `<dt>Harness and model</dt><dd>${esc(p.adapter || '—')} · ${model ? `<code>${esc(model)}</code>`
      : 'the harness default model'}</dd>` +
    `<dt>Policy</dt><dd><code>${esc(p.policyId || '—')}</code> · template ` +
    `<code>${esc(p.template ? p.template.name : '—')}</code> (${esc(p.template ? p.template.source : '')})</dd>` +
    `<dt>Where it runs</dt><dd>${esc(p.launch.cwd)}<br><span class="b-note">${esc(p.launch.how)}. ` +
    `Denied: ${esc(p.launch.denied)}.</span>` +
    (p.launch.memory ? `<br><span class="b-note">${esc(p.launch.memory)}.</span>` : '') + '</dd>' +
    `<dt>Also given to the run</dt><dd>${esc(p.launch.context || '—')}</dd>` +
    `<dt>Destinations</dt><dd>${esc(p.destinations.writes)}</dd>` +
    `<dt>Budget</dt><dd>${b.files} files · ${bytes(b.bytes)} · at most ${b.rounds} rounds of ` +
    `${b.secondsPerRound}s each<br><span class="b-note">${esc(b.attempts)}</span><br>` +
    `<span class="b-warn">Tokens: ${esc(b.tokens.why)}</span></dd>` +
    `<dt>Authority</dt><dd>${esc(p.authority)}<br><span class="b-note">Enforced by ${esc(p.enforcement)}. ` +
    `${esc(p.retention)}.</span></dd></dl>` +
    `<details class="sd-excl" open><summary>Exactly what the run reads (${p.reads.length})</summary>` +
    `<ul class="sd-list">${reads}</ul></details>` +
    `<details class="sd-excl"><summary>What the harness enforces</summary><ul class="sd-list">${caps}</ul></details>` +
    `<p class="b-note">Approval <code>${esc(p.approval)}</code> binds manifest ` +
    `<code>${esc(short(p.manifest.digest))}</code>, the model, policy, template and budget. ` +
    'A change to any of them needs a new review.</p></div>';
}

function generateHtml() {
  if (!sd.result || sd.draft) return '';
  const p = sd.pre;
  const ready = p && p.ready && !sd.genBusy;
  return '<section class="b-panel" aria-labelledby="sd-h-gen" id="sd-gen" tabindex="-1">' +
    '<h2 id="sd-h-gen">5 · Generate a private draft</h2>' +
    '<p>A read-only coding agent drafts each chosen handbook section from exactly the sources ' +
    'inspected above, citing them by id. The draft stays private to this board; nothing is ' +
    'written to the Brain, the application, the cards or the configuration.</p>' +
    '<div class="sd-acts"><button type="button" class="b-btn" data-sact="gen-preflight"' +
    `${sd.preBusy ? ' disabled aria-busy="true"' : ''}>${sd.preBusy ? 'Reading…'
      : p ? 'Review again' : 'Review what generation would use'}</button></div>` +
    preflightHtml() +
    (p ? '<p><label for="sd-gnote">A note for the agent (optional)</label><br>' +
      `<textarea id="sd-gnote" rows="2" data-sgnote data-skey="g">${esc(sd.note)}</textarea></p>` +
      '<div class="sd-acts">' +
      `<button type="button" class="b-btn b-primary${sd.armed ? ' armed' : ''}" data-sact="generate"` +
      `${ready ? '' : ' disabled'}${sd.genBusy ? ' aria-busy="true"' : ''}>` +
      `${sd.genBusy ? 'Launching…' : sd.armed ? 'Confirm: generate draft (launches one paid run)'
        : 'Generate draft'}</button>` +
      (sd.armed ? '<button type="button" class="b-btn" data-sact="gen-disarm">Not now</button>' : '') +
      '</div>' : '') +
    (sd.genError ? `<p class="b-missing" role="alert">${esc(sd.genError)}</p>` : '') +
    '</section>';
}

function claimHtml(c) {
  return `<li><span class="b-chip">${esc(c.status)}</span>` +
    `${c.authority ? ` <span class="b-chip">${esc(AUTH_WORDS[c.authority] || c.authority)}</span>` : ''} ` +
    `${esc(c.statement)} <span class="b-note">${c.sources.length ? c.sources.map(s => `<code>${esc(s)}</code>`).join(' ')
      : 'no source'}</span></li>`;
}

function sectionHtml(s) {
  return `<article class="b-panel sd-section" aria-labelledby="sd-sec-${esc(s.section)}">` +
    `<h3 id="sd-sec-${esc(s.section)}">${esc(s.title)}</h3>` +
    (s.fromRound ? `<p class="b-note">Kept from round ${esc(s.fromRound)}: the newest round's ` +
      'version of this section was refused.</p>' : '') +
    (s.summary ? `<p class="b-note">${esc(s.summary)}</p>` : '') +
    `<div class="b-prose">${md(s.body || '', { base: 3 }) || '<p class="b-none">No body.</p>'}</div>` +
    (s.claims.length ? `<h4>Claims, and how far each goes</h4><ul class="sd-list">${s.claims.map(claimHtml).join('')}</ul>` : '') +
    (s.contradictions.length ? '<h4>Sources that disagree</h4><ul class="sd-list">' +
      s.contradictions.map(c => `<li>${esc(c.statement)} <span class="b-note">` +
        `${c.sources.map(x => `<code>${esc(x)}</code>`).join(' ')}</span></li>`).join('') + '</ul>' : '') +
    (s.gaps.length ? '<h4>Not known</h4><ul class="sd-list">' + s.gaps.map(g =>
      `<li><b>${esc(g.what)}</b>${g.why ? ` — ${esc(g.why)}` : ''}</li>`).join('') + '</ul>' : '') +
    (s.uncertainty ? `<p class="b-note">Uncertain: ${esc(s.uncertainty)}</p>` : '') +
    `<details class="sd-excl"><summary>Sources bound (${s.bindings.length})</summary><ul class="sd-list">` +
    s.bindings.map(b => `<li><code>${esc(b.ref)}</code> <span class="b-note">${esc(b.treatment)} · sha256 ` +
      `<code>${esc(short(b.sha256))}</code></span></li>`).join('') + '</ul></details></article>';
}

function draftHtml() {
  const d = sd.draft;
  if (!d) return '';
  const pr = d.progress || {};
  const seed = d.seed || {};
  const out = seed.draft;
  const tone = d.status === 'failed' ? ' b-bad' : '';
  const activity = pr.toolActivity || pr.process
    ? '<h3 class="sd-root">Process and tool activity</h3><p class="b-note">What the run is doing ' +
      'mechanically — activity, not progress.</p><dl class="sd-facts">' +
      (pr.process ? `<dt>Process</dt><dd>${esc(pr.process.status || 'unknown')}</dd>` : '') +
      (pr.toolActivity ? `<dt>Last tool</dt><dd>${esc((pr.toolActivity.last && pr.toolActivity.last.summary) ||
        pr.toolActivity.inFlight || 'none yet')}</dd>` : '') + '</dl>' : '';
  const questions = d.status === 'questions' && d.questions ? '<h3 class="sd-root">Questions</h3>' +
    d.questions.map(q => `<p><label for="sd-q-${esc(q.id)}"><b>${esc(q.question)}</b></label>` +
      `${q.why ? `<br><span class="b-note">${esc(q.why)}</span>` : ''}<br>` +
      `<textarea id="sd-q-${esc(q.id)}" rows="2" data-sanswer="${esc(q.id)}" data-skey="a:${esc(q.id)}">` +
      `${esc(sd.answers[q.id] || '')}</textarea></p>`).join('') : '';
  const canCont = d.canContinue && !sd.stepBusy;
  const acts = '<div class="sd-acts">' +
    (d.status === 'questions' ? `<button type="button" class="b-btn b-primary" data-sact="draft-continue"${canCont ? '' : ' disabled'}>Continue with these answers</button>` : '') +
    (['proposed', 'failed'].includes(d.status) && d.canContinue
      ? `<p><label for="sd-cnote">${d.status === 'failed' ? 'What to do differently (optional)' : 'What to redraft'}</label><br>` +
        `<textarea id="sd-cnote" rows="2" data-scnote data-skey="c">${esc(sd.contNote)}</textarea></p>` +
        `<button type="button" class="b-btn" data-sact="draft-continue"${canCont ? '' : ' disabled'}>` +
        `${d.status === 'failed' ? 'Try again (one more round)' : 'Redraft (one more round)'}</button>` : '') +
    (d.status === 'running' ? '<button type="button" class="b-btn" data-sact="draft-cancel">Cancel this round</button>' : '') +
    (!['running', 'discarded', 'cancelled', 'expired'].includes(d.status)
      ? '<button type="button" class="b-btn" data-sact="draft-discard">Discard the draft</button>' : '') +
    (['discarded', 'cancelled', 'expired', 'failed'].includes(d.status)
      ? '<button type="button" class="b-btn" data-sact="draft-new">Start a new draft</button>' : '') +
    '</div>';
  const refused = out && out.refused && out.refused.length
    ? `<div class="b-warn" role="status"><p><b>${out.partial ? 'A partial draft:' : 'Refused this round:'}` +
      '</b> the newest round\'s version of these sections was refused. A section an earlier ' +
      'round drafted keeps that version; the rest are not shown.</p><ul class="sd-list">' +
      out.refused.map(r => `<li><b>${esc(r.section)}</b> — ${esc(r.problems.join('; '))}` +
        `${r.kept ? ` <span class="b-note">(round ${esc(r.kept)}'s version kept)</span>` : ''}</li>`).join('') +
      '</ul></div>' : '';
  const gaps = (seed.manifest && seed.manifest.gaps) || [];
  return `<section class="b-panel${tone}" aria-labelledby="sd-h-draft" id="sd-draft" tabindex="-1">` +
    '<h2 id="sd-h-draft">5 · Private draft</h2>' +
    `<p role="status"><b>${esc(DRAFT_WORDS[d.status] || d.status)}</b> · round ${d.round} of ${d.maxRounds}` +
    `${seed.unknownRounds ? ` · ${seed.unknownRounds} round(s) whose launch is unknown, counted` : ''}</p>` +
    (d.error ? `<p class="b-missing" role="alert">${esc(d.error.why)}</p>` : '') +
    '<div class="sd-progress"><h3 class="sd-root">Progress</h3><dl class="sd-facts">' +
    `<dt>Done</dt><dd>${list(pr.done)}</dd>` +
    `<dt>Now</dt><dd>${esc(pr.doing || '—')}</dd>` +
    `<dt>Remaining</dt><dd>${list(pr.remaining)}</dd>` +
    `<dt>Blocked on</dt><dd>${esc(pr.blocked || 'nothing')}</dd></dl></div>` +
    activity + questions + acts + refused +
    (out ? `<p>${out.sections.length} of ${out.expected.length} sections · ${out.contradictions} ` +
      `contradiction${out.contradictions === 1 ? '' : 's'} kept visible · claims: ` +
      Object.entries(out.claims).map(([k, n]) => `${esc(k)} ${n}`).join(', ') + '</p>' +
      (gaps.length ? `<p class="b-warn">No approved source covers: ${gaps.map(g => esc(g.section)).join(', ')}. ` +
        'Those sections say what is missing instead of being filled in.</p>' : '') +
      out.sections.map(sectionHtml).join('') : '') +
    `<p class="b-note">${esc(seed.applies || '')} The draft is kept on this board only` +
    `${seed.expires ? ` until ${esc(new Date(seed.expires * 1000).toLocaleDateString())}` : ''}.</p>` +
    '</section>';
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
  return { loading: false, html: head + identityHtml(d) + choose + plannedHtml(d) + resultHtml() +
    generateHtml() + draftHtml() };
}

async function genPreflight() {
  if (!sd.result || sd.preBusy) return;
  sd.preBusy = true;
  sd.preError = null;
  sd.armed = false;
  repaint(true);
  const res = await post('/api/brain/seed/preflight', { manifest: sd.result });
  sd.preBusy = false;
  if (res.ok) { sd.pre = res.data.preflight; } else { sd.pre = null; sd.preError = res.error; }
  repaint(true);
  document.getElementById('sd-gen')?.focus();
}

async function generate() {
  if (!sd.pre || !sd.pre.ready || sd.genBusy) return;
  if (!sd.armed) { sd.armed = true; repaint(true); return; }
  sd.armed = false;
  sd.genBusy = true;
  sd.genError = null;
  repaint(true);
  // one identity per Generate: a resend after a lost reply answers, never launches twice
  sd.genKey = sd.genKey || `seed-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const res = await post('/api/brain/seed/generate', { request: sd.genKey, manifest: sd.result,
    approval: sd.pre.approval, text: sd.note });
  sd.genBusy = false;
  if (res.ok) {
    sd.draft = res.data.draft;
    sd.genKey = null;
    watch();
  } else {
    sd.genError = res.error;
    if (res.status && res.status !== 0) sd.genKey = null;
    if (res.data && res.data.stale) sd.pre = null;
  }
  repaint(true);
  document.getElementById(sd.draft ? 'sd-draft' : 'sd-gen')?.focus();
}

async function draftStep(step) {
  const d = sd.draft;
  if (!d || sd.stepBusy) return;
  const body = { id: d.id };
  if (step === 'continue') {
    body.request = `seed-c-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    body.round = d.round;
    if (d.status === 'questions') body.answers = { ...sd.answers };
    if (sd.contNote.trim()) body.note = sd.contNote.trim();
  }
  sd.stepBusy = true;
  repaint(true);
  const res = await post(`/api/brain/draft/${step}`, body);
  sd.stepBusy = false;
  if (res.ok && res.data.draft) {
    sd.draft = res.data.draft;
    sd.answers = {};
    sd.contNote = '';
    sd.genError = null;
  } else {
    sd.draft = { ...d, error: { why: res.error } };
  }
  repaint(true);
  watch();
}

async function inspect() {
  const d = sd.slot && sd.slot.data;
  if (!d || sd.busy) return;
  const sources = [];
  for (const p of sd.picked.values()) {
    const item = { root: p.root, path: p.path, treatment: p.treatment };
    const bad = sliceProblem(keyOf(p), p);
    if (bad) { sd.error = `${p.path}: ${bad}`; sd.result = null; repaint(true); return; }
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
  if (name === 'gen-preflight') { genPreflight(); return; }
  if (name === 'generate') { generate(); return; }
  if (name === 'gen-disarm') { sd.armed = false; repaint(true); return; }
  if (name === 'draft-continue') { draftStep('continue'); return; }
  if (name === 'draft-cancel') { draftStep('cancel'); return; }
  if (name === 'draft-discard') { draftStep('discard'); return; }
  if (name === 'draft-new') {
    sd.draft = null; sd.pre = null; sd.armed = false; repaint(true); return;
  }
  if (name === 'reread') {
    // a fresh reading; the choice made so far is kept
    sd.slot = null;
    sd.result = null;
    sd.error = null;
  } else if (name === 'defaults') chooseDefaults();
  else if (name === 'clear') sd.picked = new Map();
  // a changed choice is a different inspection: the old result is not its,
  // and neither is the generation preview approved for it
  if (name !== 'reread') { sd.result = null; sd.error = null; }
  sd.pre = null;
  sd.armed = false;
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
  // the draft's own text fields: kept as typed, sent only by their button
  if (ds.sgnote !== undefined) { sd.note = f.value; return true; }
  if (ds.scnote !== undefined) { sd.contNote = f.value; return true; }
  if (ds.sanswer !== undefined) { sd.answers[ds.sanswer] = f.value; return true; }
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
  sd.pre = null;
  sd.armed = false;
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
