/* The detail view's sections. */
import { agentOnTask, loadState } from '../../app/data.js';
import { S } from '../../app/state.js';
import { fetch } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { md } from '../../lib/md.js';
import { resource, staleWords } from '../../lib/resource.js';
import { ago } from '../../lib/time.js';
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
const DTABS = [['overview', 'Overview'], ['brief', 'Brief'], ['acceptance', 'Acceptance'],
               ['reviews', 'Reviews'], ['activity', 'Activity']];
const ACC_GLYPH = { accepted: '✓', 'needs-work': '✕', recheck: '↻', reported: '·', pending: '' };
const STAGE_WORDS = { backlog: 'Backlog', 'to-do': 'To do', 'in-progress': 'In progress',
                      review: 'Review', done: 'Done' };

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
    acceptance: acc && acc.tracked ? `${acc.counts.accepted}/${acc.counts.total}` : '',
    reviews: acc && (acc.findingCounts.open + acc.findingCounts['fix-reported'])
      ? String(acc.findingCounts.open + acc.findingCounts['fix-reported']) : '',
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

/* The Overview leads with the card's one delivery status — the server's
   reading, the same one the face and the merge dialog draw — then its
   merge requirements as supporting detail, then the facts. Nothing here
   works out a next step of its own (task 99). */
