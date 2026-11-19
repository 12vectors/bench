/* The board view: columns, cards, plan and stack chips and sheets. */
import { agentOnTask, allTasks } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { isArmed } from '../lib/arm.js';
import { $, esc } from '../lib/dom.js';
import { markScroll, restoreScroll } from '../lib/keep.js';
import { emptyNote, holdFocus, keyedPool, placeChildren, setHtml } from '../lib/keyed.js';
import { syncRoute } from '../lib/nav.js';
import { closeSheet, keyedCard, move, replan } from './card.js';
import { renderDrawer } from './detail/drawer.js';
import { findTask } from './focus.js';
import { phaseProgress } from './frame.js';
import { copyButton } from './profile.js';
import { STAGE_NOTE, STAGE_TINT } from '../words.js';

/* ── board ────────────────────────────────────────────────────────────── */

/* A phase member is not drawn on the Board view. It keeps its stage, its
   file, its agent and its actions — this view simply stops listing it,
   because the phase card stands for it here. The Board goes back to
   meaning one thing: the work you are personally holding, in five columns
   that fit.

   Membership is the only thing that hides a card, so *removing* membership
   is the un-hiding — no sweep, no migration, no second rule. A phase card
   archived, or a number dropped from its `## Cards` list, stops deriving
   the membership at all (the server weaves it fresh on every read), and a
   phase that has reached done/ is over: it holds nothing and hands its
   cards straight back into the columns they are genuinely in.

   A membership that did not resolve hides nothing either: an authoring
   mistake must not make work vanish, so a card wearing `phase drift` stays
   where it can be seen. */
function heldByPhase(task) {
  if (!task.phase || (task.phaseDrift || []).length) return false;
  const card = findTask(task.phase.file);
  return !!card && card.stage !== 'done';
}

/* What a column draws, and what its header says about what it does not.
   The count is the number of cards you can see — true by construction
   rather than true-with-a-footnote — and `+2 in phases` beside it is a
   signpost to where the rest went, in the register the existing note uses.
   It appears only where members are actually hidden, so a board with no
   phases on it reads exactly as it did before they existed.

   The agents note counts the agents on the cards this column draws;
   counting every agent anywhere is the header's live chip's job, and that
   chip still sees an agent working on a hidden member. */
function columnCards(stage) {
  return stage.tasks.filter(t => !heldByPhase(t));
}

function columnNote(stage, shown) {
  const agentsHere = shown.filter(t => agentOnTask(t.file)).length;
  const hidden = stage.tasks.length - shown.length;
  const notes = [];
  if (agentsHere) notes.push(`${agentsHere} agent${agentsHere > 1 ? 's' : ''}`);
  else if (STAGE_NOTE[stage.slug]) notes.push(STAGE_NOTE[stage.slug]);
  if (hidden) notes.push(`+${hidden} in phases`);
  return notes.join(' · ');
}

/* The board is painted by key (task 130). Its five columns are built once
   and kept — so their scroll, their drop listeners and a drag in flight
   all survive a paint — and each column's cards are reconciled: a card
   that would draw the same keeps its node, a changed one gets a fresh
   node in its place, a moved one is moved, and one that left is removed.
   A session event that changes nothing on the board therefore touches no
   card at all. The columns are built again only when the stages
   themselves change, and that rebuild keeps your place the way every
   rebuild here does. */
