/* Phases: a swimlane each. */
import { allTasks } from '../app/data.js';
import { S } from '../app/state.js';
import { phaseOf } from '../lib/arm.js';
import { post } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { markScroll, restoreScroll, scrollMarks } from '../lib/keep.js';
import { emptyNote, holdFocus, keyedPool, placeChildren, setHtml } from '../lib/keyed.js';
import { plainClick, syncRoute } from '../lib/nav.js';
import { toast } from '../lib/toast.js';
import { cardId, findCard, formatRoute } from '../lib/route.js';
import { phaseFlight } from './board.js';
import { actLabel, holdPhase, keyedCard, runPhase, showDetail, wireAction } from './card.js';
import { renderDrawer } from './detail/drawer.js';
import { findTask } from './focus.js';
import { gateState } from './gates.js';
import { STAGE_TINT } from '../words.js';

/* ── phases: a swimlane each ──────────────────────────────────────────────

   The Board draws the work you are personally holding; this draws what the
   phases are holding. One lane per phase card, the same five stages left to
   right, and the cards are the cards — the very nodes keyedCard() paints for
   the Board, agent line, chips and hover actions intact. That fidelity is
   the whole reason this is a view and not a rail squeezed into five
   columns that could never spare the width.

   Nothing here ends a phase. Merging is a board move on the phase card, so
   there is exactly one place where work leaves the board, and it is not
   this one. */

/* Where a lane sits in the stack: a halt first, because it is the one
   thing on this page waiting on a person and it must not be below the
   fold; then a run in flight; then a run a person held, which is still
   theirs to resume and so belongs above the phases nobody has started;
   then the ones that are over. Every phase card draws a lane — a phase
   whose members are all merged and which is waiting on its own PR still
   owns them, and a lane that vanished exactly when it needed merging
   would be a view you could not trust. */
function laneRank(task) {
  const snap = (S.state.phases || {})[task.file];
  if (snap && snap.halted) return 0;
  if (snap && snap.running) return 1;
  if (snap && snap.stopped) return 2;
  return task.stage === 'done' ? 4 : 3;
}

function lanePhases() {
  return allTasks().filter(t => t.isPhase).sort((a, b) =>
    laneRank(a) - laneRank(b) ||
    (+a.number || 0) - (+b.number || 0) ||
    a.file.localeCompare(b.file));
}

/* `07`, `7` and `#007` are one card, on this side of the wire too —
   taskfiles.canonical_number, in the language the page is written in. */
function canonicalNumber(number) {
  const n = parseInt(number, 10);
  return Number.isNaN(n) ? String(number ?? '') : String(n);
}

/* The phase log: the `- <date> <time> · <entry>` lines the runner appends
   to a `## Phase log` section on the phase card, read here exactly as
   phases.py writes them. It is the record of every decision a run made,
   and the only thing that can tell "the phase has not reached this card"
   from "it started it and it ended badly" — until this view it had
   nowhere to be shown. */
const PHASE_LOG_LINE = /^-\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+·\s+(.+?)\s*$/;

