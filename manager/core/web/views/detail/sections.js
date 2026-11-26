/* The detail view's sections. */
import { agentOnTask, loadState } from '../../app/data.js';
import { S } from '../../app/state.js';
import { fetch, post } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { wireFolds } from '../../lib/folds.js';
import { md } from '../../lib/md.js';
import { askAttrs } from '../../lib/panel.js';
import { syncRoute } from '../../lib/nav.js';
import { cardEntryHtml } from '../../lib/orchwords.js';
import { cardId, formatRoute, stemOf } from '../../lib/route.js';
import { resource, staleWords } from '../../lib/resource.js';
import { ago, duration } from '../../lib/time.js';
import { toast } from '../../lib/toast.js';
import { stackBlock } from '../board.js';
import { actLabel, wireAction } from '../card.js';
import { deliveryOf, deliveryStatusHtml, loadDeliveryFull, requirementsHtml } from '../delivery.js';
import { afterCardWrite, commentRowHtml, jobsByComment, newRequestKey, postCard, withoutDiscussion } from './discussion.js';
import { renderDrawer } from './drawer.js';
import { ENDED_WORDS, fetchEvidence, fetchProgress, progressOf } from '../progress.js';

/* ── the detail view's sections (task 81) ──────────────────────────────

   Overview leads with what the card is, where it stands and the next
   decision; Brief is the requirement; Acceptance, Reviews and Activity
   are the records. What a card's acceptance and findings *are* is
   computed by the server (`acceptance.py`) from the card's own records,
   bench's evidence and the runs' claims — this draws it and never infers
   a result from a lane, a clean exit or a green suite. Every word a
   person or an agent wrote is drawn through esc(). */
const DTABS = [['overview', 'Overview'], ['brief', 'Brief'], ['inputs', 'Inputs'],
               ['acceptance', 'Acceptance'], ['reviews', 'Reviews'], ['activity', 'Activity']];
const ACC_GLYPH = { accepted: '✓', 'needs-work': '✕', recheck: '↻', reported: '·', pending: '', 'not-verified': '?', decision: '!' };
// who an acceptance result came from, and who acts next, in words (task 190)
const ROLE_WORDS = { implementer: 'implementation agent', reviewer: 'independent review', person: 'a person' };
const OWNER_WORDS = { you: 'you', agent: 'the implementation agent', reviewer: 'the independent review' };
const STAGE_WORDS = { backlog: 'Backlog', 'to-do': 'To do', 'in-progress': 'In progress',
                      review: 'Review', done: 'Done' };

/* The Overview's Work base block (task 150): the orchestrator's decision
   about which code this card's work starts from, in the server's words —
   "Starting from main", "Building on #61's committed work" — with why,
   what it rests on and what withdrew it, folded under it. It draws the
   recorded decision only; nothing here fetches, decides or launches. */
function workBaseBlock(t) {
  const w = ((S.state.plans || {})[t.file] || {}).workBase;
  if (!w) return '';
  const a = w.accepted || {};
  const l = w.latest || {};
  const stale = l.decision === 'stale' || l.decision === 'blocked';
  const fact = (k, v) => v ? `<dt>${esc(k)}</dt><dd>${v}</dd>` : '';
  const mono = v => v ? `<span class="mono">${esc(v)}</span>` : '';
  return `<section class="workbase" aria-label="Work base">` +
    // a sub-heading of "Where it stands", where task 149 placed it
    `<h4 class="ovsub">Work base</h4>` +
    `<p class="wbhead" data-decision="${esc(l.decision || '')}"${stale ? ' role="status"' : ''}>` +
    `<b>${esc(w.headline)}.</b> ${esc((stale ? l.summary : a.summary) || '')}</p>` +
    (w.withdrawn ? `<p class="dnote">A new start is held: ${esc(w.withdrawn)}</p>` : '') +
    // kept open across a live redraw, as every other kept fold is
    `<details class="rpt" data-keep="workbase"${S.accOpen[t.file + ':workbase'] ? ' open' : ''}>` +
    `<summary>Refs, evidence and history</summary>` +
    `<dl class="dfacts">` +
    fact('Decision', esc(a.decision || l.decision || '') + (a.id ? ` · ${mono(a.id)}` : '')) +
    fact('Starts from', a.branch ? mono(a.branch) + (a.commit ? ` at ${mono(a.commit.slice(0, 12))}` : '') : '') +
    fact('PR targets', mono(a.prBase)) +
    fact('Normal base', mono(a.appBase)) +
    fact('Prerequisite', a.parent ? `#${esc(a.parent)}` + (a.parentPr ? ` · ${esc(a.parentPr)}` : '') : '') +
    fact('Not selected', esc(a.alternatives || l.alternatives || '')) +
    fact('Latest record', l.id && l.id !== a.id ? `${esc(l.decision)} (${esc(l.code || '')}) · ${mono(l.id)} — ${esc(l.summary || '')}` : '') +
    fact('Recorded', esc(a.at || l.at || '') + ' · observed by Bench, not agent-reported') +
    `</dl></details></section>`;
}

function drawerTab(t) {
  const want = S.dtab[t.file];
  return DTABS.some(([k]) => k === want) ? want : 'overview';
}

/* The first keyed block the reader can see in the open section, with
   where it sits — read before a redraw, matched after it. */
function drawerAnchor(body) {
  if (!body || !body.scrollTop) return null;
  const head = body.querySelector('.dsticky');
  const floor = body.getBoundingClientRect().top + (head ? head.offsetHeight : 0);
  for (const el of body.querySelectorAll('.dsec:not([hidden]) [data-keep]')) {
    const box = el.getBoundingClientRect();
    if (box.bottom > floor) return { keep: el.dataset.keep, top: box.top };
  }
  return null;
}

function dsec(key, tab, html) {
  return `<section class="dsec" id="dsec-${key}" role="tabpanel" aria-labelledby="dtab-${key}"` +
    ` tabindex="-1"${key === tab ? '' : ' hidden'}>${html}</section>`;
}

function dtabsHtml(t, tab, acc) {
  const count = {
    // a count once there is something on record — ids or a run's results;
    // a card nobody has reported on keeps a quiet tab, as before (task 190)
    acceptance: acc && acc.counts.total && (acc.tracked || (acc.reporting || []).length)
      ? `${acc.counts.accepted}/${acc.counts.total}` : '',
    reviews: acc && (acc.findingCounts.open + acc.findingCounts['fix-reported'])
      ? String(acc.findingCounts.open + acc.findingCounts['fix-reported']) : '',
    // required information that stops a launch, counted (task 132)
    inputs: (() => {
      const i = ((S.state || {}).information || {})[t.file];
      return i && (i.blocked + i.unchecked) ? String(i.blocked + i.unchecked) : '';
    })(),
  };
  return `<nav class="dtabs" role="tablist" aria-label="Sections of this card">` +
    DTABS.map(([k, word]) => `<button role="tab" id="dtab-${k}" data-tab="${k}" ` +
      `aria-controls="dsec-${k}" aria-selected="${k === tab}" tabindex="${k === tab ? 0 : -1}">` +
      `${word}${count[k] ? `<span class="n">${esc(count[k])}</span>` : ''}</button>`).join('') +
    `</nav>`;
}

/* The card split as the server splits it — and by the server
   (`taskfiles.card_parts`, task 125): the state carries `parts`, two
   offsets into the body in the units a JavaScript string counts, so the
   page slices the card and keeps no second copy of the rule. A card read
   without offsets (an older payload) is all brief: shown, never guessed
   apart. */
function cardSplit(t) {
  const body = String((t && t.body) || '');
  const p = (t && t.parts) || {};
  const at = (n, floor) => (Number.isInteger(n) ? Math.min(Math.max(n, floor), body.length) : null);
  const brief = at(p.brief, 0);
  const history = brief == null ? null : at(p.history, brief);
  if (brief == null || history == null) return { head: '', brief: body, history: '' };
  return { head: body.slice(0, brief), brief: body.slice(brief, history),
           history: body.slice(history) };
}

function whenOf(value) {
  if (value == null || value === '') return 0;
  if (typeof value === 'number') return value * 1000;
  const iso = String(value);
  const parsed = Date.parse(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso.replace(' ', 'T'));
  return Number.isFinite(parsed) ? parsed : 0;
}

function stampOf(value) {
  const ms = whenOf(value);
  return ms ? new Date(ms).toLocaleString() : 'time not recorded';
}

function accStamp(t) {
  const p = (S.state.progress || {})[t.file];
  const a = (S.state.acceptance || {})[t.file];
  return `${t.stage}:${t.mtime}:${p ? p.agent + ':' + (p.lastReport || '') + ':' + p.active : '-'}:` +
    `${a ? JSON.stringify(a) : '-'}`;
}

/* Acceptance, findings and reports, read on demand under lib/resource.js's
   policy (task 125): a forced read during one in flight — a decision just
   recorded — is queued, never dropped, and a failure keeps the last
   reading, marked stale. */