function renderBoard() {
  const board = $('#board');
  const stages = S.state.board.stages;
  const slugs = stages.map(s => s.slug).join('|');
  const paintColumns = () => holdFocus(board, () => {
    // every card on the board, by file: a card moved to another column
    // keeps its node too, when it would draw the same there
    const pool = keyedPool(board, 'article.card');
    const replaced = new Map();
    stages.forEach((stage, i) => {
      const col = board.children[i];
      const shown = columnCards(stage);
      setHtml(col.firstElementChild,
        `<span class="tickmark" style="background:${STAGE_TINT[stage.slug]}"></span>` +
        `<span>${stage.label}</span><span class="count">${shown.length}</span>` +
        `<span class="note">${esc(columnNote(stage, shown))}</span>`);
      const drop = col.lastElementChild;
      const nodes = [];
      // a column emptied by a phase says so rather than congratulating you
      if (!shown.length) nodes.push(emptyNote(drop,
        stage.tasks.length ? 'Everything here is in a phase.' : 'Nothing here. Good.'));
      for (const task of shown) nodes.push(keyedCard(pool, task, replaced));
      placeChildren(drop, nodes);
    });
    return replaced;
  });
  if (board.__stages !== slugs || board.children.length !== stages.length) {
    // where you were looking, taken before the wipe throws the nodes away:
    // the columns scroll down, #board itself scrolls sideways
    markScroll('v:board', board);
    board.querySelectorAll('.drop[data-stage]').forEach(d =>
      markScroll('v:col:' + d.dataset.stage, d));
    board.innerHTML = '';
    const drops = [];
    for (const stage of stages) {
      const col = document.createElement('section');
      col.className = 'kcol';
      col.id = 'col-' + stage.slug;
      col.setAttribute('aria-label', stage.label);
      col.appendChild(document.createElement('h2'));
      const drop = document.createElement('div');
      drop.className = 'drop';
      drop.dataset.stage = stage.slug;
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (e) => {
        e.preventDefault();
        drop.classList.remove('over');
        const { file, from } = JSON.parse(e.dataTransfer.getData('application/json'));
        if (from !== drop.dataset.stage) move(file, from, drop.dataset.stage);
      });
      col.appendChild(drop);
      board.appendChild(col);
      drops.push(drop);
    }
    board.__stages = slugs;
    paintColumns();
    // the new nodes are in place: put the page back where it was, same frame
    restoreScroll('v:board', board);
    for (const drop of drops) restoreScroll('v:col:' + drop.dataset.stage, drop);
  } else {
    paintColumns();
  }
  renderStageNav(stages);
  if (S.selected) {
    S.selected = allTasks().find(t => t.file === S.selected.file) || null;
    // a card that left the board while open (archived, deleted) closes its
    // view; the address follows in place, adding no entry (task 134)
    if (!S.selected) syncRoute({ replace: true });
  }
  renderDrawer();
}

/* The stage strip (task 154). On a phone the board shows one stage at a
   time, so a card needing attention three stages away would be out of
   sight; the strip names every stage with how many cards it draws and
   says which hold a card that needs attention. That reading is not made
   here: it is whatever the cards in that column already wear — a failed
   run, a halted phase, a delivery status in the alarm tone, drift — read
   off the faces the column just painted, so the strip can never disagree
   with the cards. A tap scrolls the board to the stage; the stage in view
   is marked as the current one. The strip is drawn on every width and
   shown only on a phone (board.css). Its buttons are built once per set
   of stages and updated in place, so a repaint never takes a focused
   button out from under a finger or a keyboard. */
const ATTENTION = '.card.verdict-bad, .card.run-failed, .card.halted, .card .pill.drift';

function stageInView() {
  const board = $('#board');
  const cols = [...board.children];
  if (!cols.length) return null;
  const left = board.getBoundingClientRect().left;
  let best = cols[0], dist = Infinity;
  for (const col of cols) {
    const d = Math.abs(col.getBoundingClientRect().left - left);
    if (d < dist) { dist = d; best = col; }
  }
  return best.id.replace(/^col-/, '');
}

function markStageInView() {
  const at = stageInView();
  for (const b of document.querySelectorAll('#stagenav [data-stage]')) {
    if (b.dataset.stage === at) b.setAttribute('aria-current', 'true');
    else b.removeAttribute('aria-current');
  }
}