function phaseLog(task) {
  // the server's reading of the log (task 129): the list carries no text
  const logs = S.state && S.state.phaseLogs;
  if (logs && task.body == null) return logs[task.file] || [];
  const lines = (task.body || '').split('\n');
  const start = lines.findIndex(line => /^##\s+Phase log\s*$/.test(line));
  if (start < 0) return [];
  const entries = [];
  for (const line of lines.slice(start + 1)) {
    if (/^##\s/.test(line)) break;
    const match = PHASE_LOG_LINE.exec(line.trim());
    if (match) entries.push({ at: match[1], text: match[2] });
  }
  return entries;
}

/* Which members the phase branch already holds, as canonical numbers —
   the server's answer (`phases.merged_members`, task 125), read from the
   runner's last pass and the phase log. The page draws it; it no longer
   works it out. */
function mergedIn(task) {
  return new Set(((S.state && S.state.phaseMerged) || {})[task.file] || []);
}

/* The columns a lane runs. The first four are the board's own stages, named
   by the server that names them everywhere else. The last one is the
   phase's own and it is deliberately not done/: a member merged into the
   phase branch is finished as far as the phase is concerned, and is not in
   main yet — main waits on the phase's own PR, at the end, for a person.
   So the column is named for what it actually holds. */
function laneColumns() {
  const stages = ((S.state.board || {}).stages || []).filter(s => s.slug !== 'done');
  return [...stages.map(s => ({ slug: s.slug, label: s.label, tint: STAGE_TINT[s.slug] })),
          { slug: 'merged', label: 'Merged in', tint: 'var(--calm)',
            note: 'on the phase branch' }];
}

/* Where one member is drawn: the stage it is actually in, unless the phase
   branch already holds it — then it is in the lane's last column whatever
   its card says, because within the phase that work has landed. A card in
   done/ is finished by any reading. */
function laneColumnOf(task, merged) {
  if (task.number && merged.has(canonicalNumber(task.number))) return 'merged';
  return task.stage === 'done' ? 'merged' : task.stage;
}

/* Every card the phase lists, in run order, each with the column it is
   drawn in. The card itself is the whole of what is known about it — this
   only says where in the lane it goes. A listed number that resolves to no
   card is not here: the phase card wears `phase drift` for that, as it
   already did. */
function laneMembers(task) {
  const merged = mergedIn(task);
  const members = [];
  (task.members || []).forEach((listed, index) => {
    const card = findTask(listed.file);
    if (!card) return;
    members.push({ pos: index + 1, task: card, column: laneColumnOf(card, merged) });
  });
  return members;
}

/* What the head says it has got through, counted off the same placement
   the columns draw by — so the head and the last column can never
   disagree about how far the phase has got. */
function laneProgress(members) {
  return { done: members.filter(m => m.column === 'merged').length,
           total: members.length };
}

function laneLog(task) {
  const entries = phaseLog(task);
  const lines = entries.map(entry =>
    `<div class="pline${/^halted\b/.test(entry.text) ? ' bad' : ''}">` +
    `<span class="stamp">${esc(entry.at)}</span>` +
    `<span class="what">${esc(entry.text)}</span></div>`).join('');
  return `<div class="plog"><div class="phead">Phase log` +
    `<span class="mono">${entries.length ? entries.length + ' in the order they were made'
                                         : 'nothing yet'}</span></div>` +
    (entries.length
      ? `<div class="plines" data-phase="${esc(task.file)}">${lines}</div>`
      : `<div class="pempty">The runner writes a line here for every decision it makes — ` +
        `a run started, a card started, a card merged, a halt.</div>`) +
    `</div>`;
}

/* One lane, built, or brought up to date in place when `el` is the lane a
   previous paint built (task 130). The head, the halt and the gate line
   are rebuilt only when what they draw changed; the log likewise; the
   columns are kept and their cards painted by key, through the same
   keyedCard() the Board uses — so a session event leaves every lane and
   every card in it exactly as it was. */
function laneFor(task, el) {
  const fresh = !el;
  if (fresh) el = document.createElement('section');
  // the lane's listeners act on the phase as the latest paint read it
  el.__task = task;
  const snap = (S.state.phases || {})[task.file];
  const members = laneMembers(task);
  const at = laneProgress(members);
  const halted = !!(snap && snap.halted);
  const running = !!(snap && snap.running);
  const id = cardId(task, S.state);
  const chosen = !!S.phaseSel && id === S.phaseSel;
  el.className = 'lane' + (halted ? ' halted' : running ? ' running' : '') + (chosen ? ' sel' : '');
  if (chosen) el.setAttribute('aria-current', 'true'); else el.removeAttribute('aria-current');
  el.dataset.phase = task.file;

  const branch = (snap && snap.branch) || 'phase/' + task.file.replace(/\.md$/, '');
  // the branch is only claimed to be missing when the runner has just
  // looked; without a pass to read, the name is offered without a verdict
  const uncut = !!(snap && !snap.started);
  const flight = phaseFlight(task);
  const head =
    `<div class="lhead">` +
    `<span class="mark${running ? ' breathing' : ''}" style="background:${
      halted ? 'var(--alarm)' : running ? 'var(--accent)' : STAGE_TINT[task.stage]}"></span>` +
    // the phase's address (task 134): selects this lane, and a new tab
    // takes it
    `<a class="lref" href="${esc(formatRoute({ view: 'phases', phase: id }))}" data-phaselink="${esc(id)}" ` +
      `title="This phase's address">${task.number ? '#' + esc(task.number) : esc(id)}</a>` +
    `<button class="ltitle" data-open="1" title="Open the phase card — its text, ` +
      `its list and its PR">${esc(task.title)}</button>` +
    `<span class="lprog">${at.total ? `${at.done} of ${at.total} merged`
                                    : 'no cards listed'} · ${esc(task.stage)}</span>` +
    `<button class="chip2" data-open="1" title="Open the phase card">card<span class="g2">↗</span></button>` +
    `<span class="chip2${uncut ? ' dim' : ''}" title="${uncut
      ? 'The phase branch has not been cut yet — running the phase cuts it from the newest main'
      : "The phase's integration branch: every member is branched from its tip and merged back into it"
      }">${esc(branch)}</span>` +
    `<button class="chip2" data-copy-link="${esc(formatRoute({ view: 'phases', phase: id }))}" ` +
      `aria-label="copy a link to this phase">Copy link</button>` +
    `<span class="lacts"></span></div>`;

  // the card the phase stopped on, named rather than left as a number
  const stoppedOn = halted && snap.haltedAt
    ? members.find(m => m.task.number &&
        canonicalNumber(m.task.number) === canonicalNumber(snap.haltedAt))
    : null;
  const halt = halted
    ? `<div class="lhalt"><span class="lglyph">✕</span><span class="lwhy">` +
      `<b>halted${snap.haltedAt ? ` at #${esc(snap.haltedAt)}` : ''}</b>` +
      `${stoppedOn ? ' · ' + esc(stoppedOn.task.title) : ''} — ` +
      `${esc(snap.haltedWhy || snap.halted)}</span>` +
      `<span class="lacts"></span></div>`
    : '';
  // a run in flight, in the same well the cards use for the same fact
  const well = !halted && flight
    ? `<div class="well"><span class="lead">·</span><span class="wbody">${esc(flight.line)}` +
      `${flight.idle ? '' : '<span class="caret">▌</span>'}</span></div>`
    : '';

  // What accepting *this* phase requires, which the lane owes a reader
  // for the same reason the halt is drawn at the top of it: every member
  // merged and green says nothing about the commit they were merged
  // into, and the one place that distinction can be learned before
  // somebody drags the card to done/ is here. Drawn only where a gate is
  // configured, and `ungated` is never drawn as a pass.
  const gate = snap && snap.gate && snap.gate.gated ? snap.gate : null;
  const gateLine = gate
    ? `<div class="lhalt"${gate.decision === 'blocked'
        ? '' : ' style="border-color:var(--line)"'}>` +
      `<span class="lglyph">${gateState(gate.decision)[1]}</span>` +
      `<span class="lwhy"><b>integration gate ${esc(gateState(gate.decision)[2])}` +
      `</b> — ${esc(gate.summary)}</span><span class="lacts"></span></div>`
    : '';

  const top = head + halt + gateLine + well;
  // what the lane's own controls draw depends on their armed windows too
  const topSig = [top, halted, running,
    ...['run again', 'hold'].map(l => phaseOf(S.acts, `${task.file}::${l}`))].join('\u0001');
  if (fresh) {
    el.innerHTML = top + `<div class="lstages" data-phase="${esc(task.file)}"></div>` + laneLog(task);
    el.__log = laneLog(task);
  } else if (el.__topSig !== topSig) {
    // the head, the halt and the gate line go, and their new copies come
    // in where they were — above the columns, which stay
    const stagesEl = el.querySelector('.lstages');
    while (el.firstElementChild && el.firstElementChild !== stagesEl) el.firstElementChild.remove();
    stagesEl.insertAdjacentHTML('beforebegin', top);
  }
  const log = fresh ? el.__log : laneLog(task);
  if (el.__log !== log) {
    const old = el.querySelector('.plog');
    if (old) old.remove();
    el.insertAdjacentHTML('beforeend', log);
    el.__log = log;
  }
  if (fresh || el.__topSig !== topSig) {
    el.__topSig = topSig;
    el.querySelectorAll('[data-open]').forEach(btn =>
      btn.addEventListener('click', () => showDetail(el.__task)));
    // choosing a lane by its address: selected here, written as one entry
    el.querySelectorAll('[data-phaselink]').forEach(a =>
      a.addEventListener('click', (e) => {
        if (!plainClick(e)) return;
        e.preventDefault();
        S.phaseSel = a.dataset.phaselink;
        renderLanes();
        syncRoute();
      }));

    // ▸ run again lives here and nowhere else: clearing a halt should mean
    // having read what caused it, and the reason is the line above it
    if (halted) {
      laneAction(el.querySelector('.lhalt .lacts'), task,
        { glyph: '▸', label: 'run again', confirm: 'run it again?', busy: 'starting…',
          title: 'Clear the halt and carry on from where it stopped — every card already '
               + 'merged into the phase branch stays merged',
          run: () => runPhase(task) });
    }
    // ‖ hold means here what it means everywhere: the run stops, the member
    // agent in flight stops with it, and nothing is unwound
    if (running || halted) {
      laneAction(el.querySelector('.lhead .lacts'), task,
        { glyph: '‖', label: 'hold', confirm: 'hold it?', busy: 'holding…',
          title: 'Stop the phase and the agent it has in flight — the phase branch, every '
               + 'card already merged into it and every worktree stay exactly as they are',
          run: () => holdPhase(task) });
    }
  }

  const stages = el.querySelector('.lstages');
  const columns = laneColumns();
  const slugs = columns.map(c => c.slug).join('|');
  if (stages.__cols !== slugs) {
    stages.innerHTML = '';
    for (const col of columns) {
      const box = document.createElement('div');
      box.className = 'lcol';
      box.appendChild(document.createElement('h3'));
      const cards = document.createElement('div');
      cards.className = 'lcards';
      box.appendChild(cards);
      // A member is drawn here and nowhere else (the Board hides it), so
      // its stage columns accept a drop exactly as the Board's do (task
      // 160): before this a member dragged in its lane had nowhere to
      // land, and the only way back for one moved by mistake was the API.
      // The move is the same request, held by the same server guards.
      // "Merged in" is the phase's own column, not a stage: no drop.
      if (col.slug !== 'merged') {
        box.dataset.stage = col.slug;
        box.addEventListener('dragover', (e) => { e.preventDefault(); box.classList.add('over'); });
        box.addEventListener('dragleave', () => box.classList.remove('over'));
        box.addEventListener('drop', (e) => {
          e.preventDefault();
          box.classList.remove('over');
          let carried;
          try { carried = JSON.parse(e.dataTransfer.getData('application/json')); }
          catch { return; }
          if (carried && carried.file && carried.from !== col.slug) {
            laneMove(carried.file, carried.from, col.slug);
          }
        });
      }
      stages.appendChild(box);
    }
    stages.__cols = slugs;
  }
  // a pool per lane: a card two phases both list (phase drift) is drawn in
  // each of their lanes, and each lane keeps its own node of it
  const pool = keyedPool(stages, 'article.card');
  const replaced = new Map();
  columns.forEach((col, i) => {
    const here = members.filter(m => m.column === col.slug);
    const box = stages.children[i];
    setHtml(box.firstElementChild, `<span class="tickmark" style="background:${col.tint}"></span>` +
      `<span>${esc(col.label)}</span><span class="count">${here.length}</span>` +
      `<span class="note">${esc(col.note || '')}</span>`);
    const cards = box.lastElementChild;
    // the cards are the cards: same builder, same actions, same chips —
    // including the ⟶ chip that says where each one sits in the run
    const nodes = here.map(m => keyedCard(pool, m.task, replaced));
    if (!here.length) nodes.push(emptyNote(cards, '—'));
    placeChildren(cards, nodes);
  });
  el.__replaced = replaced;
  return el;
}

/* A move made in a lane (task 160): the same request the Board's drop
   sends, refused in the server's words by the same guards. The board frame
   the move causes brings the new reading; nothing here guesses where the
   card went. */
async function laneMove(file, from, to) {
  const answer = await post('/api/move', { file, from, to });
  if (answer.ok) toast(`${file} → ${to}/`);
  else toast(answer.error || 'move failed', true);
  return answer.ok;
}

/* A phase's controls are card actions in every way that matters, so they
   go through the one arm-then-fire machine, keyed like every other. */
function laneAction(slot, task, act) {
  if (!slot) return;
  const btn = document.createElement('button');
  btn.innerHTML = `<span class="g">${act.glyph}</span>${actLabel(act.label, act.confirm, act.busy)}`;
  btn.title = act.title;
  wireAction(btn, `${task.file}::${act.label}`, act);
  slot.appendChild(btn);
}

/* A phase an address named that has no lane here (task 134): said above
   the lanes, never swapped for another phase — a number no card has, a
   card that is not a phase (with its own address), two cards sharing the
   number, or an identifier that is not one. */
function phaseNote(phases) {
  const note = $('#phasenote');
  if (!note) return;
  const id = S.phaseSel;
  const here = id && phases.some(t => cardId(t, S.state) === id);
  if (!id || here) { note.hidden = true; note.innerHTML = ''; return; }
  const found = findCard(id, S.state);
  const label = /^\d+$/.test(id) ? '#' + id : id;
  let say;
  if (!/^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/.test(id)) {
    say = `<b>That is not a phase address.</b> A phase is named by its card's number or file name.`;
  } else if (found.task) {
    say = `<b>${esc(label)} is a card, not a phase.</b> ` +
      `<a href="${esc(formatRoute({ card: cardId(found.task, S.state) }))}" data-route>Open the card</a>.`;
  } else if (found.ambiguous) {
    say = `<b>${found.ambiguous.length} cards carry the number ${esc(id)}.</b> Name the phase by its file instead.`;
  } else {
    say = `<b>No phase ${esc(label)} on this board.</b> No card in its five stages has that ` +
      `${/^\d+$/.test(id) ? 'number' : 'name'}; an archived phase has no lane.`;
  }
  note.innerHTML = say + ' Every phase on this board is below.';
  note.hidden = false;
}

function renderLanes() {
  const view = $('#view-phases'), wrap = $('#lanes');
  // where you were looking, taken before the wipe throws the nodes away:
  // the page scrolls down, each lane's stages scroll sideways, each log
  // scrolls in its own box
  // A lane is kept and brought up to date (task 130), so most paints move
  // nothing; but a lane that changes place, or a log that is drawn again,
  // is a node whose offsets the browser resets — so the marks are still
  // taken, and put back below.
  markScroll('v:phases', view);
  wrap.querySelectorAll('.lstages').forEach(el =>
    markScroll('v:lanestages:' + el.dataset.phase, el));
  wrap.querySelectorAll('.plines').forEach(el =>
    markScroll('v:phaselog:' + el.dataset.phase, el));
  const phases = lanePhases();
  phaseNote(phases);
  if (!phases.length) {
    // an empty grid of five columns would be a room pretending to be full
    setHtml(wrap, '<div class="empty">No phases yet. A phase is an ordinary card ' +
      'marked <b>Type: Phase</b> with a <code>## Cards</code> section listing the cards ' +
      'it runs, in the order they run.</div>');
  } else {
    holdFocus(wrap, () => {
      const lanes = new Map([...wrap.children]
        .filter(c => c.classList.contains('lane')).map(c => [c.dataset.phase, c]));
      const replaced = new Map();
      const want = phases.map(task => {
        const lane = laneFor(task, lanes.get(task.file));
        for (const [a, b] of lane.__replaced) replaced.set(a, b);
        return lane;
      });
      placeChildren(wrap, want);
      wrap.__html = null;
      return replaced;
    });
  }
  restoreScroll('v:phases', view);
  // an address that chose a lane brings it into view, once
  if (S.phaseScroll) {
    const sel = wrap.querySelector('.lane.sel');
    if (sel && view.classList.contains('on')) {
      S.phaseScroll = false;
      sel.scrollIntoView({ block: 'start' });
    }
  }
  wrap.querySelectorAll('.lstages').forEach(el =>
    restoreScroll('v:lanestages:' + el.dataset.phase, el));
  wrap.querySelectorAll('.plines').forEach(el => {
    // a log you have not touched opens on its newest line, which is the one
    // that says where the phase is; once you have scrolled it, that is where
    // it stays through every redraw
    const key = 'v:phaselog:' + el.dataset.phase;
    if (scrollMarks[key]) restoreScroll(key, el);
    else el.scrollTop = el.scrollHeight;
  });
  if (S.selected) {
    S.selected = allTasks().find(t => t.file === S.selected.file) || null;
    // as on the Board (task 134): the address follows, in place
    if (!S.selected) syncRoute({ replace: true });
  }
  renderDrawer();
}

export {
  canonicalNumber, laneAction, laneColumnOf, laneColumns, laneFor, laneLog,
  laneMembers, lanePhases, laneProgress, laneRank, mergedIn, PHASE_LOG_LINE,
  phaseLog, renderLanes
};