function overviewHtml(t, acc, failBlock, signals, plan, members) {
  const d = deliveryOf(t);
  const full = loadDeliveryFull(t, () => {
    if (S.selected && S.selected.file === t.file && !S.fileView) renderDrawer();
  });
  const counts = acc ? acc.counts : null;
  const last = acc && acc.rounds.length ? acc.rounds[acc.rounds.length - 1] : null;
  const lastRel = acc && (acc.relevance || []).length ? acc.relevance[acc.relevance.length - 1] : null;
  const link = (tab, text) => `<button class="dlink" data-goto="${tab}">${esc(text)}</button>`;
  const accFact = !acc ? 'reading…'
    : !acc.tracked ? `${link('acceptance', 'Acceptance results not recorded')}`
    : `${link('acceptance', `${counts.accepted} of ${counts.total} accepted`)}` +
      ['reported', 'pending', 'needs-work', 'recheck'].filter(k => counts[k])
        .map(k => ` · ${counts[k]} ${esc(acc.states[k])}`).join('');
  const fc = acc ? acc.findingCounts : null;
  const findFact = !acc ? 'reading…'
    : !acc.findings.length ? (acc.rounds.length ? 'No findings recorded against the reviews.' : '')
    : `${link('reviews', `${fc.open} open`)} · ${fc['fix-reported']} fix reported` +
      `${fc['fix-reported'] ? ' — recheck and resolve, or reopen' : ''} · ${fc.resolved} resolved`;
  const fact = (dt, dd) => dd ? `<dt>${dt}</dt><dd>${dd}</dd>` : '';
  return deliveryStatusHtml(t, d, 'ov') + failBlock +
    requirementsHtml(t, d, full, 'ov') +
    `<dl class="dfacts">` +
    fact('Stage', esc(STAGE_WORDS[t.stage] || t.stage) + (t.assignee ? ` · held by ${esc(t.assignee)}` : '')) +
    fact('Progress', esc(progressFact(t))) +
    fact('Acceptance', accFact) +
    fact('Findings', findFact) +
    fact('Last review', last ? esc(roundLine(last)) +
      (sinceRound(last, acc) ? ` — ${esc(sinceRound(last, acc))}` : '') +
      ` · <button class="dlink" data-goreview="${esc(last.id)}">open this review</button>` : '') +
    // the brief's own review, apart from the code's: never an approval
    fact('Relevance', lastRel ? esc(relevanceLine(lastRel)) +
      ` · <button class="dlink" data-goreview="${esc(lastRel.id)}">open this review</button>` +
      ((acc.relevance || []).length > 1 ? ` · ${acc.relevance.length} on record` : '') : '') +
    `</dl>` +
    (last ? `<p class="dnote">A reviewer's verdict is about the revision it names. GitHub's formal approval and Bench's own verdict are separate facts, in Signals below.</p>` : '') +
    stackBlock(t) + signals + members +
    `<h3>Tools</h3><div id="drawertools"><p class="dnote">This card has no tools in its current stage.</p></div>` +
    (plan ? `<details class="rpt"${S.accOpen[t.file + ':plan'] ? ' open' : ''} data-keep="plan">` +
            `<summary>Branch and plan</summary><div style="padding:6px 10px">${plan}</div></details>` : '');
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
  return { ...t, body: d.body, parts: d.parts, words: d.words, textRev: d.rev };
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
  return (note ? `<p class="dnote dstale" role="status">${esc(note)}</p>` : '') +
    `<div class="dbody">${md(head + '\n' + withoutDiscussion(parts.brief))}</div>` +
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

function recordHtml(r) {
  const word = { accepted: 'accepted', 'needs-work': 'needs work', recheck: 'recheck requested',
                 pending: 'reset to pending', reported: 'reported' }[r.state] || r.state;
  const src = { manual: 'confirmed by a person', evidence: 'mapped to bench evidence',
                tracked: 'tracking started', 'legacy-tick': 'ticked by hand before tracking' }[r.source] || r.source;
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

function criterionHtml(t, c) {
  const key = `${t.file}:${c.id}`;
  const open = S.accOpen[key] ?? false;
  const v = (id) => esc(S.accDraft[id] || '');
  const ev = c.evidence;
  const claim = c.claim;
  return `<details class="arow ${esc(c.state)}" data-keep="${esc(c.id)}"${open ? ' open' : ''}>` +
    `<summary><span class="abox" aria-hidden="true">${ACC_GLYPH[c.state] || ''}</span>` +
    `<span class="alabel" title="${esc(c.text)}">${esc(c.label)}</span>` +
    `<span class="aword">${esc(c.word)}</span></summary>` +
    `<div class="abody"><p class="afull">${esc(c.text)}</p>` +
    `<span class="ameta">id ${esc(c.id)} · revision ${esc(c.rev)}${c.legacyTick ? ' · ticked by hand in the brief' : ''}</span>` +
    (c.reason ? `<p class="dnote${['recheck', 'needs-work'].includes(c.state) ? ' bad' : ''}">${esc(c.reason)}</p>` : '') +
    (ev ? `<div class="arec">supporting evidence ${esc(ev.id)}: ${esc(ev.check || '')} · <b>${esc(ev.outcome)}</b>` +
          `${ev.commit ? ' at ' + esc(ev.commit) : ''} · read from bench's record now</div>` : '') +
    (claim ? `<div class="arec">${esc(claim.agent)} reported it implemented, ${esc(ago(claim.at))} ago` +
             ` — agent-reported, not a result${(claim.evidence || []).length ? ` · proposed evidence ${esc(claim.evidence.join(', '))}` : ''}</div>` : '') +
    (c.history.length ? `<h3>Record</h3>` + c.history.map(recordHtml).join('') : '<p class="dnote">Nothing recorded about it yet.</p>') +
    `<div class="aform" data-crit="${esc(c.id)}" data-rev="${esc(c.rev)}" data-basis="${esc(c.basis || '')}">` +
    `<label>Decision<select id="acc-state-${esc(c.id)}">` +
    [['accepted', 'Accept'], ['needs-work', 'Needs work'], ['recheck', 'Needs recheck'], ['pending', 'Back to pending']]
      .map(([k, w]) => `<option value="${k}"${(S.accDraft[`acc-state-${c.id}`] || 'accepted') === k ? ' selected' : ''}>${w}</option>`).join('') +
    `</select></label>` +
    `<label>Note — what you checked and how<textarea id="acc-note-${esc(c.id)}" rows="2">${v(`acc-note-${c.id}`)}</textarea></label>` +
    `<label>Reference (optional) — a screenshot, URL or file<input id="acc-ref-${esc(c.id)}" value="${v(`acc-ref-${c.id}`)}"></label>` +
    `<label>Supporting evidence (optional, for Accept)<select id="acc-ev-${esc(c.id)}">${evidenceOptions(t, `acc-ev-${c.id}`)}</select></label>` +
    `<div class="racts"><button class="dact" data-decide="${esc(c.id)}">Record decision</button></div>` +
    `<p class="dnote">Recorded as ${esc(accActor(t) || 'this board')}, against revision ${esc(c.rev)} of this criterion.</p>` +
    `</div></div></details>`;
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
  const legacy = acc.untracked.map(c => `<li>${c.legacyTick ? '<b>[x]</b> ' : '[ ] '}${esc(c.text)}` +
    `${c.legacyTick ? ' <span class="ameta">ticked by hand — a legacy claim, not an acceptance</span>' : ''}</li>`).join('');
  const trackBtn = (word) => `<div class="racts"><button class="dact" id="accTrack">` +
    `${actLabel(word, 'click again — adds ids to the brief', 'tracking…')}</button></div>`;
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
  if (!acc.tracked) {
    return signoffLine + `<h3>Acceptance results not recorded</h3>` +
      (acc.untracked.length
        ? `<p class="dnote">Nothing on record says whether these criteria were met. The card's stage says nothing about it either, and nothing here is inferred from it.</p>` +
          `<ul class="chain" style="list-style:none">${legacy}</ul>` +
          trackBtn('Track acceptance') +
          `<p class="dnote">Tracking gives each criterion a stable id on its own line in the brief and records it as pending — hand-written ticks are kept, and recorded as legacy claims awaiting confirmation. It changes no wording, and it is the only thing that assigns ids; opening this view never does.</p>`
        : `<p class="dnote">This brief has no checklist criteria (lines like <code>- [ ] …</code>) to track.</p>`);
  }
  const breakdown = ['reported', 'pending', 'needs-work', 'recheck']
    .filter(k => counts[k]).map(k => `${counts[k]} ${esc(acc.states[k])}`).join(' · ');
  // Done is not acceptance — said here, as a fact about the records, and
  // not as a step on the Overview. Whether a rule asked for a sign-off is
  // the server's answer, never assumed (design #94 §8.1, §8.3)
  const unaccepted = t.stage === 'done' ? counts.total - counts.accepted : 0;
  return signoffLine + `<p class="acount"><b>${counts.accepted} of ${counts.total}</b> criteria accepted` +
    `${breakdown ? ` · ${breakdown}` : ''}</p>` +
    (unaccepted ? `<p class="dnote">Delivered, and ${unaccepted} criteri${unaccepted === 1 ? 'on was' : 'a were'} ` +
      `never accepted on record. Done is not acceptance` +
      (so.required ? `.</p>` : `; no merge rule here requires a sign-off.</p>`) : '') +
    `<p class="dnote">Accepted means a named person confirmed it, or mapped one of bench's own current passing records to it. It is not a measure of how complete the work is, and an agent's report never ticks one.</p>` +
    acc.criteria.map(c => criterionHtml(t, c)).join('') +
    (acc.untracked.length ? `<h3>New criteria, not yet tracked</h3><ul class="chain">${legacy}</ul>` +
      `<p class="dnote">They count as pending.</p>` + trackBtn('Track new criteria') : '') +
    (acc.removed.length ? `<details class="rpt"><summary>Removed from the brief (${acc.removed.length}) — history, not counted</summary>` +
      `<div style="padding:6px 10px">` + acc.removed.map(r => `<p class="afull">${esc(r.text || r.id)}</p>` +
        r.history.map(recordHtml).join('')).join('') + `</div></details>` : '');
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
    reportHtml(t, r.report, r.id) +
    `<div class="discussion rvdisc" role="group" aria-label="Comments on this review"><h4>Comments on this review</h4>` +
    `<div class="rvthread" data-review="${esc(r.id)}">${reviewThreadHtml(t, r.id)}</div>` +
    `<div class="composer rvcompose" data-review="${esc(r.id)}">` +
    `<label for="rvbox-${esc(r.id)}">Comment, or what to take from this review into the brief</label>` +
    `<textarea id="rvbox-${esc(r.id)}" rows="3">${esc((S.commentDraft || {})[key] || '')}</textarea>` +
    `<div class="racts"><button class="dact" data-rvact="comment">Comment</button>` +
    `<button class="dact" data-rvact="refine"${cannot ? ' disabled' : ''}>` +
    `${actLabel('✎ Update card from this review', 'click again — asks an agent', 'asking…')}</button></div>` +
    `<p class="dnote bad rvwhy" role="status">${reviewRefineWhy(d)}</p>` +
    `<p class="dnote">Comment only saves your note, linked to this review. Update card from this review ` +
    `sends this review and your instruction (or, if you leave it empty, “apply its recommendations”) ` +
    `to an agent that proposes edits to the brief. It is not another review and starts no implementation; ` +
    `the brief changes only when you apply the proposal.</p></div></div></details>`;
}

function reviewsHtml(t, acc) {
  if (!acc) return `<p class="dnote">reading reviews…</p>`;
  const live = acc.findings.filter(f => f.state !== 'resolved');
  const done = acc.findings.filter(f => f.state === 'resolved');
  const rounds = [...acc.rounds].reverse();
  const rel = [...(acc.relevance || [])].reverse();
  const roundHtml = (r, open) => `<details class="rpt" id="rvw-${esc(r.id)}" data-keep="${esc(r.id)}"${(S.accOpen[`${t.file}:${r.id}`] ?? open) ? ' open' : ''}>` +
    `<summary id="rvs-${esc(r.id)}"><b>${esc(roundLine(r))}</b>${sinceRound(r, acc) ? `<span class="dnote">${esc(sinceRound(r, acc))}</span>` : ''}` +
    `<span class="ameta">${esc(r.id)}</span></summary>${reportHtml(t, r.report, r.id)}</details>`;
  const fc = acc.findingCounts;
  const relevance = `<section class="rvgroup" aria-labelledby="rvg-rel-${esc(t.number || 'x')}">` +
    `<h3 id="rvg-rel-${esc(t.number || 'x')}">Relevance reviews <span class="n">${rel.length}</span></h3>` +
    `<p class="dnote">Whether the brief is still needed and current, and what to change in it. ` +
    `A relevance verdict is about the card's definition: it is not a code review, approves nothing ` +
    `and counts toward no acceptance or merge requirement.</p>` +
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
  return relevance +
    `<section class="rvgroup" aria-labelledby="rvg-pr-${esc(t.number || 'x')}">` +
    `<h3 id="rvg-pr-${esc(t.number || 'x')}">PR reviews <span class="n">${rounds.length}</span></h3>` +
    `<p class="dnote">The implementation at a code revision, and the findings recorded against it.</p>` +
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
    `<p class="dnote">A round's verdict is about the revision it names. A request for changes stays what it was; what happened after it is the fixes and resolutions above, and formal GitHub approval is a separate fact in Overview's Signals.</p>` +
    `</section>`;
}

/* Open one review where it lives — from Activity, the Overview or a
   comment's link: the Reviews section, that review unfolded (and the
   fold of earlier ones holding it), scrolled to and focused. */
function openReview(t, id) {
  S.dtab[t.file] = 'reviews';
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
    for (const c of [...acc.criteria, ...acc.removed]) {
      for (const r of c.history) items.push({ at: whenOf(r.at), src: `acceptance · ${r.actor || ''}`,
        text: `${r.state} — ${(c.label || r.text || c.id)}${r.note ? ': ' + r.note : ''}` });
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
  return `<p class="dnote">Newest first: progress the runs reported (labelled as theirs), work reports, reviews (dated here, read in Reviews), comments and decisions, and acceptance and finding records. Raw reads, edits and commands are technical detail, in the Sessions view.</p>` +
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
  const retry = body.querySelector('#dretry');
  if (retry) retry.addEventListener('click', () => { loadDetail(t, true); renderDrawer(); });
  const tabs = [...body.querySelectorAll('.dtabs [role="tab"]')];
  const go = (key, focus) => {
    S.dtab[t.file] = key;
    renderDrawer();
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
  // a link to one exact review; the discussion's and a thread's own rows
  // are wired with the rows (wireCommentRows), which a live read redraws
  body.querySelectorAll('[data-goreview]').forEach(btn => {
    if (btn.closest('#disc-live, .rvthread')) return;
    btn.addEventListener('click', () => openReview(t, btn.dataset.goreview));
  });
  $('#dwide').addEventListener('click', () => { S.dwide = !S.dwide; renderDrawer(); $('#dwide').focus(); });
  // open/closed folds and unsent text survive a live redraw
  body.querySelectorAll('details[data-keep], details[data-fold]').forEach(d => d.addEventListener('toggle', () => {
    S.accOpen[`${t.file}:${d.dataset.keep || d.dataset.fold}`] = d.open;
  }));
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
  const acc = (S.acc[t.file] || {}).data;
  body.querySelectorAll('#accTrack').forEach(btn => wireAction(btn, `${t.file}::acc-track`, {
    label: 'Track acceptance', confirm: true,
    run: async () => {
      if (!acc) return false;
      const data = await postAcc(t, '/api/card/acceptance/track',
        { file: t.file, stage: t.stage, briefSha: acc.briefSha, request: newRequestKey() });
      if (!data) return false;
      toast(`tracking ${data.tracked.length} criteria` + (data.committed ? '' : ' — written to the card, not committed'));
      return true;
    },
  }));
  body.querySelectorAll('[data-decide]').forEach(btn => btn.addEventListener('click', async () => {
    const form = btn.closest('.aform');
    const cid = form.dataset.crit;
    const payload = { file: t.file, stage: t.stage, criterion: cid, rev: form.dataset.rev,
      basis: form.dataset.basis || null, state: $(`#acc-state-${cid}`).value,
      note: $(`#acc-note-${cid}`).value, ref: $(`#acc-ref-${cid}`).value,
      evidence: $(`#acc-ev-${cid}`).value || null, request: newRequestKey() };
    btn.disabled = true;
    const data = await postAcc(t, '/api/card/acceptance/decide', payload);
    btn.disabled = false;
    if (!data) return;
    for (const k of ['state', 'note', 'ref', 'ev']) delete S.accDraft[`acc-${k}-${cid}`];
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
  const a = ((S.state || {}).acceptance || {})[task.file];
  if (!a) return [];
  const out = [];
  if (a.tracked) {
    const behind = a.needsWork + a.recheck;
    const parts = [['reported', a.reported], ['pending', a.pending], ['needing work', a.needsWork],
                   ['needing recheck', a.recheck]].filter(([, n]) => n).map(([w, n]) => `${n} ${w}`);
    // Acceptance is not a default ceremony (design #94 §8.1): the count is
    // a tool, and earns the face only when a person recorded that a
    // criterion needs work or a recheck — a current, actionable problem.
    out.push({ label: `${a.accepted}/${a.total} accepted`, pre: '☑', cls: behind ? 'bad' : '',
      section: 'acceptance', face: !!behind,
      title: `${a.accepted} of ${a.total} criteria accepted on record${parts.length ? ' — ' + parts.join(', ') : ''}. Not a measure of completion.` });
  }
  if (a.openFindings || a.fixReported) {
    out.push({ label: a.openFindings ? `${a.openFindings} open finding${a.openFindings === 1 ? '' : 's'}`
                                     : `${a.fixReported} fix${a.fixReported === 1 ? '' : 'es'} to recheck`,
      pre: '◇', cls: a.openFindings ? 'bad' : 'accent', section: 'reviews', face: true,
      title: 'Review findings on record — open the Reviews section' });
  }
  return out;
}

export {
  ACC_GLYPH, accActor, acceptanceChips, acceptanceHtml, accStamp,
  activityHtml, briefHtml, cardSplit, criterionHtml, drawerAnchor, drawerTab,
  detailKey, dsec, DTABS, dtabsHtml, evidenceOptions, findingHtml, loadAcc, loadDetail,
  openReview, withDetail,
  overviewHtml, postAcc, progressFact, recordHtml, RELEVANCE_TONE,
  relevanceHtml, relevanceLine, reportHtml, reviewRefineWhy, reviewsHtml,
  reviewThreadHtml, roundLine, sinceRound, STAGE_WORDS, stampOf, whenOf,
  wireDetail, wireReviews
};