function renderStageNav(stages) {
  const nav = $('#stagenav');
  if (!nav) return;
  const slugs = stages.map(s => s.slug).join('|');
  if (nav.__stages !== slugs) {
    nav.innerHTML = stages.map(s =>
      `<button type="button" data-stage="${esc(s.slug)}" aria-controls="col-${esc(s.slug)}">` +
      `<span class="tickmark" style="background:${STAGE_TINT[s.slug]}"></span>` +
      `<span class="sname">${esc(s.label)}</span><span class="scount"></span>` +
      `<span class="salarm" hidden>!</span></button>`).join('');
    nav.__stages = slugs;
    nav.querySelectorAll('[data-stage]').forEach(b => b.addEventListener('click', () => {
      const col = document.getElementById('col-' + b.dataset.stage);
      if (!col) return;
      col.scrollIntoView({ block: 'nearest', inline: 'start' });
      for (const o of nav.querySelectorAll('[data-stage]')) {
        if (o === b) o.setAttribute('aria-current', 'true');
        else o.removeAttribute('aria-current');
      }
    }));
    const board = $('#board');
    if (!board.__stageScroll) {
      board.__stageScroll = true;
      let pending = 0;
      board.addEventListener('scroll', () => {
        cancelAnimationFrame(pending);
        pending = requestAnimationFrame(markStageInView);
      }, { passive: true });
    }
  }
  for (const stage of stages) {
    const b = nav.querySelector(`[data-stage="${CSS.escape(stage.slug)}"]`);
    const drop = document.querySelector(`#board .drop[data-stage="${CSS.escape(stage.slug)}"]`);
    if (!b || !drop) continue;
    const shown = drop.querySelectorAll('article.card').length;
    const attention = new Set([...drop.querySelectorAll(ATTENTION)]
      .map(e => e.closest('article.card'))).size;
    setHtml(b.querySelector('.scount'), String(shown));
    b.querySelector('.salarm').hidden = !attention;
    b.classList.toggle('attention', !!attention);
    const name = `${stage.label}, ${shown} card${shown === 1 ? '' : 's'}` +
      (attention ? `, ${attention} need${attention === 1 ? 's' : ''} attention` : '');
    if (b.getAttribute('aria-label') !== name) b.setAttribute('aria-label', name);
  }
  markStageInView();
}

/* A phase is named by its card, so the chip says what the card says —
   minus the number it usually opens with (the chip's own tooltip carries
   that), and clipped to a chip's width. The whole name is one hover away. */
function phaseLabel(phase) {
  const name = phase.title ? phase.title.replace(/^\s*\d+\s*[—–-]\s*/, '') : phase.file;
  return name.length > 22 ? name.slice(0, 21) + '…' : name;
}

/* The unclipped name, for a card's chip: there the chip's CSS ellipsizes
   to the card's real width (task 72), and the header chip keeps the cut
   above. */
function phaseFullName(phase) {
  return phase.title ? phase.title.replace(/^\s*\d+\s*[—–-]\s*/, '') : phase.file;
}

/* The phase card is now the only thing standing for its members on this
   view, so it carries the summary it owes them: how far the run has got.
   Progress is what has landed on the phase branch, which only a run knows
   — before there is one the card says how much it holds, which is the
   honest reading of a phase nobody has started. */
function phaseSummary(task) {
  const snap = (S.state.phases || {})[task.file];
  if (snap) {
    const at = phaseProgress(snap);
    return `${at.done} of ${at.total} merged`;
  }
  const total = (task.members || []).length;
  return `${total} card${total === 1 ? '' : 's'}`;
}

/* …and what it is doing right now: the member in flight while a run is on,
   the halt while one is held — and the quiet case this exists for, that
   nobody has started it. A phase card sitting in in-progress/ that has not
   been run used to look exactly like one that is running: the header chip
   is the only other place that difference is written, and it is absent both
   when a phase has not started and when there is no phase at all. So the
   card says it itself. `idle` is not work — it takes no accent, no
   breathing mark and no caret, because nothing is happening. Null when the
   phase has no story to tell here: one waiting in to-do/ is just a card. */
function phaseFlight(task) {
  const snap = (S.state.phases || {})[task.file];
  if (!snap) return null;
  if (snap.halted) {
    return { bad: true, line: 'halted' + (snap.haltedAt ? ` at #${snap.haltedAt}` : '') +
      ' — ' + (snap.haltedWhy || snap.halted) };
  }
  if (!snap.running) {
    if (task.stage !== 'in-progress') return null;
    return snap.stopped
      ? { idle: true, pill: 'held',
          line: 'held — ▸ run phase carries on from where it stopped' }
      : { idle: true, pill: 'not started',
          line: 'not started — ▸ run phase cuts its branch and starts the first card' };
  }
  const at = phaseProgress(snap);
  if (at.on) return { bad: false, line: `on #${at.on.number || at.on.file} — ${at.on.title}` };
  if ((snap.waitingOn || []).length) {
    return { bad: false, line: `waiting on #${snap.waitingOn[0]}` };
  }
  return { bad: false, line: 'running' };
}

/* Which phases this card could join, which is also whether the action is
   there at all: the phase cards waiting in to-do/, and none whatsoever for
   a card already in a phase, a phase card itself (they do not nest), or a
   card with no number for a list to name it by. A phase in in-progress/ is
   running — its members are being worked in the order the list had when it
   started — so it is not on offer. The board offers what it can do. */