const acceptance = resource({
  store: S.acc,
  url: (file, t) => `/api/card/acceptance?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  ask: (url) => fetch(url),
});

function loadAcc(t, force) {
  acceptance.read(t.file, accStamp(t), {
    force, arg: t,
    done: () => { if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer(); },
  });
}

function progressFact(t) {
  const p = (S.state.progress || {})[t.file];
  const agent = agentOnTask(t.file);
  if (!p) return agent ? 'No progress was recorded for this run.' : 'No run on this card has reported progress.';
  if ((p.channel || {}).state !== 'available') {
    return `Reporting unavailable for the latest run: ${(p.channel || {}).why || 'it was given no progress channel'}.`;
  }
  const c = p.current;
  const said = c ? (c.doing || c.narrative || (c.done || [])[0] || '') : '';
  if (p.active) {
    if (!c) return 'The run is working and has sent no progress update yet.';
    return `agent-reported, ${ago(p.lastReport)} ago — ${said}${c.blocked ? ` · Blocked: ${c.blocked}` : ''}`;
  }
  // a finished run is history: its last words are never "doing now"
  return `The latest run ${ENDED_WORDS[p.outcome] || 'ended'}${p.closed ? ' ' + ago(p.closed) + ' ago' : ''}.` +
    (said ? ` It last said (history, not current): ${said}` : ' It sent no progress update.');
}

/* The latest run on the card, opened in the shared panel as a run to
   inspect (task 206): its identity, what it reported, its ending and the
   exact records — read, never relaunched. Absent where no run is known. */
function inspectRunButton(t) {
  const p = (S.state.progress || {})[t.file];
  const agent = agentOnTask(t.file);
  const id = (agent && agent.id) || (p && p.agent);
  if (!id) return '';
  return ` <button type="button" class="dlink ap-open"${askAttrs('run', { agent: id, card: t.file })}>Inspect this run</button>`;
}

function roundLine(r) {
  const word = { APPROVE: 'approved', 'REQUEST CHANGES': 'asked for changes' }[r.verdict] || 'gave no verdict';
  return `${r.reviewer || 'A reviewer'} ${word}${r.commit ? ' at ' + r.commit.slice(0, 12) : ''}, ${r.at}` +
    (r.label !== 'current-at-the-time' ? ` (${r.label})` : '');
}

/* What has happened since a review round: the PR updates filed after it
   and the findings opened against it, by state. An earlier request for
   changes is explained, never silently turned into approval. */
function sinceRound(r, acc) {
  const later = (acc.reports || []).filter(x => /^PR update/.test(x.kind) && whenOf(x.at) > whenOf(r.at));
  const mine = (acc.findings || []).filter(f => f.round === r.id);
  const bits = [];
  if (later.length) bits.push(`${later.length} PR update${later.length === 1 ? '' : 's'} since`);
  for (const [state, word] of [['open', 'open'], ['fix-reported', 'fix reported'], ['resolved', 'resolved']]) {
    const n = mine.filter(f => f.state === state).length;
    if (n) bits.push(`${n} finding${n === 1 ? '' : 's'} ${word}`);
  }
  return bits.join(' · ');
}

/* ── The Overview, ordered by a reader's questions (tasks 149, 186) ─────

   A busy reader opens a card to learn four things, and the Overview
   answers them in this order before anything else. Where the card stands
   leads (task 186): its status and next action are read before the work's
   narrative, which follows directly after it.

   1. Where does it stand, and what happens next? — `standsHtml`: the
      server's one delivery status (word, sentence, primary action and who
      holds it — task 99, never a cascade of this page's own), a failed
      run, what the latest run says it is doing, and the last recorded
      result, labelled as the run's own account.
   2. What is the work and why? — `workHtml`: excerpts of the brief as it
      reads now (`taskfiles.brief_outline`, served with the card's text),
      each labelled with the section it came from, and the way to the
      whole brief. Never a paraphrase, never persisted, never a model's.
   3. What is required? — `requiredHtml`: what the work needs (inputs,
      dependencies, criteria) apart from what a merge needs (the merge
      requirements, unchanged).
   4. What did the reviews find? — `reviewFactsHtml`.

   Supporting detail follows, quieter and folded where it is long: the
   demonstration, the stack, the signals, the phase's members and the
   branch plan. The card's tools are not here: by the owner's review they
   sit once at the top of the detail view, under its navigation, on every
   section (drawer.js). Nothing that was here is gone; it is placed. */
function overviewHtml(t, acc, failBlock, signals, plan, members) {
  const d = deliveryOf(t);
  const full = loadDeliveryFull(t, () => {
    if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer();
  });
  return standsHtml(t, d, acc, failBlock) +
    workHtml(t) +
    requiredHtml(t, d, full, acc) +
    reviewFactsHtml(t, acc) +
    `<div class="ovdetail" role="group" aria-labelledby="ov-more-h">` +
    `<h3 class="ovh" id="ov-more-h">More about this card</h3>` +
    resultHtml(t) +
    timingHtml(t) +
    stackBlock(t) + signals + members +
    (plan ? `<details class="rpt"${S.accOpen[t.file + ':plan'] ? ' open' : ''} data-keep="plan">` +
            `<summary>Branch and plan</summary><div style="padding:6px 10px">${plan}</div></details>` : '') +
    `</div>`;
}

function ovSection(key, title, inner, extra = '') {
  return `<section class="ovsec ov${key}" data-keep="ov-${key}" aria-labelledby="ov-${key}-h"${extra}>` +
    `<h3 class="ovh" id="ov-${key}-h">${title}</h3>${inner}</section>`;
}

const ovFact = (dt, dd, cls = '') => dd ? `<dt>${dt}</dt><dd${cls ? ` class="${cls}"` : ''}>${dd}</dd>` : '';

/* 2. What the work is: the brief's own words, quoted. The outline is read
   with the card's text, so it is never older than the brief the Brief
   section shows; while a newer revision is being read, it says so. */
function workHtml(listed) {
  const t = withDetail(listed);
  const entry = (listed && S.detail[listed.file]) || {};
  const go = `<button class="dlink" data-goto="brief">Read the full brief</button>`;
  if (!t) {
    if (entry.error && !entry.loading)
      return ovSection('work', 'The work', `<p class="dnote" role="alert">The brief could not be read — ` +
        `${esc(entry.error)}. Nothing is summarised in its place. ${go}</p>`);
    return ovSection('work', 'The work', `<p class="dnote" role="status" aria-busy="true">Reading the brief…</p>`);
  }
  const o = t.outline;
  if (!o) return ovSection('work', 'The work', `<p class="dnote">This board sent no outline of the brief. ${go}</p>`);
  const changed = !!(t.textRev && listed.rev && t.textRev !== listed.rev);
  const stale = changed
    ? `<p class="dnote dstale" role="status">This card has changed since this text was read; the newest text is ` +
      `being read, and the summary below is not current until it arrives.</p>` : '';
  const extra = changed ? ' data-stale="1"' : '';
  if (o.state === 'none') {
    return ovSection('work', 'The work', stale +
      `<p class="ovnone">The brief has no opening paragraph, problem section or scope section to quote, ` +
      `so there is no summary of it here — nothing is written in its place. ${go}</p>`, extra);
  }
  const more = (x) => x.clipped ? ` <span class="ovmore">…continues in the brief</span>` : '';
  const from = (x) => ` <span class="ovfrom">— from “${esc(x.from)}”</span>`;
  const parts = [];
  if (o.problem) parts.push(['Problem', esc(o.problem.text) + more(o.problem) + from(o.problem)]);
  if (o.scope) {
    parts.push(['Scope', `<ul class="ovscope">${o.scope.items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` +
      (o.scope.more ? `<span class="ovmore">and ${o.scope.more} more in the brief</span>` : '') + from(o.scope)]);
  }
  if (o.boundaries) parts.push(['Not in scope', esc(o.boundaries.text) + more(o.boundaries) + from(o.boundaries)]);
  const absent = [!o.lead && !o.problem ? 'opening paragraph or problem section' : '',
                  !o.scope ? 'scope section' : ''].filter(Boolean);
  return ovSection('work', 'The work', stale +
    (o.lead ? `<p class="ovlead">${esc(o.lead.text)}${more(o.lead)}</p>` : '') +
    (parts.length ? `<dl class="ovparts">${parts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>` : '') +
    (o.questions
      ? `<p class="ovq" role="note"><b>Open questions.</b> The brief lists ${o.questions.count} under ` +
        `“${esc(o.questions.from)}”. A work run is told to decline a card whose questions are open.</p>` : '') +
    (absent.length ? `<p class="dnote">The brief has no ${absent.join(' and no ')} to quote.</p>` : '') +
    `<p class="ovsrc">Quoted from the brief as it reads now (revision <span class="mono">${esc(o.rev)}</span>). ` +
    `The brief is the requirement; this is not a second copy of it. ${go}</p>`, extra);
}

/* The last result a run recorded on the card: its newest work report or
   PR update, by its own first paragraph — the run's account, said to be
   one, and never a check result. A card in review or done with none says
   the result is not stated rather than inventing one. */
function lastResultHtml(t, acc) {
  if (!acc) return 'reading…';
  const reports = (acc.reports || []).filter(r => !r.review && /^(work report|pr update)/i.test(r.kind || ''));
  const last = reports[reports.length - 1];
  if (!last) {
    return ['review', 'done'].includes(t.stage)
      ? `<span class="unknown">No work report is on record on this card, so its result is not stated here.</span>` : '';
  }
  const h = last.headline;
  return (h ? `${esc(h.text)}${h.clipped ? ' …' : ''}` : `<span class="unknown">The report has no opening paragraph.</span>`) +
    ` <span class="ovfrom">— ${esc(last.kind)}${last.by ? ' by ' + esc(last.by) : ''}, ${esc(last.at)}: ` +
    `the run's own account, not a check result</span> · <button class="dlink" data-goto="activity">read it in Activity</button>`;
}

/* 1. Where it stands and what happens next: the server's delivery
   reading leads, then the facts that say what is active, what failed and
   what was last recorded. */
function standsHtml(t, d, acc, failBlock) {
  return ovSection('now', 'Where it stands · what happens next',
    deliveryStatusHtml(t, d, 'ov') + failBlock +
    `<dl class="dfacts ovfacts">` +
    ovFact('Stage', esc(STAGE_WORDS[t.stage] || t.stage) + (t.assignee ? ` · held by ${esc(t.assignee)}` : '')) +
    ovFact('Progress', esc(progressFact(t)) + inspectRunButton(t)) +
    ovFact('Last result', lastResultHtml(t, acc)) +
    // the acceptance conclusion and who acts next — the very reading the
    // Acceptance section leads with (task 190), never worked out here
    ovFact('Acceptance', accOutcomeHtml(acc, true)) +
    // the card's lead time and what is in flight (task 165), from the
    // same reading as "Time on this card" further down
    ovFact('Time', timingFact(t)) +
    // which scope holds it and what waits on you, with the link into the
    // Orchestrator at this card (task 145) — absent while nothing does
    ovFact('Orchestration', cardEntryHtml(t, S.state, cardId(t, S.state))) +
    `</dl>` +
    // where the work starts (task 150): a held new start is a blocker at
    // the point of decision, so it sits here rather than under More
    workBaseBlock(t));
}

/* A dependency by number, with the stage it is in on this board — or
   said to be not on it. Never resolved by title. */
function dependencyHtml(n) {
  const want = String(n).replace(/^0+(?=\d)/, '');
  // the board's own listing, as the state carries it
  const tasks = ((S.state && S.state.board && S.state.board.stages) || []).flatMap(s => s.tasks || []);
  const dep = tasks.find(x => String(x.number || '').replace(/^0+(?=\d)/, '') === want);
  const href = esc(formatRoute({ card: want }));
  return `<a href="${href}" data-route>#${esc(n)}</a> ` +
    (dep ? `<span class="ovfrom">${esc(STAGE_WORDS[dep.stage] || dep.stage)}</span>`
         : `<span class="ovfrom">not on this board</span>`);
}

/* 3. What is required — to do the work, and, apart from it, to merge it.
   The merge requirements are the server's reading, as they always were. */
function requiredHtml(t, d, full, acc) {
  const link = (tab, text) => `<button class="dlink" data-goto="${tab}">${esc(text)}</button>`;
  // the criteria are a requirement of the work; where they stand is the
  // outcome under "Where it stands", one reading for both (task 190)
  const accFact = !acc ? 'reading…'
    : acc.counts.total
      ? `${acc.counts.total} criteri${acc.counts.total === 1 ? 'on' : 'a'} in the brief · ` +
        link('acceptance', 'see each result')
      : `<span class="unknown">The brief has no checklist criteria.</span>`;
  const deps = t.dependsOn || [];
  return ovSection('req', 'What is required',
    `<h4 class="ovsub">To do the work</h4><dl class="dfacts ovfacts">` +
    ovFact('Information', inputsFact(t)) +
    ovFact('Depends on', deps.length ? deps.map(dependencyHtml).join(' · ') : '') +
    ovFact('Acceptance', accFact) +
    `</dl>` +
    `<h4 class="ovsub">To merge it</h4>` + requirementsHtml(t, d, full, 'ov'));
}

/* 4. What the reviews found: open findings first, then the last review
   and the brief's own relevance review — each a link to the one copy in
   Reviews. */
function reviewFactsHtml(t, acc) {
  const link = (tab, text) => `<button class="dlink" data-goto="${tab}">${esc(text)}</button>`;
  const last = acc && acc.rounds.length ? acc.rounds[acc.rounds.length - 1] : null;
  const lastRel = acc && (acc.relevance || []).length ? acc.relevance[acc.relevance.length - 1] : null;
  const fc = acc ? acc.findingCounts : null;
  const findFact = !acc ? 'reading…'
    : !acc.findings.length ? (acc.rounds.length ? 'No findings recorded against the reviews.' : '')
    : `${link('reviews', `${fc.open} open`)} · ${fc['fix-reported']} fix reported` +
      `${fc['fix-reported'] ? ' — recheck and resolve, or reopen' : ''} · ${fc.resolved} resolved`;
  const facts = ovFact('Findings', findFact) +
    ovFact('Last review', last ? esc(roundLine(last)) +
      (sinceRound(last, acc) ? ` — ${esc(sinceRound(last, acc))}` : '') +
      ` · <button class="dlink" data-goreview="${esc(last.id)}">open this review</button>` : '') +
    // the brief's own review, apart from the code's: never an approval
    ovFact('Relevance', lastRel ? esc(relevanceLine(lastRel)) +
      ` · <button class="dlink" data-goreview="${esc(lastRel.id)}">open this review</button>` +
      ((acc.relevance || []).length > 1 ? ` · ${acc.relevance.length} on record` : '') : '');
  return ovSection('rev', 'Reviews and findings',
    (facts ? `<dl class="dfacts ovfacts">${facts}</dl>`
           : `<p class="dnote">No review has been filed on this card.</p>`) +
    (last ? `<p class="dnote">A reviewer's verdict is about the revision it names. GitHub's formal approval and Bench's own verdict are separate facts, in Signals below.</p>` : ''));
}

/* The card's text, read when the card is opened (task 129). The board list
   carries every field the server reads out of a card — and its revision,
   `rev` — but not the text, so the brief is asked for here, under
   lib/resource.js's race policy: keyed by the card's file, so a reply
   about one card never lands in another's; numbered, so a late reply
   never replaces a newer one; and asked again whenever the stage or the
   revision the list carries moves. The server answers by name *and*
   stage, and a card that moved, was archived or deleted is answered as
   such — then the board itself is read again, which moves the detail view
   with the card or closes it. */
const DETAIL_KEEP = 12;

const GONE_WORDS = {
  moved: (b) => `it moved to ${b.stage || 'another stage'}/ since the board was read`,
  archived: () => 'it was archived since the board was read',
  gone: () => 'it is no longer on the board',
};

const detail = resource({
  store: S.detail,
  url: (file, t) => `/api/card?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  accept: (ok, body) => {
    if (ok && body && body.card) return { data: body.card };
    const why = body && GONE_WORDS[body.code];
    // the list is behind the disk: read it again, and the drawer follows
    // the card to its new stage, or closes with it
    // (a failed reload is the state reader's to say; it is not this one's)
    if (why) Promise.resolve().then(() => loadState()).catch(() => {});
    return { error: why ? why(body) : ((body && body.error) || 'the board did not answer') };
  },
  ask: (url) => fetch(url),
});

function detailKey(t) {
  return `${t.stage}:${t.rev || ''}`;
}

/* ── Inputs and required tools (task 132) ──────────────────────────────

   The card's required information as the server reads it
   (`inputs.readiness`, the resolver every launch and the move into
   in-progress ask): the section written in the brief, every input and
   tool with its state, and what the brief names that the section does
   not. Read on demand under lib/resource.js's race policy, and again
   whenever the card's text or its state summary moves. Reading it
   retrieves nothing; Recheck information is the one action that does. */
const INPUT_CLASSES = [['required', 'Required'], ['optional', 'Optional'], ['non-input', 'Not an input']];
const TOOL_CLASSES = [['required', 'Required'], ['optional', 'Optional']];
const INPUT_TONE = { available: 'ok', blocked: 'bad', unchecked: '', optional: 'dim', 'not-needed': 'dim' };
const CARD_TONE = { ready: 'ok', 'needs-information': 'bad', checking: 'accent', unchecked: '', 'not-applicable': 'dim' };

const inputsRes = resource({
  store: S.inputs,
  url: (file, t) => `/api/card/inputs?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  ask: (url) => fetch(url),
});

function inputsStamp(t) {
  const i = ((S.state || {}).information || {})[t.file];
  return `${t.stage}:${t.mtime}:${t.rev || ''}:${i ? JSON.stringify(i) : '-'}`;
}

function loadInputs(t, force) {
  inputsRes.read(t.file, inputsStamp(t), {
    force, arg: t,
    done: () => { if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer(); },
  });
}

function inputRowHtml(t, r, i, kind) {
  const classes = kind === 'tool' ? TOOL_CLASSES : INPUT_CLASSES;
  const id = `inp-cls-${kind}-${i}`;
  const chosen = S.accDraft[id] || r.classification;
  const origin = { section: 'listed in the section', discovered: 'found in the brief',
                   supporting: `found in ${r.via || 'a supporting document'}` }[r.origin] || '';
  const meta = [
    r.classification === 'required' ? 'required' : r.classification === 'optional' ? 'optional' : 'not an input',
    origin,
    r.representation ? `${r.representation}` : '',
    r.checkedAt ? `checked ${stampOf(r.checkedAt)}` : (r.status === 'not-needed' ? '' : 'not checked'),
    r.revision || '',
  ].filter(Boolean).join(' · ');
  const controls = r.source
    ? `<span class="inpcls"><label for="${id}">Classify</label> <select id="${id}">` +
      classes.map(([v, w]) => `<option value="${v}"${v === chosen ? ' selected' : ''}>${w}</option>`).join('') +
      `</select> <button data-inp-save="${kind}:${i}">Save</button></span>`
    : '';
  return `<li class="inprow" data-keep="inp-${kind}-${i}" data-state="${esc(r.status)}">` +
    `<span class="cbadge ${INPUT_TONE[r.status] || ''}">${esc(r.statusLabel || r.status)}</span> ` +
    `<b class="mono">${esc(r.display || r.source)}</b>` +
    (r.label ? ` <span class="dim">“${esc(r.label)}”</span>` : '') +
    (r.note ? ` — ${esc(r.note)}` : '') +
    `<div class="dim">${esc(meta)}</div>` +
    (r.why && r.status !== 'available' ? `<div>${esc(r.why)}</div>` : '') +
    (r.remedy && r.status !== 'available' ? `<div><b>Fix:</b> ${esc(r.remedy)}</div>` : '') +
    (r.how ? `<div class="dim">${esc(r.how)}</div>` : '') +
    (r.delivery && r.status === 'available' ? `<div class="dim">Delivered as a ${esc(r.delivery)}.</div>` : '') +
    controls + `</li>`;
}

function inputsHtml(t) {
  // asked for as it is drawn, the way the Overview asks for its delivery
  // reading: once per stamp, and again when the card or its summary moves
  loadInputs(t);
  const entry = S.inputs[t.file] || {};
  const d = entry.data;
  if (!d) {
    return entry.error
      ? `<p class="dnote" role="alert">The card's inputs could not be read: ${esc(entry.error)} ` +
        `<button id="inpRetry">Try again</button></p>`
      : `<p class="dnote" aria-busy="true">Reading the card's inputs…</p>`;
  }
  const running = d.check && d.check.running;
  const last = d.check && d.check.last;
  const alarm = d.state === 'needs-information';
  const head = `<div class="inphead" data-keep="inp-head">` +
    `<h3>Inputs and required tools <span class="cbadge ${CARD_TONE[d.state] || ''}" id="inpState">${esc(d.label)}</span></h3>` +
    `<p ${alarm ? 'role="alert"' : 'role="status"'}>${esc(d.sentence)}</p>` +
    (d.remedy && d.state !== 'ready' ? `<p><b>Fix:</b> ${esc(d.remedy)}</p>` : '') +
    `<p><button id="inpRecheck"${running ? ' disabled aria-disabled="true"' : ''}>` +
    `${running ? 'Checking…' : 'Recheck information'}</button> ` +
    `<span class="dim">${last ? `Last check ${esc(stampOf(last.at))} — ${esc(last.outcome)}.` : 'No check has run on this board.'} ` +
    `Checked for the ${esc(d.run.intent)} run on ${esc(d.run.harness || 'the default harness')}` +
    ` (reads ${esc((d.run.representations || []).join(', ') || 'nothing declared')}).</span></p>` +
    (entry.stale ? `<p class="dnote" role="status">${esc(staleWords(entry))}</p>` : '') +
    `</div>`;
  // what this reading does and does not prove, and how to write the
  // section: reference, one fold away rather than repeated above the
  // list (task 149)
  const about = `<details class="rpt" data-keep="inp-about"${S.accOpen[t.file + ':inp-about'] ? ' open' : ''}>` +
    `<summary>What this does and does not prove</summary><div style="padding:6px 10px">` +
    `<p>Information readiness is one thing a launch checks. Ready here is not launch readiness ` +
    `(the plan, ownership and the launch check) and not merge readiness (the merge requirements) — the Overview says those.</p>` +
    `<p>${esc(d.distinctions)}</p><p>${esc(d.coverage)}</p>` +
    `<p>Write an entry as <code>- input, required: ../../reference/spec.md — why</code> or ` +
    `<code>- tool, required: check unit</code>. Tools bench can verify: <code>check &lt;name&gt;</code>, ` +
    `<code>command &lt;prefix&gt;</code>, <code>capability &lt;word&gt;</code>, <code>image-reading</code>, <code>pdf-reading</code>.</p>` +
    `</div></details>`;
  const section = d.section || {};
  const empty = !d.inputs.length && !d.tools.length && !section.present &&
    !(section.problems || []).length && !(d.proposed || []).length;
  if (empty) {
    // nothing named and nothing to classify: one statement, not three
    // headings each saying "none" (task 149)
    return head + (d.limits || []).map(l => `<p class="dnote" role="status">${esc(l)}</p>`).join('') +
      `<p class="dnote">This card has no “Inputs and required tools” section, and its brief names no ` +
      `supporting input or tool — there is nothing to list.</p>` + about;
  }
  const notes = (!section.present
    ? `<p class="dnote">This card has no “Inputs and required tools” section. ` +
      (d.inputs.length ? 'The references below were found in the brief; each counts as required until classified, and classifying one writes the section.'
                       : 'The brief names no supporting input or tool.') + `</p>`
    : '') +
    (section.problems || []).map(p => `<p class="dnote" role="alert">${esc(p)}</p>`).join('') +
    (d.proposed && d.proposed.length && section.present
      ? `<p class="dnote">${d.proposed.length} reference${d.proposed.length === 1 ? '' : 's'} in the brief ` +
        `${d.proposed.length === 1 ? 'is' : 'are'} not listed in the section — proposed below, required until classified.</p>` : '') +
    (d.limits || []).map(l => `<p class="dnote" role="status">${esc(l)}</p>`).join('');
  const list = (rows, kind) => rows.length
    ? `<ul class="inplist" aria-label="${kind === 'tool' ? 'Required tools' : 'Inputs'}">` +
      rows.map((r, i) => inputRowHtml(t, r, i, kind)).join('') + `</ul>`
    : `<p class="dnote">${kind === 'tool' ? 'No tools are listed.' : 'No inputs.'}</p>`;
  return head + notes +
    `<h4>Inputs</h4>` + list(d.inputs, 'input') +
    `<h4>Tools</h4>` + list(d.tools, 'tool') + about;
}

function wireInputs(t) {
  const body = $('#drawerbody');
  const retry = body.querySelector('#inpRetry');
  if (retry) retry.addEventListener('click', () => { loadInputs(t, true); renderDrawer(); });
  const recheck = body.querySelector('#inpRecheck');
  if (recheck) recheck.addEventListener('click', async () => {
    recheck.disabled = true;
    const data = await postCard('/api/card/inputs/check', { file: t.file, stage: t.stage });
    if (data) toast(data.joined ? 'a check is already running — joined it' : 'checking the card\'s information');
    loadInputs(t, true);
    loadState();
  });
  const d = (S.inputs[t.file] || {}).data;
  body.querySelectorAll('[data-inp-save]').forEach(btn => btn.addEventListener('click', async () => {
    if (!d) return;
    const [kind, index] = btn.dataset.inpSave.split(':');
    const row = (kind === 'tool' ? d.tools : d.inputs)[Number(index)];
    const select = document.getElementById(`inp-cls-${kind}-${index}`);
    if (!row || !select) return;
    btn.disabled = true;
    // an input is named by its identity: the source the page is sent is
    // the safe form (a URL without its query), which names another document
    const named = kind === 'input' && row.identity ? { identity: row.identity } : { source: row.source };
    const data = await postCard('/api/card/inputs/classify', {
      file: t.file, stage: t.stage, briefSha: d.briefSha, kind,
      ...named, classification: select.value, note: row.note || '' });
    if (data) {
      delete S.accDraft[select.id];
      toast(`${row.display} classified ${select.value}` + (data.committed ? '' : ' — written to the card, not committed'));
    }
    loadInputs(t, true);
    loadDetail(t, true);
    loadState();
  }));
}

/* ── Show the result (task 133) ─────────────────────────────────────────

   What the card delivered and what shows it, as the server reads it
   (`results.reading`): the card's own request (Off, Requested or Required
   for handoff), the outputs bench kept from checks that declare them —
   each with how far it can be trusted — the checks bench ran, acceptance
   counts and what is known of merged, installed and deployed. Reading it
   runs nothing; the only writes are the setting (into the brief), "Not
   now" on the suggestion and withholding one output. Every word a program
   or a person wrote is drawn through esc(); an image is an <img> of the
   board's own nosniff URL under a restrictive policy, and anything active
   is a download. */
const RES_TONE = { neutral: '', calm: 'ok', alarm: 'bad', working: 'accent' };
const RES_MODES = [['off', 'Off'], ['requested', 'Requested'], ['required', 'Required for handoff']];
const RES_SHORTCUTS = [['screenshots', 'Screenshots', 'Screenshots of the changed screen'],
                       ['output', 'Example output', 'Example output from the changed behaviour'],
                       ['before-after', 'Before/after', 'Before/after screenshots of the changed screen']];
const FACT_WORDS = { implemented: 'Implemented', merged: 'Merged', installed: 'Installed', deployed: 'Deployed' };

const resultRes = resource({
  store: S.result,
  url: (file, t) => `/api/card/result?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  ask: (url) => fetch(url),
});

function resultStamp(t) {
  const r = ((S.state || {}).results || {})[t.file];
  return `${t.stage}:${t.mtime}:${t.rev || ''}:${r ? JSON.stringify(r) : '-'}`;
}

function loadResult(t, force) {
  resultRes.read(t.file, resultStamp(t), {
    force, arg: t,
    done: () => { if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer(); },
  });
}

function shortSha(s) { return s ? String(s).slice(0, 12) : 'unknown'; }

/* ── Time on this card (task 165) ──────────────────────────────────────

   The server's cycle-time reading (`cycletime.py`, GET /api/card/timing),
   drawn and never recomputed: every duration on this block is a number
   the reading carries. The categories overlap — a check runs inside a
   run, which runs while the card sits in a stage — so they are listed as
   separate measurements and the only whole is the lead time. An absent
   number reads "none recorded", never 0s, and every unknown and clock
   anomaly the server found is listed in its words. */
const timingRes = resource({
  store: S.timing,
  url: (file, t) => `/api/card/timing?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  ask: (url) => fetch(url),
});

function timingStamp(t) {
  // asked again when the card or its latest run changes — never on a
  // clock, so a timer cannot redraw the drawer under a reader; the block
  // says the time it was read, and a running interval is "so far" as of then
  const p = (S.state.progress || {})[t.file];
  const live = (S.state.agents || []).filter(a => a.task === t.file).map(a => a.id + a.status).join(',');
  return `${t.stage}:${t.mtime}:${p ? p.agent + ':' + (p.lastReport || '') + ':' + p.active : '-'}:${live}`;
}

/* A reading that lands redraws its own block and its one-line fact in
   place, and nothing else: a whole-drawer redraw here replaced the nodes a
   reader was in the middle of using — a fold being toggled, a section
   being opened. Clicks inside are delegated (wireTiming, drawer.js). */
function patchTiming(t) {
  if (!(S.selected && S.selected.file === t.file && !S.fileView)) return;
  const sec = document.querySelector('#dsec-overview .cycletime');
  if (sec) sec.outerHTML = timingHtml(t);
  const fact = document.querySelector('#dsec-overview .ctfact');
  if (fact) fact.outerHTML = timingFact(t);
}

function loadTiming(t, force) {
  timingRes.read(t.file, timingStamp(t), { force, arg: t, done: () => patchTiming(t) });
}

const CT_STAGE = { ...STAGE_WORDS, archive: 'Archive' };
const ctDur = (s) => duration(s) || 'none recorded';
const ctAt = (ts) => ts ? new Date(ts * 1000).toLocaleString() : 'time not recorded';

function ctActiveHtml(d) {
  if (!(d.active || []).length) return '';
  return `<ul class="ctactive" role="status">` + d.active.map(a => {
    if (a.kind === 'check') {
      return `<li><b>Checking now:</b> ${esc(a.name)}${a.scope ? ` (${esc(a.scope)})` : ''}` +
        ` · attempt ${esc(a.attempt)} · ${esc(a.phase || 'running')} · ${esc(ctDur(a.elapsed))} so far` +
        ` · <button class="dlink" data-dview="evidence">${esc(a.next.label)}</button></li>`;
    }
    const rep = a.lastReport;
    return `<li><b>${esc(a.name)} working</b> · ${esc(ctDur(a.elapsed))} so far` +
      `<br><span class="ctsrc">Last progress report (agent-reported, not verified): ` +
      (rep ? `${esc(ctDur(a.reportAge))} ago${rep.doing ? ' — ' + esc(rep.doing) : ''}` +
             `${rep.blocked ? ' · Blocked: ' + esc(rep.blocked) : ''}` : 'none sent') +
      `.</span><br><span class="ctsrc">Last tool activity (observed by Bench — activity, not progress): ` +
      (a.activityAge != null ? `${esc(ctDur(a.activityAge))} ago` : 'none observed') +
      ` · process ${esc(a.process || 'unknown')}.</span>` +
      `<br><span class="ctsrc">Next: ${esc(a.next.label)} on the card — ${esc(a.next.why)}.</span></li>`;
  }).join('') + `</ul>`;
}

function ctFold(t, key, summary, inner) {
  return `<details class="rpt" data-keep="${key}"${S.accOpen[`${t.file}:${key}`] ? ' open' : ''}>` +
    `<summary>${summary}</summary><div class="ctfold">${inner}</div></details>`;
}

function timingHtml(t) {
  loadTiming(t);
  const entry = S.timing[t.file] || {};
  const d = entry.data && entry.data.timing;
  const head = `<h3 id="ct-h">Time on this card</h3>`;
  if (!d) {
    return `<section class="cycletime" data-keep="timing" aria-labelledby="ct-h">${head}` +
      (entry.error ? `<p class="dnote" role="alert">The timing could not be read: ${esc(entry.error)} ` +
                     `<button id="ctRetry">Try again</button></p>`
                   : `<p class="dnote" aria-busy="true">Reading the timing…</p>`) + `</section>`;
  }
  const lead = d.lead || {};
  const leadWords = lead.seconds == null
    ? `<span class="unknown">Lead time not known</span> — see what is not recorded below.`
    : `<b id="ctLead">${esc(duration(lead.seconds))}</b> ` +
      (lead.open ? `so far — still open, in ${esc(CT_STAGE[d.stage] || d.stage)}` : `from first record to Done`) +
      `${lead.reopened ? ` · reopened ${esc(lead.reopened)}×` : ''}.` +
      ` <span class="ctsrc">Started ${esc(ctAt((lead.start || {}).at))}` +
      ` (${lead.start && lead.start.how === 'created' ? 'created' : 'first recorded move'}).</span>`;
  const cats = (d.categories || []).map(c =>
    `<li data-ct="${esc(c.key)}"${c.key === 'lead' ? ' class="ctlead"' : ''}>` +
    `<span class="ctlabel">${esc(c.label)}</span>` +
    `<span class="ctval${c.recorded ? '' : ' unknown'}">${esc(ctDur(c.seconds))}` +
    `${c.open && c.recorded ? ' <span class="cbadge accent">running</span>' : ''}` +
    `${c.summed != null && c.summed !== c.seconds ? ` <span class="ctsrc">(${esc(duration(c.summed))} summed across checks)</span>` : ''}</span>` +
    `<span class="ctnote">${esc(c.note)}</span></li>`).join('');
  const stages = (d.stages || []).length
    ? `<ol class="ctline">` + d.stages.map(s =>
      `<li><b>${esc(CT_STAGE[s.stage] || s.stage)}</b> · ${esc(ctAt(s.from))} → ` +
      `${s.open ? 'now' : esc(ctAt(s.to))} · ${s.seconds == null ? '<span class="unknown">duration unknown</span>' : esc(duration(s.seconds))}` +
      `${s.open ? ' <span class="cbadge accent">here now</span>' : ''}` +
      ` <span class="ctsrc">${esc(s.how === 'created' ? 'created' : s.how === 'automated' ? 'moved by the board' : 'moved')}` +
      `${s.commit ? ' · ' + esc(s.commit) : ''}${s.source === 'journal' ? ' · uncommitted move' : ''}</span></li>`).join('') + `</ol>`
    : `<p class="dnote">No stage move of this card is on record.</p>`;
  const runs = (d.runs || []).length
    ? `<ul class="ctruns">` + d.runs.map(r =>
      `<li><b>${esc(r.label)}</b> <span class="mono">${esc(r.agent)}</span> · ${esc(ctAt(r.opened))} · ` +
      (r.ended === 'running' ? `${esc(ctDur(r.seconds))} so far <span class="cbadge accent">running</span>`
       : r.ended === 'unobserved' ? `<span class="unknown">ending not observed — the board restarted</span>`
       : `${esc(ctDur(r.seconds))} · ${esc(r.outcome || 'ended')}`) +
      `<br><span class="ctsrc">${esc(r.checks.count)} check request${r.checks.count === 1 ? '' : 's'}` +
      `${r.checks.seconds ? `, ${esc(duration(r.checks.seconds))} with a check in flight` : ''}` +
      `${r.outsideChecks != null ? ` · ${esc(duration(r.outsideChecks))} outside checks (not measured coding time)` : ''}</span></li>`).join('') + `</ul>`
    : `<p class="dnote">No work, repair or review run of this card is on record on this board.</p>`;
  const v = d.verification || {};
  const checks = (v.records || []).length
    ? `<p class="ctsrc">${esc(ctDur(v.seconds))} with at least one check in flight (union); ` +
      `${esc(ctDur(v.summed))} summed over every check, ${esc(ctDur(v.overlapped))} of it overlapping; ` +
      `${esc(ctDur(v.execution))} in check processes, ${esc(ctDur(v.preparation))} preparing.` +
      `${v.reused ? ` ${esc(v.reused)} request${v.reused === 1 ? ' was' : 's were'} answered by an existing record and ran nothing.` : ''}</p>` +
      `<ul class="ctchecks">` + v.records.map(e =>
        `<li><b>${esc(e.check)}</b>${e.scope ? ` (${esc(e.scope)})` : ''} · attempt ${esc(e.attempt)} · ` +
        `${esc(e.live ? (e.phase || 'running') : (e.outcome || 'unknown'))} · ` +
        (e.ranNothing ? 'ran nothing' : e.seconds == null ? '<span class="unknown">duration unknown</span>'
          : `${esc(duration(e.seconds))}${e.execution != null ? ` (check ${esc(duration(e.execution))}` +
            `${e.preparation ? `, preparing ${esc(duration(e.preparation))}` : ''})` : ''}`) +
        `<br><span class="ctsrc">requested ${esc(ctAt(e.requested))}` +
        `${e.commit ? ' · at ' + esc(e.commit) : ''}${e.agent ? ' · for ' + esc(e.agent) : e.actor ? ' · by ' + esc(e.actor) : ''}` +
        ` · <button class="dlink mono" data-dview="evidence" title="Open the ◫ evidence sheet">${esc(e.id)}</button></span></li>`).join('') + `</ul>`
    : `<p class="dnote">Bench has run no check for this card.</p>`;
  const ig = d.integration;
  const integration = ig
    ? `<p class="ctsrc ctint">Phase #${esc(ig.phase)}: ` +
      (ig.integratedAt ? `merged into the phase branch ${esc(ctAt(ig.integratedAt))} (phase log, to the minute)` : 'not yet merged into the phase branch') +
      ` · ${ig.deliveredAt ? `delivered to the application base ${esc(ctAt(ig.deliveredAt))} (${esc(ig.deliveredHow)})` : 'not yet delivered to the application base'}. ` +
      `${esc(ig.note)}</p>` : '';
  const gaps = [...(d.unknown || []).map(u => `<li><b>Not recorded:</b> ${esc(u.what)} — ${esc(u.why)}</li>`),
                ...(d.anomalies || []).map(a => `<li><b>Clock or order anomaly</b> (${esc(a.code)}): ${esc(a.detail)}</li>`)];
  return `<section class="cycletime" data-keep="timing" aria-labelledby="ct-h">${head}` +
    `<p class="ctheadline">Lead time: ${leadWords}</p>` +
    ctActiveHtml(d) + integration +
    `<p class="ctsrc">These measurements overlap — a check runs inside a run, a run inside a stage — so they are ` +
    `not parts of one total. Only the lead time measures the whole ticket.</p>` +
    `<ul class="ctcats" aria-label="Time by category">${cats}</ul>` +
    (gaps.length ? `<ul class="ctgaps">${gaps.join('')}</ul>` : '') +
    ctFold(t, 'ct-stages', `Stage timeline (${esc((d.stages || []).length)})`, stages) +
    ctFold(t, 'ct-runs', `Runs (${esc((d.runs || []).length)})`, runs) +
    ctFold(t, 'ct-checks', `Checks and attempts (${esc((v.records || []).length)})`, checks) +
    `<p class="ctsrc">Read from this board's own records: stage moves (git and the move journal), each run's ` +
    `opening and the ending the board observed, and Bench's evidence records, as of ` +
    `${esc(ctAt(d.now))}. Local to this board; reading it ran and wrote nothing. ` +
    `<button class="dlink" id="ctRefresh">Read again</button></p></section>`;
}

/* The one-line Time fact under "Where it stands": the lead time and
   what is in flight, from the same reading as the block below. */
function timingFact(t) {
  const d = ((S.timing[t.file] || {}).data || {}).timing;
  if (!d) return `<span class="ctfact unknown">reading…</span>`;
  const lead = d.lead || {};
  const now = (d.active || []).map(a => `${a.kind === 'check' ? 'checking ' + a.name : a.name}` +
    ` ${duration(a.elapsed) || ''}`.trimEnd());
  return `<span class="ctfact">` +
    (lead.seconds == null ? '<span class="unknown">lead time not known</span>'
    : `${esc(duration(lead.seconds))} lead time${lead.open ? ' so far' : ''}`) +
    (now.length ? ` · now: ${esc(now.join('; '))}` : '') +
    ` · <button class="dlink" data-ct-open="1">where the time went</button></span>`;
}

/* `where` names the list an output is drawn in ('top' for the curated
   selection and its pairs, 'all' for the fold of every output): one output
   may be drawn in both, and each drawing's ids and kept places are its own. */
function artifactHtml(t, a, i, where = 'top') {
  const caption = a.caption || a.name || 'output';
  const badge = a.current ? `<span class="cbadge ok">Current</span>`
    : `<span class="cbadge ${a.state === 'available' ? '' : 'dim'}">${esc((a.relevanceWords || [])[0] || a.stateWords)}</span>`;
  let media;
  if (a.url && a.kind === 'image') {
    media = `<button class="resthumb" data-res-view="${i}" aria-label="View full size: ${esc(caption)}">` +
      `<img src="${esc(a.url)}" alt="${esc(caption)}" loading="lazy"></button>`;
  } else if (a.url && (a.kind === 'text' || a.kind === 'json')) {
    media = `<p><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">Open ${esc(a.name)} as text</a>` +
      ` · <a href="${esc(a.url)}&download=1" download>Download</a></p>`;
  } else if (a.url) {
    media = `<p><a href="${esc(a.url)}&download=1" download>Download ${esc(a.name)}</a>` +
      (a.active ? ` <span class="dim">— active content (HTML, SVG or script): download only, never opened here</span>` : '') + `</p>`;
  } else {
    media = `<p class="dim">${esc(a.stateWords)}` +
      (a.withheld ? ` — ${esc(a.withheld.reason)}` : '') +
      (a.expired ? ` — ${esc(a.expired.why)}` : '') + `</p>`;
  }
  const p = a.producer || {};
  const src = a.source || {};
  const meta = [
    `commit ${shortSha(src.commit)}${src.dirty ? ' with uncommitted work' : ''}`,
    p.check ? `check ${p.check} — ${p.outcome || 'unknown'}` : '',
    a.capturedAt ? `captured ${stampOf(a.capturedAt)}` : '',
    a.viewport ? `viewport ${a.viewport}` : '',
    a.data && a.data !== 'unknown' ? `data: ${a.data}` : 'data: not stated',
    a.environment ? `environment: ${a.environment}` : '',
    a.role && a.role !== 'single' ? a.role : '',
  ].filter(Boolean).join(' · ');
  const depicts = a.depicts || {};
  const open = S.accOpen[`${t.file}:res-${a.id}`];
  const uid = `${where}-${i}`;
  const evLink = p.evidence
    ? `<button class="dlink mono" data-dview="evidence" title="Open the ◫ evidence sheet">${esc(p.evidence)}</button>`
    : `<span class="mono">unknown</span>`;
  return `<li class="resitem" data-keep="res-${where}-${esc(a.id)}"><figure>` + media +
    `<figcaption>${badge} <b>${esc(caption)}</b>` +
    (a.scenario ? `<div>Scenario: ${esc(a.scenario)}</div>` : '') +
    (a.criterion ? `<div>Criterion: <span class="mono">${esc(a.criterion)}</span>` +
      (a.criterionText ? ` — ${esc(a.criterionText)}` : '') + `</div>` : '') +
    `<div class="dim">${esc(meta)}</div>` +
    ((a.relevanceWords || []).length && !a.current
      ? `<div class="dim">${a.relevanceWords.map(esc).join('; ')}</div>` : '') +
    (a.described === false
      ? `<div class="dim resundescribed">The program did not describe this output: what it shows, its data and whether it is of this checkout or an already running service are not stated.</div>` : '') +
    `</figcaption></figure>` +
    `<details class="rpt" data-keep="res-d-${where}-${esc(a.id)}"${open ? ' open' : ''} data-res-fold="${esc(a.id)}">` +
    `<summary>Provenance</summary><div style="padding:6px 10px">` +
    `<p>Bench recorded: the run ${evLink}, its check and outcome, and the source it ran against (commit ${esc(shortSha(src.commit))}${src.bound ? '' : ', not bound'}). ` +
    `The output's hash is <span class="mono">${esc(shortSha(a.sha256))}</span>, ${esc(String(a.size))} bytes, ${esc(a.kind)}.</p>` +
    `<p>The program's account: it shows revision ${esc(shortSha(depicts.revision))}` +
    `${depicts.claimed ? ' (stated by the program)' : ''}; target ${esc(a.target || 'unknown')}. ` +
    `Bench did not confirm which build it depicts.</p>` +
    (a.kind === 'image' ? `<p class="dim">Images are kept as they were made; their pixels are not inspected for secrets.</p>` : '') +
    (a.scrubbed ? `<p class="dim">Credential-shaped text was scrubbed from this output when it was kept.</p>` : '') +
    (a.state === 'available'
      ? `<p><label for="res-why-${uid}">Withhold this output</label> <input id="res-why-${uid}" data-res-why="${i}" placeholder="why — e.g. shows personal data" ` +
        `value="${esc(S.accDraft[`res-why:${a.id}`] || '')}"> <button data-res-withhold="${i}">${actLabel('Withhold', 'Confirm: withhold', 'Withholding…')}</button> ` +
        `<span class="dim">Removes its bytes and description for good; the record that it existed stays. Click twice to confirm.</span></p>` : '') +
    `</div></details></li>`;
}

function resultSettingHtml(t, d) {
  const s = d.setting || {};
  const key = (k) => `res-${k}:${t.file}`;
  const mode = S.accDraft[key('mode')] || s.mode || 'off';
  const want = S.accDraft[key('want')] !== undefined ? S.accDraft[key('want')] : (s.want || '');
  const expects = S.accDraft[key('expects')] || s.expects || 'any';
  const agree = S.accDraft[key('agree')] !== undefined ? S.accDraft[key('agree')] : !!s.agreedNow;
  const preview = S.accDraft[key('preview')];
  const open = S.accOpen[`${t.file}:res-edit`] || t.stage === 'done' && preview;
  const radios = RES_MODES.map(([v, w]) =>
    `<label><input type="radio" name="res-mode-${esc(t.file)}" value="${v}"${v === mode ? ' checked' : ''} data-res-mode> ${w}</label>`).join(' ');
  const shortcuts = RES_SHORTCUTS.map(([v, w]) =>
    `<button type="button" data-res-shortcut="${v}"${v === expects ? ' aria-pressed="true"' : ' aria-pressed="false"'}>${w}</button>`).join(' ');
  return `<details class="rpt" data-keep="res-edit" data-res-fold="edit"${open ? ' open' : ''}>` +
    `<summary>${t.stage === 'done' ? 'Request a demonstration' : 'Edit Show the result'}</summary>` +
    `<div style="padding:6px 10px" class="ressetting">` +
    `<fieldset><legend>Show the result</legend>${radios}</fieldset>` +
    `<p class="dim">Off: no extra demonstration work and nothing missing. Requested: the run is asked to capture something useful and to say what it could not; nothing is held. ` +
    `Required for handoff: the agreed output must exist for the current commit before the card goes to review.</p>` +
    (mode !== 'off'
      ? `<p><label for="res-want">What would you like to see?</label><br>` +
        `<input id="res-want" class="reswant" maxlength="300" placeholder="e.g. Before/after screenshots of the mobile settings screen" value="${esc(want)}"></p>` +
        `<p>${shortcuts}</p>` +
        (mode === 'required'
          ? `<p><label><input type="checkbox" id="res-agree"${agree ? ' checked' : ''}> This is the agreed output to show</label> ` +
            `<span class="dim">Required enforces nothing until a concrete output is agreed. It grants the run no new capability.</span></p>` : '') +
        (mode === 'requested' && !want ? `<p class="dim">Left empty, the run chooses a sensible demonstration and says why.</p>` : '')
      : '') +
    (preview ? `<div role="status">${preview.notes.map(n => `<p>${esc(n)}</p>`).join('')}</div>` : '') +
    `<p>` + (t.stage === 'done' && !preview
      ? `<button id="resPreview">Check what this would need</button>`
      : `<button id="resSave">Save</button>`) +
    ` <span class="dim">Saving writes the card's brief. It starts no capture, check or agent${t.stage === 'done' ? ' and does not reopen the card' : ''}.</span></p>` +
    (d.activeRun ? `<p class="dnote" role="status">${esc(d.activeRun.name || 'An agent')} is working on this card and keeps the instructions and authority it launched with; a change here reaches the next run.</p>` : '') +
    `</div></details>`;
}

function resultHtml(t) {
  loadResult(t);
  const entry = S.result[t.file] || {};
  const d = entry.data;
  if (!d) {
    return `<section class="showresult" data-keep="result" aria-labelledby="res-h"><h3 id="res-h">Show the result</h3>` +
      (entry.error ? `<p class="dnote" role="alert">The result could not be read: ${esc(entry.error)} <button id="resRetry">Try again</button></p>`
                   : `<p class="dnote" aria-busy="true">Reading the result…</p>`) + `</section>`;
  }
  const rows = d.artifacts || [];
  const byId = Object.fromEntries(rows.map((a, i) => [a.id, i]));
  const curated = (d.curated || []).map(id => rows[byId[id]]).filter(Boolean);
  const pairs = (d.pairs || []).filter(p => p.matched);
  const alarm = d.tone === 'alarm';
  const nothing = !rows.length && d.status === 'off';
  const facts = (d.availability || []).map(a =>
    `<dt>${esc(FACT_WORDS[a.fact] || a.fact)}</dt><dd><span class="cbadge ${a.state === 'yes' ? 'ok' : a.state === 'unknown' ? 'dim' : ''}">${esc(a.state)}</span> ${esc(a.words)}</dd>`).join('');
  const checks = (d.checks || []).length
    ? `<ul class="reschecks">` + d.checks.map(c => `<li><button class="dlink" data-dview="evidence" title="Open the ◫ evidence sheet${c.evidence ? ' — ' + esc(c.evidence) : ''}"><b>${esc(c.check)}</b> — ${esc(c.outcome || 'unknown')}</button>` +
        ` <span class="dim">at ${esc(shortSha(c.commit))}${c.atCandidate ? ' (current commit)' : ' (another commit — history)'}</span></li>`).join('') + `</ul>`
    : `<p class="dnote">Bench has run no check for this card.</p>`;
  const acc = d.acceptance
    ? `<button class="dlink" data-goto="acceptance">${d.acceptance.accepted} of ${d.acceptance.total} criteria accepted on record</button> — a demonstration accepts none of them.`
    : `<button class="dlink" data-goto="acceptance">No acceptance results yet</button> — a demonstration records none.`;
  const gaps = (d.gaps || []).length
    ? `<p class="dim">No current output is linked to: ${d.gaps.map(g => esc(g.text)).join('; ')}.</p>` : '';
  const pairHtml = pairs.map(p => {
    const b = rows[byId[p.before]], a = rows[byId[p.after]];
    return `<div class="respair" data-keep="res-pair-${esc(p.pair)}"><h4>Before / after — ${esc(p.scenario || p.pair)}` +
      `${p.viewport ? ` at ${esc(p.viewport)}` : ''}</h4>` +
      `<p class="dim">Before shows ${esc(shortSha(p.beforeRevision))} (the program's account); after was captured at ${esc(shortSha(p.afterRevision))}.` +
      `${p.current ? '' : ' Not current.'}</p>` +
      `<ul class="resgallery">${[b, a].filter(Boolean).map(x => artifactHtml(t, x, byId[x.id])).join('')}</ul></div>`;
  }).join('');
  if (nothing && !d.suggest) {
    // Off with nothing kept: one line and the way to change it — Off asks
    // for no extra work, so it earns no more of the Overview than that
    return `<section class="showresult" data-keep="result" aria-labelledby="res-h">` +
      `<h3 id="res-h">Show the result <span class="cbadge" id="resState">${esc(d.label)}</span></h3>` +
      `<p class="dim">No demonstration was asked for and none is kept on this board.</p>` +
      resultSettingHtml(t, d) + `</section>`;
  }
  if (nothing) {
    // Off with nothing kept, and a suggestion to make (task 149): the
    // suggestion, the checks bench ran and where the change is are all
    // still here, one fold away — an optional demonstration does not get
    // to push the work's own account down the Overview
    const open = S.accOpen[`${t.file}:res-off`];
    return `<section class="showresult compact" data-keep="result" aria-labelledby="res-h">` +
      `<h3 id="res-h">Show the result <span class="cbadge" id="resState">${esc(d.label)}</span></h3>` +
      `<p class="dim">No demonstration was asked for and none is kept on this board.</p>` +
      `<details class="rpt" data-keep="res-off" data-res-fold="off"${open ? ' open' : ''}>` +
      `<summary>Screenshots, the checks bench ran, and where the change is</summary>` +
      `<div style="padding:6px 10px">` +
      `<div class="ressuggest" role="note"><p>Would screenshots help you review this change?</p>` +
      `<p><button id="resSuggestYes">Request screenshots</button> <button id="resSuggestNo">Not now</button></p></div>` +
      `<h4>What ran</h4>` + checks + `<p>${acc}</p>` +
      `<h4>Where the change is</h4><dl class="dfacts resfacts">${facts}</dl>` +
      `</div></details>` +
      resultSettingHtml(t, d) + `</section>`;
  }
  const pairedIds = new Set(pairs.flatMap(p => [p.before, p.after]));
  const singles = curated.filter(a => !pairedIds.has(a.id));
  const allOpen = S.accOpen[`${t.file}:res-all`];
  return `<section class="showresult" data-keep="result" aria-labelledby="res-h">` +
    `<h3 id="res-h">Show the result <span class="cbadge ${RES_TONE[d.tone] || ''}" id="resState">${esc(d.label)}</span></h3>` +
    `<p ${alarm ? 'role="alert"' : 'role="status"'}>${esc(d.sentence)}</p>` +
    (d.remedy ? `<p><b>Fix:</b> ${esc(d.remedy)}</p>` : '') +
    (d.setting && d.setting.want ? `<p>Asked to see: “${esc(d.setting.want)}”</p>` : '') +
    (d.summary ? `<p>${esc(d.summary.text)} <span class="dim">— ${esc(d.summary.from)}</span></p>` : '') +
    (d.suggest
      ? `<div class="ressuggest" role="note"><p>Would screenshots help you review this change?</p>` +
        `<p><button id="resSuggestYes">Request screenshots</button> <button id="resSuggestNo">Not now</button></p></div>` : '') +
    (nothing ? `<p class="dnote">No demonstration output is kept for this card on this board.</p>` : '') +
    pairHtml +
    (singles.length ? `<ul class="resgallery">${singles.map(a => artifactHtml(t, a, byId[a.id])).join('')}</ul>` : '') +
    (rows.length > curated.length || rows.some(a => !a.current)
      ? `<details class="rpt" data-keep="res-all" data-res-fold="all"${allOpen ? ' open' : ''}><summary>All outputs on this board (${rows.length}), with history</summary>` +
        `<ul class="resgallery">${rows.map((a, i) => artifactHtml(t, a, i, 'all')).join('')}</ul></details>` : '') +
    `<h4>What ran</h4>` + checks +
    `<p>${acc}</p>` + gaps +
    `<h4>Where the change is</h4><dl class="dfacts resfacts">${facts}</dl>` +
    (!d.capture.supported && d.status !== 'off'
      ? `<p class="dnote" role="status">${esc(d.capture.why)}</p>` : '') +
    resultSettingHtml(t, d) +
    `<details class="rpt" data-keep="res-about" data-res-fold="about"${S.accOpen[`${t.file}:res-about`] ? ' open' : ''}>` +
    `<summary>What a demonstration is, and is not</summary><div style="padding:6px 10px">` +
    (d.boundaries || []).map(b => `<p>${esc(b)}</p>`).join('') + `</div></details>` +
    `</section>`;
}

/* The full-size view: one native dialog, made once on the page rather than
   inside the drawer, so a live redraw of the drawer does not close it.
   Escape closes it (the dialog's own behaviour) and focus goes back to the
   thumbnail that opened it, found again by its index after any redraw. */
function resultViewer() {
  let dialog = document.getElementById('resView');
  if (dialog) return dialog;
  dialog = document.createElement('dialog');
  dialog.className = 'resview';
  dialog.id = 'resView';
  dialog.setAttribute('aria-labelledby', 'resViewCap');
  dialog.innerHTML = `<figure><img id="resViewImg" alt=""><figcaption id="resViewCap"></figcaption></figure>` +
    `<p><a id="resViewOpen" target="_blank" rel="noopener noreferrer">Open in a new tab</a> · ` +
    `<button id="resViewClose">Close</button></p>`;
  document.body.appendChild(dialog);
  dialog.querySelector('#resViewClose').addEventListener('click', () => dialog.close());
  // Escape closes this view only: the page's own Escape (closing the
  // detail view) listens on the document, so it is not told
  dialog.addEventListener('keydown', (e) => { if (e.key === 'Escape') e.stopPropagation(); });
  dialog.addEventListener('close', () => {
    const back = document.querySelector(`[data-res-view="${dialog.dataset.from}"]`);
    if (back) back.focus();
  });
  return dialog;
}

async function saveResult(t, d, fields) {
  const data = await postCard('/api/card/result/setting', {
    file: t.file, stage: t.stage, briefSha: d.briefSha, ...fields });
  if (data) {
    for (const k of ['mode', 'want', 'expects', 'agree', 'preview']) delete S.accDraft[`res-${k}:${t.file}`];
    toast(`Show the result: ${data.mode}` + (data.unagreed ? ' — not enforced until an output is agreed' : '') +
          (data.note ? ` — ${data.note}` : '') + (data.committed ? '' : ' — written to the card, not committed'));
  }
  loadResult(t, true);
  loadDetail(t, true);
  loadState();
}

/* Delegated on the Overview section, which a full redraw replaces (so
   one listener per drawing) and an in-place patch does not (so a patched
   block's buttons still work). */
function wireTiming(t) {
  const ov = document.querySelector('#dsec-overview');
  if (!ov) return;
  ov.addEventListener('click', (e) => {
    const el = e.target.closest('#ctRetry, #ctRefresh, [data-ct-open]');
    if (!el) return;
    if (el.matches('[data-ct-open]')) {
      const h = ov.querySelector('#ct-h');
      if (h) h.scrollIntoView({ block: 'start' });
      return;
    }
    loadTiming(t, true);
    patchTiming(t);
  });
}

function wireResult(t) {
  const body = $('#drawerbody');
  const d = (S.result[t.file] || {}).data;
  const retry = body.querySelector('#resRetry');
  if (retry) retry.addEventListener('click', () => { loadResult(t, true); renderDrawer(); });
  if (!d) return;
  const key = (k) => `res-${k}:${t.file}`;
  // a fold's open or closed choice is recorded by wireDetail's wireFolds,
  // under the key its renderer reads (`res-<fold>`) as well as its data-keep
  body.querySelectorAll('[data-res-mode]').forEach(r => r.addEventListener('change', () => {
    S.accDraft[key('mode')] = r.value;
    S.accOpen[`${t.file}:res-edit`] = true;
    renderDrawer();
  }));
  const want = body.querySelector('#res-want');
  if (want) want.addEventListener('input', () => { S.accDraft[key('want')] = want.value; });
  const agree = body.querySelector('#res-agree');
  if (agree) agree.addEventListener('change', () => { S.accDraft[key('agree')] = agree.checked; });
  body.querySelectorAll('[data-res-shortcut]').forEach(b => b.addEventListener('click', () => {
    const pick = RES_SHORTCUTS.find(([v]) => v === b.dataset.resShortcut);
    S.accDraft[key('expects')] = pick[0];
    if (!(S.accDraft[key('want')] || (d.setting || {}).want)) S.accDraft[key('want')] = pick[2];
    renderDrawer();
  }));
  const fields = () => {
    const s = d.setting || {};
    const mode = S.accDraft[key('mode')] || s.mode || 'off';
    return { mode,
      want: S.accDraft[key('want')] !== undefined ? S.accDraft[key('want')] : (s.want || ''),
      expects: S.accDraft[key('expects')] || s.expects || 'any',
      agree: mode === 'required' && (S.accDraft[key('agree')] !== undefined ? S.accDraft[key('agree')] : !!s.agreedNow) };
  };
  const save = body.querySelector('#resSave');
  if (save) save.addEventListener('click', () => { save.disabled = true; saveResult(t, d, fields()); });
  const preview = body.querySelector('#resPreview');
  if (preview) preview.addEventListener('click', async () => {
    preview.disabled = true;
    const data = await postCard('/api/card/result/preview', { file: t.file, stage: t.stage });
    if (data) {
      S.accDraft[key('preview')] = data;
      if (!S.accDraft[key('mode')]) S.accDraft[key('mode')] = 'requested';
    }
    renderDrawer();
  });
  const yes = body.querySelector('#resSuggestYes');
  if (yes) yes.addEventListener('click', () => {
    S.accDraft[key('mode')] = 'requested';
    S.accDraft[key('expects')] = 'screenshots';
    S.accOpen[`${t.file}:res-edit`] = true;
    renderDrawer();
  });
  const no = body.querySelector('#resSuggestNo');
  if (no) no.addEventListener('click', async () => {
    no.disabled = true;
    await postCard('/api/card/result/dismiss', { file: t.file, stage: t.stage });
    loadResult(t, true);
  });
  const rows = d.artifacts || [];
  body.querySelectorAll('input[data-res-why]').forEach(input => input.addEventListener('input', () => {
    const a = rows[Number(input.dataset.resWhy)];
    if (a) S.accDraft[`res-why:${a.id}`] = input.value;
  }));
  // Withholding deletes bytes for good, so it arms on the first click and
  // fires on the second, like every other action that cannot be taken back
  body.querySelectorAll('[data-res-withhold]').forEach(btn => {
    const a = rows[Number(btn.dataset.resWithhold)];
    if (!a) return;
    wireAction(btn, `${t.file}::res-withhold:${a.id}`, {
      label: 'Withhold', confirm: true,
      run: async () => {
        const data = await postCard('/api/artifact/withhold', {
          file: t.file, stage: t.stage, id: a.id, reason: S.accDraft[`res-why:${a.id}`] || '' });
        if (data) { delete S.accDraft[`res-why:${a.id}`]; toast('output withheld — its record stays'); }
        loadResult(t, true);
        return !!data;
      },
    });
  });
  body.querySelectorAll('[data-res-view]').forEach(btn => btn.addEventListener('click', () => {
    const a = rows[Number(btn.dataset.resView)];
    if (!a || !a.url) return;
    const dialog = resultViewer();
    const img = dialog.querySelector('#resViewImg');
    img.src = a.url;
    img.alt = a.caption || a.name || 'output';
    dialog.querySelector('#resViewCap').textContent = (a.caption || a.name || '') +
      (a.current ? ' — current' : ` — ${(a.relevanceWords || [])[0] || ''}`);
    dialog.querySelector('#resViewOpen').href = a.url;
    dialog.dataset.from = btn.dataset.resView;
    if (!dialog.open) dialog.showModal();
  }));
}

/* The Overview's one line about the card's information: the same state the
   Inputs tab leads with, and the way there. */
function inputsFact(t) {
  const i = ((S.state || {}).information || {})[t.file];
  if (!i) return '';
  return `<button class="dlink" data-goto="inputs">${esc(i.label)}</button>` +
    (i.state !== 'ready' ? ` — ${esc(i.sentence)}` : '') +
    (i.optional ? ` · ${i.optional} optional left out` : '');
}

function loadDetail(t, force) {
  // a payload that still carries the text (an older board) needs no read
  if (typeof t.body === 'string' && !t.rev) return;
  detail.read(t.file, detailKey(t), {
    force, arg: t,
    done: () => { if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer(); },
  });
  // a handful of texts are kept for going back and forth; the rest are
  // dropped, and any reply still on its way for them with them
  const files = Object.keys(S.detail);
  for (const f of files.slice(0, Math.max(0, files.length - DETAIL_KEEP)))
    if (f !== t.file) detail.forget(f);
}

/* The card with its text, when a text is in hand that is about this card
   in the stage it is listed in — otherwise null, and the brief says it is
   being read. Never another card's text, and never one read from another
   stage. */
function withDetail(t) {
  if (!t) return null;
  if (typeof t.body === 'string' && !t.rev) return t;
  const d = (S.detail[t.file] || {}).data;
  if (!d || d.file !== t.file || d.stage !== t.stage) return null;
  return { ...t, body: d.body, parts: d.parts, words: d.words, textRev: d.rev, outline: d.outline || null };
}

function briefHtml(listed) {
  // the card as listed, and — once its text is in hand — the card with it
  const t = withDetail(listed);
  const entry = (listed && S.detail[listed.file]) || {};
  if (!t) {
    if (entry.error && !entry.loading)
      return `<div class="dload bad" role="alert">Could not read this card's brief — ${esc(entry.error)}. ` +
        `<button type="button" class="dlink" id="dretry">Try again</button></div>`;
    return `<div class="dload" role="status" aria-live="polite" aria-busy="true">Reading the card…</div>`;
  }
  const note = entry.stale && entry.error ? staleWords(entry.error)
    : entry.loading && t.textRev && listed.rev && t.textRev !== listed.rev
      ? 'This card has changed since this text was read; the newest text is being read.' : '';
  const parts = cardSplit(t);
  const head = parts.head.replace(/^\s*#\s[^\n]*\n?/, '');
  // the header fields as a grid of their own (task 149), rather than one
  // run-on paragraph: each value drawn by the same renderer, and any line
  // that is not a field kept as Markdown, so nothing in the head is lost
  const fields = [];
  const rest = [];
  const field = (line) => line.match(/^\*\*([A-Za-z][A-Za-z ]{0,40}):\*\*\s*(.*)$/);
  let open = false;   // the last line was a field, or its continuation
  for (const line of head.replace(/\n$/, '').split('\n')) {
    const m = field(line);
    if (m) { fields.push([m[1], m[2]]); open = true; }
    else { rest.push(line); open = false; }
  }
  // The server's head ends at the first line that is neither blank nor a
  // field (`taskfiles.card_parts`), so a field's value wrapped onto the
  // next line — and every field after it — arrives as the start of the
  // brief. Markdown reads that line as the same paragraph, so the grid
  // does too: while the head ended on a field with no blank line after
  // it, the brief's leading lines up to the first blank line (or the
  // first line that starts a block of its own) are that field's value,
  // or further fields. The split itself is the server's and is unchanged.
  let brief = withoutDiscussion(parts.brief);
  if (open && fields.length) {
    const lines = brief.split('\n');
    let k = 0;
    for (; k < lines.length; k++) {
      const line = lines[k];
      if (!line.trim() || /^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|```|>|---\s*$)/.test(line)) break;
      const m = field(line);
      if (m) fields.push([m[1], m[2]]);
      else fields[fields.length - 1][1] += ' ' + line.trim();
    }
    brief = lines.slice(k).join('\n');
  }
  const value = (v) => v.trim() ? md(v).replace(/^<p>/, '').replace(/<\/p>\s*$/, '') : '<span class="dim">—</span>';
  const meta = fields.length
    ? `<dl class="dfacts bmeta" aria-label="Card fields">` +
      fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${value(v)}</dd>`).join('') + `</dl>` : '';
  // the brief's own sections, as a short index to jump along a long one
  const heads = ((t.outline || {}).sections || []);
  const index = heads.length > 2
    ? `<nav class="bindex" aria-label="Sections of the brief">` +
      heads.map((h, i) => `<button type="button" class="dlink" data-brief-h="${i}">${esc(h)}</button>`).join('') +
      `</nav>` : '';
  return (note ? `<p class="dnote dstale" role="status">${esc(note)}</p>` : '') + meta + index +
    `<div class="dbody bbody">${md(rest.join('\n') + '\n' + brief)}</div>` +
    `<p class="dnote">The brief is the requirement. Earlier wordings are in the card's history: ` +
    `each applied refinement is a decision in the discussion below, and the card's git history ` +
    `holds every previous text.</p>`;
}

function evidenceOptions(t, draft) {
  const e = S.evidence[t.file];
  if (!e) { fetchEvidence(t.file, t.stage).then(() => { if (S.selected && S.selected.file === t.file) renderDrawer(); }); }
  const passed = ((e && e.evidence) || []).filter(r => r.outcome === 'passed');
  const chosen = S.accDraft[draft] || '';
  return `<option value="">none — a person's confirmation, with a note</option>` +
    passed.map(r => `<option value="${esc(r.id)}"${chosen === r.id ? ' selected' : ''}>` +
      `${esc(r.check)}${r.scope ? ' · ' + esc(r.scope) : ' · full run'} · passed` +
      `${(r.source || {}).commit ? ' at ' + esc(r.source.commit.slice(0, 12)) : ''} · ${esc(r.id)}</option>`).join('');
}

/* One result a run reported about a criterion, as its history shows it:
   whose words (the role and the run), about which commit and revision,
   the reason and the evidence it cited — read from the filed report's
   board-written line, never from the report's prose (task 190). */
function resultRecordHtml(r, states) {
  return `<div class="arec"><b>${esc((states || {})[r.state] || r.state)}</b> · reported by the ` +
    `${esc(ROLE_WORDS[r.role] || r.role)}${r.actor ? ' ' + esc(r.actor) : ''}` +
    `${r.model ? ` <span class="ameta">(${esc(r.model)})</span>` : ''} · ${esc(r.report || '')}` +
    ` · ${esc(stampOf(r.at))}` +
    (r.reason ? `<br>${esc(r.reason)}` : '') +
    ((r.evidenceIds || []).length ? `<br>evidence cited: ${r.evidenceIds.map(esc).join(', ')}` : '') +
    `<br><span class="ameta">${r.commit ? 'about commit ' + esc(r.commit) + ' · ' : 'no commit recorded · '}` +
    `criterion revision ${esc(r.rev || '')}</span></div>`;
}

function recordHtml(r, states) {
  if (r.source === 'agent') return resultRecordHtml(r, states);
  const word = { accepted: 'accepted', 'needs-work': 'not met', recheck: 'recheck requested',
                 pending: 'reset to no result', reported: 'reported' }[r.state] || r.state;
  const src = { manual: 'decided by a person', evidence: 'mapped to bench evidence by a person',
                tracked: 'criterion identified', 'legacy-tick': 'ticked by hand — an unverified claim' }[r.source] || r.source;
  return `<div class="arec"><b>${esc(word)}</b> · ${esc(src)} · ${esc(r.actor || 'unattributed')}` +
    `${r.actorSource === 'os-user' ? ' (OS user)' : ''} · ${esc(stampOf(r.at))}` +
    (r.evidence ? `<br>evidence ${esc(r.evidence)} — ${esc(r.check || '')}${r.scope ? ' · ' + esc(r.scope) : ''}` : '') +
    (r.commit ? `<br><span class="ameta">at ${esc(r.commit)} · criterion revision ${esc(r.rev || '')}</span>` : `<br><span class="ameta">criterion revision ${esc(r.rev || '')}</span>`) +
    (r.note ? `<br>${esc(r.note)}` : '') +
    (r.ref ? `<br>reference: ${/^(https?:\/\/|\/files\/)/.test(r.ref)
      ? `<a href="${esc(r.ref)}" target="_blank" rel="noopener">${esc(r.ref)}</a>` : esc(r.ref)}` : '') +
    (r.recheck ? `<br><span class="bad">marked for recheck: ${esc(r.recheckReason || '')}</span>` : '') +
    `</div>`;
}

/* The element-id form of a criterion's key: an untracked criterion is
   keyed by `@<revision>`, which is not a selector-safe id. */
function accDom(key) {
  return String(key || '').replace(/[^a-z0-9-]/gi, '_');
}

/* Who stands behind a criterion's state, in words. */
function accBy(c) {
  if (c.state === 'pending') return '';
  if (c.by === 'person') return `decided by ${esc(((c.decision || {}).actor) || 'a person')}`;
  if (c.result) return `${ROLE_WORDS[c.result.role] || c.result.role}${c.result.actor ? ' ' + esc(c.result.actor) : ''}`;
  if (c.claim) return 'implementation agent, while working';
  return '';
}

/* One criterion: the outcome in words, the whole requirement, and the
   reason right under it; who said so, the evidence, every earlier result
   and a person's correction fold away under it (task 190). */
function criterionHtml(t, c, states) {
  const key = c.key || c.id;
  const dom = accDom(key);
  const open = S.accOpen[`${t.file}:${key}`] ?? false;
  const v = (id) => esc(S.accDraft[id] || '');
  const ev = c.evidence;
  const claim = c.claim;
  const by = accBy(c);
  const evList = (c.evidenceList || []).map(e =>
    `<div class="arec">evidence ${esc(e.id)}: ${e.found ? esc(e.check || '') + ' · <b>' + esc(e.outcome) + '</b>' +
      (e.commit ? ' at ' + esc(e.commit) : '') + ' · read from bench\'s record now'
      : '<b>not on this board</b> — cited, and bench has no such record for this card'}</div>`).join('');
  const skipped = (c.skipped || []).length
    ? `<p class="dnote">${c.skipped.length} later report${c.skipped.length === 1 ? ' was' : 's were'} set aside: ` +
      `a run's result does not overwrite ${c.by === 'person' ? "a person's decision" : "the independent review's result"} about the same commit. Each is in the history below.</p>` : '';
  const correctOpen = S.accOpen[`${t.file}:${key}:decide`] ?? false;
  // each criterion leads with its first clause, so a long list scans by
  // its leads and the rest reads after (task 149) — the whole wording is
  // there, in order, and nothing is reworded
  const m = String(c.text).match(/^(.{12,}?[.;:])\s+([\s\S]+)$/);
  const words = m ? `<span class="alead">${esc(m[1])}</span> <span class="arest">${esc(m[2])}</span>`
                  : `<span class="alead">${esc(c.text)}</span>`;
  return `<details class="arow ${esc(c.state)}" data-keep="${esc(key)}"${open ? ' open' : ''}>` +
    `<summary><span class="abox" aria-hidden="true">${ACC_GLYPH[c.state] || ''}</span>` +
    `<span class="amain"><span class="aword">${esc(c.word)}</span>` +
    `<span class="afull">${words}</span>` +
    (c.reason ? `<span class="areason">${esc(c.reason)}</span>` : '') +
    (by ? `<span class="aby">${by}</span>` : '') +
    `</span></summary>` +
    `<div class="abody">` +
    `<span class="ameta">${c.id ? 'id ' + esc(c.id) : 'no id yet — given on the next saved brief, run report or decision'}` +
    ` · revision ${esc(c.rev)}${c.legacyTick ? ' · ticked by hand in the brief' : ''}</span>` +
    skipped +
    (ev ? `<div class="arec">supporting evidence ${esc(ev.id)}: ${esc(ev.check || '')} · <b>${esc(ev.outcome)}</b>` +
          `${ev.commit ? ' at ' + esc(ev.commit) : ''} · read from bench's record now</div>` : '') +
    evList +
    (claim ? `<div class="arec">${esc(claim.agent)} reported it implemented, ${esc(ago(claim.at))} ago` +
             ` — agent-reported while working, not a result${(claim.evidence || []).length ? ` · proposed evidence ${esc(claim.evidence.join(', '))}` : ''}</div>` : '') +
    (c.history.length ? `<h4 class="ovsub">History</h4>` + c.history.map(r => recordHtml(r, states)).join('')
                      : '<p class="dnote">Nothing recorded about it yet.</p>') +
    `<details class="rpt acorrect" data-keep="${esc(key)}:decide"${correctOpen ? ' open' : ''}>` +
    `<summary>Record your decision</summary>` +
    `<div class="aform" data-crit="${esc(key)}" data-dom="${esc(dom)}" data-rev="${esc(c.rev)}" data-basis="${esc(c.basis || '')}">` +
    `<p class="dnote">Only for a real correction or a decision that is yours: results are recorded by the runs and the independent review on their own. A decision is attributed to you and is never overwritten by a later report about the same commit.</p>` +
    `<label>Decision<select id="acc-state-${dom}">` +
    [['accepted', 'Accept'], ['needs-work', 'Not met'], ['recheck', 'Needs recheck'], ['pending', 'Clear to no result']]
      .map(([k, w]) => `<option value="${k}"${(S.accDraft[`acc-state-${dom}`] || 'accepted') === k ? ' selected' : ''}>${w}</option>`).join('') +
    `</select></label>` +
    `<label>Note — what you checked and how<textarea id="acc-note-${dom}" rows="2">${v(`acc-note-${dom}`)}</textarea></label>` +
    `<label>Reference (optional) — a screenshot, URL or file<input id="acc-ref-${dom}" value="${v(`acc-ref-${dom}`)}"></label>` +
    `<label>Supporting evidence (optional, for Accept)<select id="acc-ev-${dom}">${evidenceOptions(t, `acc-ev-${dom}`)}</select></label>` +
    `<div class="racts"><button class="dact" data-decide="${esc(key)}">Record decision</button></div>` +
    `<p class="dnote">Recorded as ${esc(accActor(t) || 'this board')}, against revision ${esc(c.rev)} of this criterion.</p>` +
    `</div></details></div></details>`;
}

/* The acceptance conclusion, as the server reads it: one headline, the
   counts of the criteria listed, and who must do what next. Drawn by the
   Acceptance section and, compact, by the Overview — never recomputed. */
function accOutcomeHtml(acc, compact) {
  if (!acc) return compact ? 'reading…' : `<p class="dnote">reading acceptance…</p>`;
  const o = acc.outcome || {};
  const counts = (o.parts || []).map(p => `${p.count} ${esc(p.word)}`).join(' · ');
  const owner = o.owner ? OWNER_WORDS[o.owner] || o.owner : '';
  const next = o.next ? `${owner ? `<b>Next — ${esc(owner)}:</b> ` : ''}${esc(o.next)}` : '';
  if (compact) {
    return `<button class="dlink" data-goto="acceptance">${esc(o.headline || 'Acceptance')}</button>` +
      (counts ? ` <span class="ovfrom">${counts}</span>` : '') +
      (next ? `<br><span class="accnext">${next}</span>` : '');
  }
  return `<div class="accsum tone-${esc(o.tone || 'neutral')}" id="accSummary" role="status">` +
    `<h3>${esc(o.headline || 'Acceptance')}</h3>` +
    (counts ? `<p class="acount">${counts}${o.total ? ` <span class="ameta">of ${o.total} criteri${o.total === 1 ? 'on' : 'a'}</span>` : ''}</p>` : '') +
    (next ? `<p class="accnext">${next}</p>` : '') + `</div>`;
}

/* What the newest filed report's reporting amounted to, when it left
   something to say: lines it could not use, criteria it skipped. Shown,
   never filled in; a person's decision and the next run are the ways on. */
function accReportingHtml(t, acc) {
  const latest = (acc.reporting || [])[0];
  if (!latest) return '';
  const problems = latest.problems || [];
  if (!problems.length && !latest.missing) return '';
  const key = `${t.file}:reporting`;
  return `<details class="rpt accprob" data-keep="reporting"${(S.accOpen[key] ?? true) ? ' open' : ''}>` +
    `<summary>The latest report recorded ${latest.count} result${latest.count === 1 ? '' : 's'}` +
    `${latest.missing ? ` and left ${latest.missing} without one` : ''}` +
    `${problems.length ? ` · ${problems.length} problem${problems.length === 1 ? '' : 's'}` : ''}</summary>` +
    `<div style="padding:6px 10px">` +
    `<p class="dnote">${esc(latest.kind)} by the ${esc(ROLE_WORDS[latest.role] || latest.role)}` +
    `${latest.by ? ' ' + esc(latest.by) : ''}, ${esc(latest.at)}${latest.commit ? ' at ' + esc(latest.commit) : ''}. ` +
    `Nothing below was recorded as a result; the criteria it does not cover read as they did.</p>` +
    (problems.length ? `<ul class="chain">${problems.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '') +
    `<p class="dnote">The way on: the next work run or independent review reports again (its report is read the same way), ` +
    `or record your decision on a criterion below. A report that could not be filed at all is retried from the card's Activity.</p>` +
    `</div></details>`;
}

function accActor(t) {
  const a = (S.acc[t.file] || {}).data;
  return a && a.actor ? a.actor.name : '';
}

/* A failed re-read keeps the last reading, and says it is the last one. */
function accStaleHtml(t) {
  const c = S.acc[t.file];
  return c && c.stale && c.error ? `<p class="dnote bad">${esc(staleWords(c.error))}</p>` : '';
}

function acceptanceHtml(t, acc) {
  return accStaleHtml(t) + acceptanceBody(t, acc);
}

function acceptanceBody(t, acc) {
  if (!acc) {
    const c = S.acc[t.file];
    return c && c.error ? `<p class="dnote bad">${esc(c.error)}</p>` : `<p class="dnote">reading acceptance…</p>`;
  }
  const counts = acc.counts;
  const items = acc.items || acc.criteria;
  // Only where a merge rule asks for sign-off is it a requirement, and
  // then it says so here in words, with what it reads now (design #94
  // §8.3). Without one, nothing on this card asks anyone to confirm
  // criteria, and the section does not pretend otherwise.
  const so = acc.signoff || {};
  const signoffLine = so.required
    ? `<p class="dnote" id="accSignoff">` +
      `<b>Sign-off required</b> by this board's ${esc(so.scope)} merge rule: ` +
      `${esc(so.plain || so.why || '')}.` +
      (so.state !== 'met' && so.remedy ? ` ${esc(so.remedy)}` : '') + `</p>`
    : '';
  // Done is not acceptance — said here, as a fact about the records, and
  // not as a step on the Overview. Whether a rule asked for a sign-off is
  // the server's answer, never assumed (design #94 §8.1, §8.3)
  const unaccepted = t.stage === 'done' && counts.total ? counts.total - counts.accepted : 0;
  const orphans = acc.orphanResults || [];
  const how = `<details class="rpt" data-keep="acc-how"${S.accOpen[`${t.file}:acc-how`] ? ' open' : ''}>` +
    `<summary>How these results are recorded</summary><div style="padding:6px 10px">` +
    `<p class="dnote">Each work or repair run reports a result per criterion in its closing report, and the independent PR review confirms each one against the exact commit it reviewed. Bench reads those results when the report is filed and checks them against this card; there is nothing to set up. ` +
    `<b>Met — awaiting review</b> is the implementer's claim; only the independent review's result, or a person's decision, reads <b>Accepted</b>. ` +
    `A new commit or a reworded criterion makes an earlier result <b>Needs recheck</b>, and the earlier result stays in the history. ` +
    `Nothing here is inferred from the card's stage, an agent's exit or a passing suite, and an accepted checklist is not a merge approval.</p>` +
    `</div></details>`;
  return signoffLine + accOutcomeHtml(acc, false) +
    (unaccepted ? `<p class="dnote">Delivered, and ${unaccepted} criteri${unaccepted === 1 ? 'on was' : 'a were'} ` +
      `not accepted on record. Done is not acceptance` +
      (so.required ? `.</p>` : `; no merge rule here requires a sign-off.</p>`) : '') +
    accReportingHtml(t, acc) +
    (items.length
      ? `<div class="acclist" role="list" aria-label="Acceptance criteria">` +
        items.map(c => `<div role="listitem" class="aitem">${criterionHtml(t, c, acc.states)}</div>`).join('') + `</div>`
      : `<p class="dnote">This brief has no checklist criteria (lines like <code>- [ ] …</code>), so there is nothing to report against.</p>`) +
    how +
    (acc.removed.length || orphans.length
      ? `<details class="rpt" data-keep="acc-removed"${S.accOpen[`${t.file}:acc-removed`] ? ' open' : ''}>` +
        `<summary>No longer in the brief (${acc.removed.length + orphans.length}) — history, not counted</summary>` +
        `<div style="padding:6px 10px">` +
        acc.removed.map(r => `<p class="afull">${esc(r.text || r.id)}</p>` +
          r.history.map(x => recordHtml(x, acc.states)).join('')).join('') +
        (orphans.length ? `<p class="dnote">Results reported about a requirement that has since changed or gone:</p>` +
          orphans.map(x => resultRecordHtml(x, acc.states)).join('') : '') +
        `</div></details>` : '');
}

function findingHtml(t, f, acc) {
  const key = `${t.file}:${f.id}`;
  const open = S.accOpen[key] ?? (f.state !== 'resolved');
  const round = acc.rounds.find(r => r.id === f.round);
  const crit = acc.criteria.find(c => c.id === f.criterion);
  const eventWord = { opened: 'found', 'fix-reported': 'fix reported', resolved: 'resolved', reopened: 'reopened' };
  const acts = { open: [['fix-reported', 'Report fix'], ['resolved', 'Resolve']],
                 'fix-reported': [['resolved', 'Resolve after recheck'], ['reopened', 'Reopen']],
                 resolved: [['reopened', 'Reopen']] }[f.state];
  return `<details class="arow ${esc(f.state)}" data-keep="${esc(f.id)}"${open ? ' open' : ''}>` +
    `<summary><span class="abox" aria-hidden="true">${f.state === 'resolved' ? '✓' : f.state === 'open' ? '!' : '·'}</span>` +
    `<span class="alabel">${esc(f.text)}</span><span class="aword">${esc(f.word)}</span></summary>` +
    `<div class="abody">` +
    `<span class="ameta">${esc(f.id)}${round ? ' · from ' + esc(roundLine(round)) : ''}</span>` +
    (crit ? `<span>About: <button class="dlink" data-goto="acceptance">${esc(crit.label)}</button> (${esc(crit.word)})</span>` : '') +
    (f.criterionRemoved ? `<span class="dnote">The criterion it was about has since been removed from the brief.</span>` : '') +
    `<ol class="chain">` + f.events.map(e => `<li class="${esc(e.event)}"><b>${esc(eventWord[e.event] || e.event)}</b>` +
      ` · ${esc(e.actor || 'unattributed')} · ${esc(stampOf(e.at))}` +
      (e.commit ? ` · at ${esc(e.commit)}` : '') +
      (e.event === 'opened' ? '' : e.note ? `<br>${esc(e.note)}` : '') +
      (e.evidenceNow ? `<br>evidence ${esc(e.evidenceNow.id)}: ${esc(e.evidenceNow.check || '')} · <b>${esc(e.evidenceNow.outcome)}</b> (read now)` : '') +
      `</li>`).join('') + `</ol>` +
    `<div class="aform" data-finding="${esc(f.id)}" data-basis="${esc(f.basis || '')}">` +
    `<label>Note — the fix, or on what authority it is resolved<textarea id="fnd-note-${esc(f.id)}" rows="2">${esc(S.accDraft[`fnd-note-${f.id}`] || '')}</textarea></label>` +
    (f.state !== 'resolved' ? `<label>Supporting evidence (optional)<select id="fnd-ev-${esc(f.id)}">${evidenceOptions(t, `fnd-ev-${f.id}`)}</select></label>` : '') +
    `<div class="racts">` + acts.map(([ev, w]) => `<button class="dact" data-event="${ev}">${esc(w)}</button>`).join('') + `</div>` +
    `<p class="dnote">A reported fix keeps the finding open until somebody rechecks it. An agent's later update never resolves it, and resolving it here is not formal PR approval.</p>` +
    `</div></div></details>`;
}

/* A report an agent filed — a review round, a work report, a PR update,
   a relevance review — drawn as the document it is (task 83): md(), the
   one renderer the brief uses, with headings nested under the section's
   own. It is presentation only. The text is the card's, byte for byte;
   nothing here reads a verdict, a finding or an acceptance out of its
   prose, and the round's reviewer, revision, date and verdict stay in the
   summary that holds it. The original Markdown is one fold away, to read
   or copy exactly as filed. */
/* The same review in the Brain (task 168): its readable page with what
   bench recorded about it and its classification. The report is not
   copied there — the Brain reads this card. A card with no number has
   only its file stem to be named by, which a rename changes. */
function brainLink(t, id) {
  const href = `/brain/r/${encodeURIComponent(cardId(t, S.state) || stemOf(t.file))}/${encodeURIComponent(id)}`;
  return `<p class="rvbrain"><a href="${esc(href)}" data-route data-brain-review="${esc(id)}">` +
    `Read in the Brain</a> <span class="dnote">— its provenance and classification; the report stays here</span></p>`;
}

function reportHtml(t, text, key) {
  const src = `src:${key}`;
  return `<div class="rtext rmd dbody">${md(text, { base: 4 })}</div>` +
    // data-fold, not data-keep: its open state survives a redraw, but it
    // is never the reading-position anchor — the report holding it is
    `<details class="rsrc" data-fold="${esc(src)}"${S.accOpen[`${t.file}:${src}`] ? ' open' : ''}>` +
    `<summary>Original Markdown</summary>` +
    `<div class="racts"><button type="button" class="dlink rcopy">Copy original Markdown</button></div>` +
    `<pre class="rraw" tabindex="0" aria-label="Original Markdown of this report">${esc(text)}</pre></details>`;
}

/* Relevance reviews (task 84): a reviewer's judgement of whether the
   *brief* is still needed and current — not of code, so no commit, no
   approval and no merge requirement is read from one. Each keeps its own
   verdict words, its reviewer and time and its stable id; the revision
   of the brief it read is said to be unrecorded rather than guessed. */
const RELEVANCE_TONE = { 'Still relevant': '', 'Partly done': '', 'Already done': '', 'Needs rewrite': 'bad' };

function relevanceLine(r) {
  return `${r.reviewer || 'Reviewer not recorded'} · ${r.verdict || r.verdictLine || 'no verdict line'} · ${r.at}`;
}

function reviewRefineWhy(d) {
  if (!d) return 'reading whether a refinement can be asked for…';
  if (d.blocked) return esc(d.blocked);
  if (!d.refine.ready) return esc('Update card from this review is not available on this board: ' +
    (d.refine.blockers[0] || 'the refine intent does not resolve'));
  return '';
}

/* The comments linked to one relevance review, oldest first, each with
   the refinement attempts that answer it — the same rows the Brief's
   discussion draws, read from the same store. */
function reviewThreadHtml(t, id) {
  const c = (S.disc || {})[t.file];
  const d = c && c.data;
  if (!d) return c && c.error ? `<p class="dnote bad">${esc(c.error)}</p>`
                              : `<p class="dnote">reading comments…</p>`;
  const jobs = jobsByComment(d);
  const mine = d.entries.filter(e => e.kind === 'comment' && e.review === id);
  return mine.length
    ? mine.map(e => commentRowHtml(t, e, jobs[e.id] || [], d, true)).join('')
    : `<p class="dnote">No comments on this review yet.</p>`;
}

function relevanceHtml(t, r, open) {
  const key = `${t.file}::${r.id}`;
  const d = ((S.disc || {})[t.file] || {}).data;
  const cannot = !d || !!d.blocked || !d.refine.ready;
  const tone = RELEVANCE_TONE[r.verdict] || '';
  return `<details class="rpt rvrel" id="rvw-${esc(r.id)}" data-keep="${esc(r.id)}"` +
    `${(S.accOpen[`${t.file}:${r.id}`] ?? open) ? ' open' : ''}>` +
    // the summary has an id so a live redraw gives focus back to it
    `<summary id="rvs-${esc(r.id)}"><b class="${tone}">${esc(r.verdict || r.verdictLine || 'No verdict line')}</b>` +
    `<span class="rvwho">${esc(r.reviewer || 'reviewer not recorded')} · ${esc(r.at)}</span>` +
    `<span class="ameta">${esc(r.id)}</span></summary>` +
    `<dl class="dfacts rvmeta">` +
    `<dt>Purpose</dt><dd>Whether the brief is still needed and current</dd>` +
    `<dt>Reviewer</dt><dd>${esc(r.reviewer || 'not recorded')}</dd>` +
    `<dt>Filed</dt><dd>${esc(r.at)}${r.legacy ? ' — filed in an older format; nothing more was recorded' : ''}</dd>` +
    `<dt>Brief revision</dt><dd>${r.briefRevision ? `<span class="mono">${esc(r.briefRevision)}</span>` : 'not recorded'}</dd>` +
    `<dt>Identity</dt><dd><span class="mono">${esc(r.id)}</span></dd></dl>` +
    brainLink(t, r.id) + reportHtml(t, r.report, r.id) +
    `<div class="discussion rvdisc" role="group" aria-label="Comments on this review"><h4>Comments on this review</h4>` +
    `<div class="rvthread" data-review="${esc(r.id)}">${reviewThreadHtml(t, r.id)}</div>` +
    `<div class="composer rvcompose" data-review="${esc(r.id)}">` +
    `<label for="rvbox-${esc(r.id)}">Comment, or what to take from this review into the brief</label>` +
    `<textarea id="rvbox-${esc(r.id)}" rows="3">${esc((S.commentDraft || {})[key] || '')}</textarea>` +
    `<div class="racts"><button class="dact" data-rvact="comment">Comment</button>` +
    `<button class="dact" data-rvact="refine"${cannot ? ' disabled' : ''}>` +
    `${actLabel('✎ Update card from this review', 'click again — asks an agent', 'asking…')}</button>` +
    `<button type="button" class="dact ap-open"${askAttrs('refine', { card: t.file, review: r.id })}>Open in conversation panel</button></div>` +
    `<p class="dnote bad rvwhy" role="status">${reviewRefineWhy(d)}</p>` +
    `<p class="dnote">Comment only saves your note, linked to this review. Update card from this review ` +
    `sends this review and your instruction (or, if you leave it empty, “apply its recommendations”) ` +
    `to an agent that proposes edits to the brief. It is not another review and starts no implementation; ` +
    `the brief changes only when you apply the proposal.</p></div></div></details>`;
}

/* The one review status the card face wears (task 152), said again where
   its rounds are, with the sentence the face keeps in its title. The
   server's answer, not a reading of the rounds below. */
function reviewNowHtml(t) {
  const d = deliveryOf(t);
  const r = d && d.review;
  if (!r || !r.word) return '';
  return `<p class="rvnow rv-${esc(r.tone || 'neutral')}" data-revword="${esc(r.word)}">` +
    `<b>${esc(r.label)}</b> — ${esc(r.sentence || '')}</p>`;
}

function reviewsHtml(t, acc) {
  if (!acc) return `<p class="dnote">reading reviews…</p>`;
  const live = acc.findings.filter(f => f.state !== 'resolved');
  const done = acc.findings.filter(f => f.state === 'resolved');
  const rounds = [...acc.rounds].reverse();
  const rel = [...(acc.relevance || [])].reverse();
  const roundHtml = (r, open) => `<details class="rpt" id="rvw-${esc(r.id)}" data-keep="${esc(r.id)}"${(S.accOpen[`${t.file}:${r.id}`] ?? open) ? ' open' : ''}>` +
    `<summary id="rvs-${esc(r.id)}"><b>${esc(roundLine(r))}</b>${sinceRound(r, acc) ? `<span class="dnote">${esc(sinceRound(r, acc))}</span>` : ''}` +
    `<span class="ameta">${esc(r.id)}</span></summary>${brainLink(t, r.id)}${reportHtml(t, r.report, r.id)}</details>`;
  const fc = acc.findingCounts;
  // what a group is for, one fold away rather than ahead of its records
  // (task 149): the records lead, the explanation is reference
  const about = (key, summary, text) =>
    `<details class="rhelp" data-fold="${key}"${S.accOpen[`${t.file}:${key}`] ? ' open' : ''}>` +
    `<summary>${esc(summary)}</summary><p class="dnote">${text}</p></details>`;
  const relevance = `<section class="rvgroup" aria-labelledby="rvg-rel-${esc(t.number || 'x')}">` +
    `<h3 id="rvg-rel-${esc(t.number || 'x')}">Relevance reviews <span class="n">${rel.length}</span></h3>` +
    about('rvhelp-rel', 'What a relevance review is',
      `Whether the brief is still needed and current, and what to change in it. ` +
      `A relevance verdict is about the card's definition: it is not a code review, approves nothing ` +
      `and counts toward no acceptance or merge requirement.`) +
    // newest first, every one at the same level: a review arriving later
    // must not fold away the one somebody is reading or commenting on, so
    // the newest opens the first time it is drawn and each keeps its own
    // open state from then on
    (rel.length ? rel.map((r, i) => {
      if (i === 0 && S.accOpen[`${t.file}:${r.id}`] === undefined) S.accOpen[`${t.file}:${r.id}`] = true;
      return relevanceHtml(t, r, false);
    }).join('') + (rel.length > 1 ? `<p class="dnote">${rel.length} relevance reviews, newest first. Each is about the brief as it stood when it was filed.</p>` : '')
      : `<p class="dnote">No relevance review has been filed on this card. ◔ still true? asks for one.</p>`) +
    `</section>`;
  // the code's reviews first — the open findings and the newest round are
  // what a merge waits on — then the brief's own reviews (task 149)
  return filingBanner(t, true) + `<section class="rvgroup" aria-labelledby="rvg-pr-${esc(t.number || 'x')}">` +
    `<h3 id="rvg-pr-${esc(t.number || 'x')}">PR reviews <span class="n">${rounds.length}</span></h3>` +
    // the review status now (main, task 152) leads the group; what a PR
    // review is stays in the fold below, as task 149 placed it
    reviewNowHtml(t) +
    `<p class="acount"><b>${fc.open}</b> open · <b>${fc['fix-reported']}</b> fix reported · <b>${fc.resolved}</b> resolved</p>` +
    (live.length ? live.map(f => findingHtml(t, f, acc)).join('')
                 : `<p class="dnote">No open findings on record.</p>`) +
    `<details class="rpt" data-keep="newfinding"${S.accOpen[`${t.file}:newfinding`] ? ' open' : ''}><summary>Record a finding</summary>` +
    `<div class="aform" style="padding:0 10px 10px" data-newfinding="1">` +
    `<label>What was found<textarea id="fnd-text" rows="2">${esc(S.accDraft['fnd-text'] || '')}</textarea></label>` +
    `<label>From review round<select id="fnd-round"><option value="">none</option>` +
    rounds.map(r => `<option value="${esc(r.id)}"${S.accDraft['fnd-round'] === r.id ? ' selected' : ''}>${esc(roundLine(r))}</option>`).join('') + `</select></label>` +
    `<label>About criterion (optional)<select id="fnd-crit"><option value="">none — not every concern is a criterion</option>` +
    acc.criteria.map(c => `<option value="${esc(c.id)}"${S.accDraft['fnd-crit'] === c.id ? ' selected' : ''}>${esc(c.label)}</option>`).join('') + `</select></label>` +
    `<div class="racts"><button class="dact" id="fndOpen">Record finding</button></div></div></details>` +
    (done.length ? `<details class="rpt" data-keep="resolved"${S.accOpen[`${t.file}:resolved`] ? ' open' : ''}><summary>Resolved findings (${done.length})</summary>` +
      `<div style="padding:6px 10px;display:flex;flex-direction:column;gap:6px">${done.map(f => findingHtml(t, f, acc)).join('')}</div></details>` : '') +
    `<h4>Review rounds</h4>` +
    (rounds.length ? roundHtml(rounds[0], true) +
      (rounds.length > 1 ? `<details class="rpt" data-keep="oldrounds"${S.accOpen[`${t.file}:oldrounds`] ? ' open' : ''}><summary>Earlier rounds (${rounds.length - 1})</summary>` +
        `<div style="padding:6px 10px;display:flex;flex-direction:column;gap:6px">${rounds.slice(1).map(r => roundHtml(r, false)).join('')}</div></details>` : '')
      : `<p class="dnote">No PR review has been filed on this card.</p>`) +
    about('rvhelp-pr', 'What a PR review is, and is not',
      `The implementation at a code revision, and the findings recorded against it. ` +
      `A round's verdict is about the revision it names. A request for changes stays what it was; what happened after it is the fixes and resolutions above, and formal GitHub approval is a separate fact in Overview's Signals.`) +
    `</section>` + relevance;
}

/* Open one review where it lives — from Activity, the Overview or a
   comment's link: the Reviews section, that review unfolded (and the
   fold of earlier ones holding it), scrolled to and focused. */
function openReview(t, id) {
  S.dtab[t.file] = 'reviews';
  syncRoute();
  S.accOpen[`${t.file}:${id}`] = true;
  const acc = (S.acc[t.file] || {}).data;
  if (acc) {
    if (acc.rounds.length > 1 && acc.rounds.slice(0, -1).some(r => r.id === id)) S.accOpen[`${t.file}:oldrounds`] = true;
  }
  renderDrawer();
  requestAnimationFrame(() => {
    const el = document.getElementById(`rvw-${id}`);
    if (!el) { toast('that review is no longer on this card', true); return; }
    el.scrollIntoView({ block: 'start' });
    const sum = el.querySelector('summary');
    if (sum) sum.focus({ preventScroll: true });
  });
}

/* A relevance review's own two actions. Comment saves and starts
   nothing; Update card from this review is armed, because it asks an
   agent. Both carry a request identity, so a double click or a resent
   request is one comment and one job. The text survives a redraw. */
function wireReviews(t) {
  const body = $('#drawerbody');
  body.querySelectorAll('.rvcompose').forEach(box => {
    const id = box.dataset.review;
    const key = `${t.file}::${id}`;
    const area = box.querySelector('textarea');
    area.addEventListener('input', () => {
      S.commentDraft[key] = area.value;
      delete S.refineKey[key];
      delete S.commentKey[key];
    });
    const comment = box.querySelector('[data-rvact="comment"]');
    comment.addEventListener('click', async () => {
      if (comment.disabled) return;
      const request = S.commentKey[key] = S.commentKey[key] || newRequestKey();
      comment.disabled = true;
      const data = await postCard('/api/card/comment',
        { file: t.file, stage: t.stage, body: area.value, review: id, request });
      comment.disabled = false;
      if (!data) return;
      S.commentDraft[key] = '';
      delete S.commentKey[key];
      toast(data.comment.duplicate ? 'that comment was already saved'
        : `comment ${data.comment.id} saved on the review` +
          (data.comment.committed ? '' : ' — written to the card, not committed'));
      afterCardWrite(t);
    });
    wireAction(box.querySelector('[data-rvact="refine"]'), `${key}::refine`, {
      label: 'Update card from this review', confirm: true,
      run: async () => {
        const request = S.refineKey[key] = S.refineKey[key] || newRequestKey();
        const data = await postCard('/api/card/refine',
          { file: t.file, stage: t.stage, text: area.value, review: id, request });
        if (!data) return false;
        S.commentDraft[key] = '';
        delete S.refineKey[key];
        toast(data.duplicate ? 'that refinement was already asked for'
          : `${data.job.name || 'an agent'} is proposing edits to the brief from this review — nothing changes until you apply`);
        afterCardWrite(t);
        return true;
      },
    });
  });
}

/* Reports a run wrote for this card that this board has not filed, or
   filed without committing (task 169): read from this board's filing
   record, which a replica does not have. Retry files the retained report
   under its original heading on this card, or commits it; nothing is
   appended twice and nothing goes anywhere else. */
const filingStore = {};
const filing = resource({
  store: filingStore,
  url: (file) => `/api/reports/filing?card=${encodeURIComponent(file)}`,
  ask: (url) => fetch(url),
});

function needsFiling(o) {
  return ['pending', 'unfiled', 'conflict'].includes(o.status) ||
    (o.status === 'filed' && o.commit && ['failed', 'unknown'].includes(o.commit.state));
}

function filingBanner(t, reviewsOnly) {
  filing.read(t.file, accStamp(t), {
    done: () => { if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer(); },
  });
  const entry = filingStore[t.file];
  const ops = ((entry && entry.data && entry.data.operations) || []).filter(needsFiling)
    .filter(o => !reviewsOnly || o.review);
  if (!ops.length) return '';
  const item = (o) => {
    const filed = o.status === 'filed';
    const words = filed
      ? `filed on this card; ${o.commit.state === 'unknown' ? 'the board stopped before its commit was recorded' : 'its commit failed'}`
      : o.status === 'conflict' ? 'not filed — this card holds a different report under the same heading'
      : o.status === 'pending' ? 'not filed yet — the filing was interrupted' : 'not filed';
    const action = filed ? 'commit'
      : (o.status === 'conflict' || o.code === 'board-changed') ? null : 'file';
    return `<li><span><b>${esc(o.runName || 'A run')}'s ${esc(String(o.kind || 'report').toLowerCase())}</b> — ` +
      `${esc(words)}${o.detail ? `: ${esc(o.detail)}` : ''}.` +
      (o.retained ? ' The report is retained on this board under its original heading.' : '') +
      (o.next ? ` <span class="dnote">Next: ${esc(o.next)}.</span>` : '') + '</span>' +
      (action ? ` <button class="act" data-rfop="${esc(o.operation)}" data-rfaction="${action}">` +
        `${action === 'file' ? 'Retry filing' : 'Retry commit'}</button>` : '') +
      ` <a class="dlink" href="/brain" data-route>Filing in the Brain</a></li>`;
  };
  return `<div class="dfiling" role="status"><p><b>${ops.length} report${ops.length === 1 ? '' : 's'} ` +
    `${ops.length === 1 ? 'needs' : 'need'} you.</b> A run's report that was not filed, or not committed, is ` +
    'kept on this board only.</p><ul>' + ops.map(item).join('') + '</ul></div>';
}

function wireFiling(t) {
  $('#drawerbody').querySelectorAll('[data-rfop]').forEach(btn => {
    const op = btn.dataset.rfop;
    const action = btn.dataset.rfaction;
    wireAction(btn, `${t.file}::rf:${op}:${action}`, {
      label: action === 'file' ? 'Retry filing' : 'Retry commit', confirm: true,
      run: async () => {
        const res = await post('/api/reports/filing/retry', { operation: op, action });
        if (!res.ok) { toast(res.error || 'the board refused that', true); return false; }
        const o = res.data.operation;
        toast(o.duplicate ? 'Already done — nothing was written again.'
          : o.status === 'filed' ? (action === 'file' ? 'Filed on this card.' : `Commit: ${o.commit ? o.commit.state : 'done'}.`)
          : `Not filed: ${o.detail || o.status}.`, o.status !== 'filed');
        filing.read(t.file, accStamp(t), { force: true });
        loadAcc(t, true);
        loadState();
        return true;
      },
    });
  });
}

function activityHtml(t, acc) {
  const items = [];
  const p = (S.state.progress || {})[t.file];
  if (p) {
    fetchProgress(p.agent);
    const full = progressOf(p.agent, t.file);
    for (const e of ((full && full.history) || (p.current ? [p.current] : []))) {
      const said = [e.stage, e.doing, ...(e.done || []).map(d => 'done: ' + d), e.blocked ? 'blocked: ' + e.blocked : '',
                    (e.criteria || []).length ? 'reports criteria ' + e.criteria.join(', ') : ''].filter(Boolean).join(' · ');
      items.push({ at: whenOf(e.at), src: 'agent-reported', text: said || e.narrative || '' });
    }
  }
  if (acc) {
    for (const r of acc.reports) {
      // a review is kept once, in Reviews: Activity dates it and links to
      // that exact copy rather than drawing a second one (task 84)
      if (r.review) {
        const rel = r.reviewType === 'relevance'
          ? (acc.relevance || []).find(x => x.id === r.review) : null;
        const round = r.reviewType === 'pr' ? acc.rounds.find(x => x.id === r.review) : null;
        items.push({ at: whenOf(r.at), src: `${r.reviewType === 'relevance' ? 'relevance review' : 'PR review'}${r.by ? ' · ' + r.by : ''}`,
          goreview: r.review, key: `rep:${r.review}`,
          text: rel ? `Filed: ${rel.verdict || rel.verdictLine || 'no verdict line'}`
            : round ? `Filed: ${roundLine(round)}` : 'Filed' });
        continue;
      }
      items.push({ at: whenOf(r.at), src: `${r.kind}${r.by ? ' · ' + r.by : ''}`,
        report: r.text, key: `rep:${r.kind}|${r.at}|${r.by}` });
    }
    for (const c of [...(acc.items || acc.criteria), ...acc.removed]) {
      for (const r of c.history) items.push({ at: whenOf(r.at),
        src: r.source === 'agent' ? `acceptance · ${ROLE_WORDS[r.role] || r.role} ${r.actor || ''}`
                                  : `acceptance · ${r.actor || ''}`,
        text: `${(acc.states || {})[r.state] || r.state} — ${(c.label || r.text || c.id)}` +
              `${r.note ? ': ' + r.note : r.reason ? ': ' + r.reason : ''}` });
    }
    for (const f of acc.findings) {
      for (const e of f.events) items.push({ at: whenOf(e.at), src: `finding · ${e.actor || ''}`,
        text: `${e.event} — ${f.text}${e.event !== 'opened' && e.note ? ': ' + e.note : ''}` });
    }
  }
  const disc = ((S.disc[t.file] || {}).data || {}).entries || [];
  for (const e of disc) items.push({ at: whenOf(e.at), src: `${e.kind === 'decision' ? 'decision ' + (e.outcome || '') : 'comment'} · ${e.author || ''}`,
    text: e.body || '', ...(e.review ? { goreview: e.review, link: 'about a relevance review' } : {}) });
  items.sort((a, b) => b.at - a.at);
  // a report's key is what it is, never where it sits: a record arriving
  // above it must not open, close or move a different one
  const row = (it) => it.goreview
    ? `<div class="tl"${it.key ? ` data-keep="${esc(it.key)}"` : ''}><time>${esc(it.at ? new Date(it.at).toLocaleString() : '—')}</time>` +
      `<span><span class="src">${esc(it.src)}</span><div class="tx">${esc(it.text)}` +
      ` <button class="dlink" data-goreview="${esc(it.goreview)}">${it.link ? esc(it.link) + ' — open it' : 'Open in Reviews'}</button></div></span></div>`
    : it.report
    ? `<details class="rpt" data-keep="${esc(it.key)}"${S.accOpen[`${t.file}:${it.key}`] ? ' open' : ''}><summary><b>${esc(it.src)}</b>` +
      `<span class="ameta">${esc(it.at ? new Date(it.at).toLocaleString() : '')}</span></summary>` +
      `${reportHtml(t, it.report, it.key)}</details>`
    : `<div class="tl"><time>${esc(it.at ? new Date(it.at).toLocaleString() : '—')}</time>` +
      `<span><span class="src">${esc(it.src)}</span><div class="tx">${esc(it.text)}</div></span></div>`;
  return filingBanner(t, false) + `<p class="dnote">Newest first: progress the runs reported (labelled as theirs), work reports, reviews (dated here, read in Reviews), comments and decisions, and acceptance and finding records. Raw reads, edits and commands are technical detail, in this card's sessions.</p>` +
    (items.length ? `<div class="tline">${items.map(row).join('')}</div>` : `<p class="dnote">Nothing has happened on this card yet.</p>`);
}

async function postAcc(t, url, payload) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(payload) });
  let data = {};
  try { data = await res.json(); } catch { /* an empty reply is a refusal too */ }
  if (!res.ok) {
    toast(data.error || 'the board refused that', true);
    if (data.stale) loadAcc(t, true);
    return null;
  }
  loadAcc(t, true);
  loadState();
  return data;
}

function wireDetail(t) {
  const body = $('#drawerbody');
  wireInputs(t);
  wireResult(t);
  wireTiming(t);
  wireFiling(t);
  const retry = body.querySelector('#dretry');
  if (retry) retry.addEventListener('click', () => { loadDetail(t, true); renderDrawer(); });
  const tabs = [...body.querySelectorAll('.dtabs [role="tab"]')];
  const go = (key, focus) => {
    S.dtab[t.file] = key;
    renderDrawer();
    // each section has its own address (task 134): ?tab=<section>
    syncRoute();
    if (focus) requestAnimationFrame(() => { const b = document.getElementById('dtab-' + key); if (b) b.focus(); });
  };
  tabs.forEach((btn, i) => {
    btn.addEventListener('click', () => go(btn.dataset.tab, true));
    btn.addEventListener('keydown', (e) => {
      const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      go(tabs[(to + tabs.length) % tabs.length].dataset.tab, true);
    });
  });
  body.querySelectorAll('[data-goto]').forEach(btn => btn.addEventListener('click', () => go(btn.dataset.goto, true)));
  // the brief's section index: to the heading, which takes focus, so a
  // keyboard reader lands where a sighted one does (task 149)
  body.querySelectorAll('[data-brief-h]').forEach(btn => btn.addEventListener('click', () => {
    const h = body.querySelectorAll('#dsec-brief .bbody h2')[Number(btn.dataset.briefH)];
    if (!h) return;
    h.setAttribute('tabindex', '-1');
    h.scrollIntoView({ block: 'start' });
    h.focus({ preventScroll: true });
  }));
  // a link to one exact review; the discussion's and a thread's own rows
  // are wired with the rows (wireCommentRows), which a live read redraws
  body.querySelectorAll('[data-goreview]').forEach(btn => {
    if (btn.closest('#disc-live, .rvthread')) return;
    btn.addEventListener('click', () => openReview(t, btn.dataset.goreview));
  });
  $('#dwide').addEventListener('click', () => { S.dwide = !S.dwide; renderDrawer(); $('#dwide').focus(); });
  // open/closed folds and unsent text survive a live redraw — a fold's
  // choice recorded by its toggle, or by the redraw that replaces it
  // before that queued event arrives (lib/folds.js, task 155)
  wireFolds(S.accOpen, t.file, body);
  // copies the report as filed; where the clipboard is refused, the text
  // is selected instead so the reader's own copy takes it
  body.querySelectorAll('.rcopy').forEach(btn => btn.addEventListener('click', async () => {
    const raw = btn.closest('.rsrc').querySelector('.rraw');
    try {
      await navigator.clipboard.writeText(raw.textContent);
      toast('original Markdown copied');
    } catch {
      const range = document.createRange();
      range.selectNodeContents(raw);
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(range);
      raw.focus();
      toast('selected — copy it with your keyboard');
    }
  }));
  body.querySelectorAll('.dsec textarea[id], .dsec input[id], .dsec select[id]').forEach(el => {
    if (el.id === 'commentbox') return;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => { S.accDraft[el.id] = el.value; });
  });
  // There is no tracking step (task 190): a decision on a criterion that
  // has no id yet names it by its revision (`@<rev>`), and that decision
  // is the write that gives the card's criteria their ids.
  body.querySelectorAll('[data-decide]').forEach(btn => btn.addEventListener('click', async () => {
    const form = btn.closest('.aform');
    const cid = form.dataset.crit;
    const dom = form.dataset.dom || cid;
    const payload = { file: t.file, stage: t.stage, criterion: cid, rev: form.dataset.rev,
      basis: form.dataset.basis || null, state: $(`#acc-state-${dom}`).value,
      note: $(`#acc-note-${dom}`).value, ref: $(`#acc-ref-${dom}`).value,
      evidence: $(`#acc-ev-${dom}`).value || null, request: newRequestKey() };
    btn.disabled = true;
    const data = await postAcc(t, '/api/card/acceptance/decide', payload);
    btn.disabled = false;
    if (!data) return;
    for (const k of ['state', 'note', 'ref', 'ev']) delete S.accDraft[`acc-${k}-${dom}`];
    toast(data.duplicate ? 'that decision was already recorded' : `recorded: ${payload.state}`);
  }));
  body.querySelectorAll('[data-finding] [data-event]').forEach(btn => btn.addEventListener('click', async () => {
    const form = btn.closest('.aform');
    const fid = form.dataset.finding;
    const ev = form.querySelector('select');
    const payload = { file: t.file, stage: t.stage, event: btn.dataset.event, finding: fid,
      basis: form.dataset.basis || null, note: $(`#fnd-note-${fid}`).value,
      evidence: (ev && ev.value) || null, request: newRequestKey() };
    btn.disabled = true;
    const data = await postAcc(t, '/api/card/finding', payload);
    btn.disabled = false;
    if (!data) return;
    delete S.accDraft[`fnd-note-${fid}`];
    toast(`finding ${fid}: ${payload.event}`);
  }));
  const open = $('#fndOpen');
  if (open) open.addEventListener('click', async () => {
    const payload = { file: t.file, stage: t.stage, event: 'opened', text: $('#fnd-text').value,
      round: $('#fnd-round').value || null, criterion: $('#fnd-crit').value || null,
      request: newRequestKey() };
    open.disabled = true;
    const data = await postAcc(t, '/api/card/finding', payload);
    open.disabled = false;
    if (!data) return;
    for (const k of ['fnd-text', 'fnd-round', 'fnd-crit']) delete S.accDraft[k];
    toast('finding recorded');
  });
}

/* The card face's acceptance and findings chips: a count of criteria a
   named person accepted (never a completion ratio) and the findings still
   open. Each opens its section of the detail view. */
function acceptanceChips(task) {
  // the information chip rides the same list (task 132): a destination to
  // a section of the detail view, built and wired as these are
  const info = informationChip(task);
  const a = ((S.state || {}).acceptance || {})[task.file];
  if (!a) return info ? [info] : [];
  const out = info ? [info] : [];
  if (a.total) {
    const behind = a.needsWork + a.recheck + (a.decision || 0);
    const o = a.outcome || {};
    // Acceptance is not a default ceremony (design #94 §8.1): the count is
    // a tool, and earns the face only for a current, actionable problem —
    // a criterion not met, needing a recheck, or waiting on your decision.
    out.push({ label: `${a.accepted}/${a.total} accepted`, pre: '☑', cls: behind ? 'bad' : '',
      section: 'acceptance', face: !!behind,
      title: `${o.headline || 'Acceptance'} — ${a.accepted} of ${a.total} criteria accepted` +
        `${o.next ? '. ' + o.next : ''}` });
  }
  if (a.openFindings || a.fixReported) {
    out.push({ label: a.openFindings ? `${a.openFindings} open finding${a.openFindings === 1 ? '' : 's'}`
                                     : `${a.fixReported} fix${a.fixReported === 1 ? '' : 'es'} to recheck`,
      pre: '◇', cls: a.openFindings ? 'bad' : 'accent', section: 'reviews', face: true,
      title: 'Review findings on record — open the Reviews section' });
  }
  return out;
}

/* The card face's information chip (task 132): a current problem — a
   required input or tool that would refuse a launch — is on the face;
   a card whose information is ready keeps it in its tools. */
function informationChip(task) {
  const i = ((S.state || {}).information || {})[task.file];
  if (!i || task.stage === 'done') return null;
  const working = ['to-do', 'in-progress', 'review'].includes(task.stage);
  if (i.state === 'needs-information')
    return { label: 'needs information', pre: 'ⓘ', cls: 'bad', section: 'inputs', face: working,
             title: `${i.sentence} — open the Inputs tab` };
  if (i.state === 'checking')
    return { label: 'checking information', pre: 'ⓘ', cls: 'accent', section: 'inputs', face: working,
             title: 'Bench is checking this card\'s inputs' };
  if (i.state === 'unchecked')
    return { label: 'information unchecked', pre: 'ⓘ', cls: '', section: 'inputs', face: working,
             title: `${i.sentence} — Recheck information in the Inputs tab` };
  return { label: 'inputs ready', pre: 'ⓘ', cls: '', section: 'inputs', face: false,
           title: i.optional ? `Ready; ${i.optional} optional input(s) will be left out` : 'Every required input and tool is usable' };
}

export {
  ACC_GLYPH, accActor, acceptanceChips, acceptanceHtml, accStamp,
  activityHtml, briefHtml, cardSplit, criterionHtml, dependencyHtml, drawerAnchor, drawerTab,
  lastResultHtml, requiredHtml, reviewFactsHtml, standsHtml, workHtml,
  detailKey, dsec, DTABS, dtabsHtml, evidenceOptions, findingHtml, inputsHtml, loadAcc, loadDetail,
  openReview, withDetail,
  overviewHtml, postAcc, progressFact, recordHtml, RELEVANCE_TONE,
  relevanceHtml, relevanceLine, reportHtml, reviewRefineWhy, reviewsHtml,
  reviewThreadHtml, roundLine, sinceRound, STAGE_WORDS, stampOf, whenOf,
  wireDetail, wireReviews
};