/* Where a card's work starts and what its PR targets, as a chip.

   Two branch names and an arrow, which is the whole of what a person
   needs at card altitude: `⌖ task/56 → task/56` reads as "cut from the
   prerequisite, aimed back at it". When the two agree — the usual stacked
   case — the arrow is redundant, so only one name is drawn. */
function planChipLabel(p) {
  if ((p.drift || []).length) return 'plan drift';
  // whole: the chip's CSS ellipsizes to the card's real width (task 72),
  // so the full name stays its accessible name; the ⌖ preflight draws it
  // wrapped with a copy control
  return p.startBranch === p.prBase ? p.startBranch : `${p.startBranch} → ${p.prBase}`;
}

/* The same label as markup: each branch name ellipsizes on its own and the
   arrow between them stays, so a narrow card still shows that there are
   two and where each begins. */
function planChipHtml(p) {
  if ((p.drift || []).length || p.startBranch === p.prBase) {
    return `<span class="clab">${esc(planChipLabel(p))}</span>`;
  }
  return `<span class="clab">${esc(p.startBranch)}</span><span class="carr">→</span>` +
    `<span class="clab">${esc(p.prBase)}</span>`;
}

/* A stacked card's chip, explained in the server's words: its edge, then
   the three targets apart. */
function stackChipTitle(s) {
  const t = s.targets || {};
  const bits = [];
  if (s.edge) bits.push(`${s.edge.label}: ${s.edge.sentence}`);
  if (t.selectedBase) bits.push(`Cut from ${t.selectedBase.branch}`);
  if (t.reviewTarget) bits.push(`Its change request targets ${t.reviewTarget.branch}`);
  if (t.landingTarget) bits.push(t.landingTarget.sentence.replace(/\.$/, ''));
  return bits.join('. ') + '.';
}

/* The Overview's Stack block: the card's path from the bottom of its
   stack, each card with its change request and its edge's state, then
   the cards stacked on this one, the three targets named apart, and what
   to do next and who holds it. Every word is the server's (`/api/state`
   `stacks`); nothing here counts or compares. */
function stackBlock(t) {
  const s = (S.state.stacks || {})[t.file];
  if (!s) return '';
  const who = c => c.number ? `#${c.number}` : c.file;
  const pr = c => c.pr ? ` · <a href="${esc(c.pr)}" target="_blank" rel="noopener">PR ↗</a>` : '';
  const edge = s.edge;
  const row = (c, state, here) =>
    `<li class="stkrow${here ? ' here' : ''}">` +
    `<span class="stkwho mono">${esc(who(c))}</span>` +
    `<span class="stktitle">${esc(c.title || c.file)}</span>` +
    `<span class="stkmeta"><span class="mono">${esc(c.branch)}</span>${pr(c)}</span>` +
    (state ? `<span class="stkstate" data-state="${esc(state.state)}" data-tone="${esc(state.tone)}">${esc(state.label)}</span>` : '') +
    `</li>`;
  const chain = (s.chain || []).map((c, i, all) =>
    row(c, i === all.length - 1 ? edge : null, i === all.length - 1)).join('');
  const kids = (s.children || []).map(c =>
    row(c, c.state ? { state: c.state, tone: c.tone, label: c.label } : null, false)).join('');
  const tg = s.targets || {};
  const pos = s.position ? (s.position.kind === 'chain'
    ? `${s.position.label} along this stack`
    : `${s.position.label} on its path — the stack branches, so there is no single order`) : '';
  return `<section class="stack" aria-label="Stack">` +
    `<h3>Stack${pos ? ` <span class="mono stkpos">${esc(pos)}</span>` : ''}</h3>` +
    (edge ? `<p class="stkedge" data-state="${esc(edge.state)}" data-tone="${esc(edge.tone)}"` +
            `${edge.tone === 'alarm' ? ' role="alert"' : ''}><b>${esc(edge.label)}.</b> ${esc(edge.sentence)}` +
            (edge.next ? ` <span class="stknext">Next (${esc(edge.who)}): ${esc(edge.next)}.</span>` : '') +
            `</p>` : '') +
    (chain && (s.chain || []).length > 1 ? `<ol class="stklist" aria-label="Path from the bottom of the stack">${chain}</ol>` : '') +
    (kids ? `<p class="dnote">Stacked on this card:</p><ul class="stklist">${kids}</ul>` : '') +
    `<dl class="dfacts">` +
    (tg.selectedBase ? `<dt>Selected base</dt><dd><span class="mono">${esc(tg.selectedBase.branch)}</span>` +
      (tg.selectedBase.commit ? ` at <span class="mono">${esc(tg.selectedBase.commit.slice(0, 12))}</span>` : '') +
      ` · ${esc(tg.selectedBase.source || '')}</dd>` : '') +
    (tg.reviewTarget ? `<dt>Review target</dt><dd><span class="mono">${esc(tg.reviewTarget.branch)}</span> · ${esc(tg.reviewTarget.source || '')}</dd>` : '') +
    (tg.landingTarget ? `<dt>Landing target</dt><dd>${esc(tg.landingTarget.sentence)}</dd>` : '') +
    `</dl>` + stackActions(t, s) + `</section>`;
}

/* The explicit stack actions (task 119): each opens its confirmation
   sheet — the server's preview, naming the identities it is bound to,
   what it does, what stops it and, where bench may not make a host edit,
   the exact manual step — and only an armed second click acts. The sheet
   lives in STACK_SHEETS so a live redraw keeps it. */
const STACK_SHEETS = {};

function stackActions(t, s) {
  const offered = s.actions || [];
  const open = STACK_SHEETS[t.file];
  if (!offered.length && !open) return '';
  const buttons = offered.map(a =>
    `<button type="button" class="stkbtn" data-stack-action="${esc(a.action)}" ` +
    `data-file="${esc(t.file)}">${esc(a.label)}</button>`).join(' ');
  return `<div class="stkactions" aria-label="Stack actions">${buttons}` +
    (open ? stackSheet(t, open) : '') + `</div>`;
}

function stackSheet(t, open) {
  const p = open.preview || {};
  const ids = p.identities || {};
  const short = v => v ? String(v).slice(0, 12) : 'none';
  const names = { childHead: 'This card\'s head', parentTip: 'Parent\'s tip',
                  target: 'Targets now', newTarget: 'Would target',
                  newTargetTip: 'Target\'s tip', change: 'Change request',
                  landing: 'Lands in' };
  const facts = Object.keys(names).filter(k => k in ids).map(k =>
    `<dt>${esc(names[k])}</dt><dd class="mono">${esc(k.endsWith('Head') || k.endsWith('Tip') ? short(ids[k]) : (ids[k] || 'none'))}</dd>`).join('');
  const blocked = (p.blockers || []).length;
  const manual = p.mode === 'manual';
  // armed in the one shared store (lib/arm.js), so it runs out by itself
  const armed = isArmed(S.acts, `${t.file}::stack-confirm`);
  const confirm = !blocked && !manual
    ? `<button type="button" class="stkbtn go${armed ? ' armed' : ''}" data-stack-confirm="${esc(open.action)}" ` +
      `data-file="${esc(t.file)}">${armed ? 'Click again to confirm' : esc(p.label || 'Confirm')}</button>`
    : '';
  return `<div class="stksheet" role="group" aria-label="${esc(p.label || 'Stack action')}">` +
    `<p><b>${esc(p.label || '')}</b> — ${esc(p.effects || '')}</p>` +
    `<dl class="dfacts">${facts}</dl>` +
    (p.notes || []).map(n => `<p class="dnote">${esc(n)}</p>`).join('') +
    (blocked ? `<p class="stkedge" data-tone="alarm" role="alert"><b>Refused.</b> ${esc(p.blockers[0])}</p>` : '') +
    (manual ? `<p class="stkedge" data-tone="alarm"><b>Manual step.</b> ${esc(p.manual || '')}. ` +
              `Bench changes nothing on the host here.</p>` : '') +
    (open.result ? `<p class="dnote" role="status">${esc(open.result)}</p>` : '') +
    confirm + ` <button type="button" class="stkbtn" data-stack-close="${esc(t.file)}">Close</button>` +
    `</div>`;
}

function planChipTitle(p) {
  if ((p.drift || []).length) return p.drift.join(' · ');
  const bits = [`Starts from ${p.startBranch} (${p.source})`,
                `Its PR targets ${p.prBase} (${p.prSource})`];
  if (p.recorded) {
    bits.push(p.startCommit
      ? `Recorded when the run began, at ${p.startCommit.slice(0, 9)} — a later `
        + 'change to the board\'s default does not retarget it'
      : 'Recorded when the run began');
    if (p.provenance === 'adopted')
      bits.push('Adopted: this branch predates its recorded plan, so its base '
                + 'was read back out of git rather than planned');
    if (p.delivery === 'local-only')
      bits.push('Local-only: this board cannot open the PR automatically');
  } else {
    bits.push('Not recorded yet — resolved from the card as it stands. '
              + 'Click for the full preflight.');
  }
  return bits.join('. ') + '.';
}

/* The preflight itself, on demand: everything the board resolved and
   validated, or the one reason it does not resolve. It is read-only — the
   server fetches and answers, and creates no branch, no worktree and no
   run — so opening it before a launch costs nothing and is the point.

   `⌖ replan` is the one button here, and it is the only thing in the whole
   design that retargets work already under way. It records a new plan or
   refuses; it never resets, rebases or removes anything. */
async function planSheet(task) {
  const wrap = $('#sheetwrap');
  // opened from a keyboard, the preflight takes focus and gives it back
  if (!wrap.classList.contains('open')) S.sheetReturn = document.activeElement;
  wrap.innerHTML = `<div class="sheet"><div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">⌖ preflight</span></div><p>Resolving…</p></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });
  const res = await fetch('/api/plan?file=' + encodeURIComponent(task.file) +
                          '&stage=' + encodeURIComponent(task.stage));
  const data = await res.json();
  if (!res.ok || !data.plan) {
    wrap.innerHTML = `<div class="sheet"><div class="stitle"><span>${esc(task.title)}</span>` +
      `<span class="mono">⌖ preflight</span></div>` +
      `<p>${esc(data.error || 'the plan could not be resolved')}</p>` +
      `<div class="sbtns"><button id="sh-noplan">Close<small>Nothing was started.</small></button>` +
      `</div></div>`;
    $('#sh-noplan').addEventListener('click', closeSheet);
    return;
  }
  const p = data.plan;
  // the branch values are the ones a card's ⌖ chip may show clipped, so
  // here each is drawn whole and carries its own copy control (task 72)
  const rows = [
    ['Starts from', (p.startRef || p.startBranch) +
      (p.startCommit ? ` · ${p.startCommit.slice(0, 9)}` : ''), p.source,
      p.startRef || p.startBranch],
    ['Branch', p.branch, p.continuing ? 'already exists — the run continues on it'
                                      : 'cut by the board when the run starts', p.branch],
    ['PR targets', p.prBase, p.prSource, p.prBase],
    ['Delivery', p.delivery === 'pr' ? 'a PR is opened automatically' : 'local-only',
      p.delivery === 'pr' ? `${p.appRemote} and gh are both available`
                          : 'the work itself is unaffected'],
  ];
  const blocked = (p.blockers || []).length;
  wrap.innerHTML =
    `<div class="sheet" role="dialog" aria-label="${esc('branch plan for ' + task.title)}">` +
    `<div class="stitle"><span id="pf-title" tabindex="-1">${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}⌖ preflight</span></div>` +
    (blocked
      ? `<p class="bad">This card will not start: ${esc(p.blockers[0])}</p>`
      : `<p>Resolved and validated. Nothing has been created — a branch, a ` +
        `worktree and a run follow the launch, not this.</p>`) +
    `<div class="prows full">` +
    rows.map(([k, v, why, copy]) => `<div class="prow"><span class="k">${esc(k)}</span>` +
      `<span class="v">${esc(v)}</span>` + (copy ? copyButton(copy, k) : '') +
      `<span class="why">${esc(why || '')}</span></div>`).join('') +
    `</div>` +
    ((p.notes || []).length
      ? `<p>${p.notes.map(n => esc(n)).join('<br>')}</p>` : '') +
    `<div class="sbtns">` +
    `<button id="sh-replan">⌖ Replan<small>Resolve this card afresh and record the ` +
    `result. Nothing is reset, rebased or removed — a branch that is not built on the ` +
    `new base is refused instead.</small></button>` +
    `<button id="sh-noplan">Close<small>Nothing is written.</small></button>` +
    `</div></div>`;
  $('#sh-noplan').addEventListener('click', closeSheet);
  $('#sh-replan').addEventListener('click', () => { closeSheet(); replan(task); });
  $('#pf-title').focus();
}

export {
  columnCards, columnNote, heldByPhase, phaseFlight, phaseFullName,
  phaseLabel, phaseSummary, planChipHtml, planChipLabel, planChipTitle,
  planSheet, renderBoard, STACK_SHEETS, stackActions, stackBlock,
  stackChipTitle, stackSheet
};
