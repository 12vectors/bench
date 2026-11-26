/* A card's discussion, refining its brief, and its signals. */
import { agentOnTask, loadState } from '../../app/data.js';
import { S } from '../../app/state.js';
import { fetch, post } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { purpose } from '../../lib/agentpurposes.js';
import { limitsHtml } from '../../lib/agentshell.js';
import { askAttrs, panelOpen, registerAgent, renderPanel, savePanel } from '../../lib/panel.js';
import { resource, staleWords } from '../../lib/resource.js';
import { cardId } from '../../lib/route.js';
import { toast } from '../../lib/toast.js';
import { REVIEW_LABELS, actLabel, wireAction } from '../card.js';
import { deliveryOf, requirementsHeadline, shortSha } from '../delivery.js';
import { openReview, relevanceLine, reviewRefineWhy, reviewThreadHtml } from './sections.js';
import { gateState } from '../gates.js';

/* ── a card's discussion, and refining its brief (task 65) ─────────────

   Three actions, deliberately three. **Add comment** saves text to the
   card and starts nothing. **✎ Update card with agent** saves the text (or
   picks a comment already there) and asks for one refinement — armed,
   because it costs tokens. A proposal is a diff and an explanation, and
   the brief changes only on **Apply**; **Discard** records the decision
   and changes nothing. Every word a person or an agent wrote is drawn as
   text through esc(), never as markup. The panel reads
   /api/card/discussion — a GET that starts nothing — and re-reads it
   whenever the card file or its refinement state changes. */
const DISCUSSION_SECTION = /^## Discussion[ \t]*$[\s\S]*?(?=^## |(?![\s\S]))/m;
function withoutDiscussion(body) {
  return String(body || '').replace(DISCUSSION_SECTION, '');
}

const JOB_WORD = {
  queued: ['queued', ''], running: ['refining…', ''],
  proposed: ['proposal — not applied', ''], failed: ['failed', 'bad'],
  cancelled: ['cancelled', ''], applied: ['applied', 'good'],
  discarded: ['discarded', ''], superseded: ['superseded by a newer attempt', ''],
};

function newRequestKey() {
  return (crypto.randomUUID ? crypto.randomUUID()
                            : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
}

function discStamp(t) {
  const r = (S.state.refinements || {})[t.file];
  return `${t.stage}:${t.mtime}:${r ? r.id + r.status : '-'}:` +
    `${agentOnTask(t.file) ? agentOnTask(t.file).id : '-'}`;
}

/* The card's discussion, read on demand under lib/resource.js's policy
   (task 125). A read already in flight may have read the card before the
   write that asked for this one, so that ask is queued and made when the
   read lands — dropping it is how a saved comment never appeared. */
const discussion = resource({
  store: S.disc,
  url: (file, t) => `/api/card/discussion?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(t.stage)}`,
  ask: (url) => fetch(url),
});

function loadDiscussion(t, force) {
  discussion.read(t.file, discStamp(t), {
    force, arg: t,
    done: () => {
      if (S.selected && S.selected.file === t.file) renderDiscussion(S.selected);
      // the panel's conversation about this card reads the same discussion
      if (panelOpen()) renderPanel('live');
    },
  });
}

function diffHtml(diff) {
  const mark = { '+': '+', '-': '−', ' ': ' ', '…': '⋯' };
  return `<pre class="rdiff" aria-label="proposed change to the brief">` +
    (diff || []).map(d => `<span class="d d${{ '+': 'add', '-': 'del', ' ': 'eq', '…': 'gap' }[d.op] || 'eq'}">` +
      `${mark[d.op] || ' '} ${esc(d.text)}</span>`).join('\n') + `</pre>`;
}

function jobHtml(job, t) {
  const [word, cls] = JOB_WORD[job.status] || [job.status, ''];
  const who = job.name ? `${esc(job.name)} · ` : '';
  let out = `<div class="rjob ${cls}" data-job="${esc(job.id)}">` +
    `<div class="rhead"><span class="rstate">${esc(word)}</span>` +
    `<span class="mono">${who}${esc(job.id)}${job.attempt > 1 ? ` · attempt ${job.attempt}` : ''}</span></div>` +
    // a refinement asked from a review says which, and what it is not:
    // it proposes brief edits — it is not another review, and applying it
    // does not make the review's recommendations met (task 84)
    (job.review ? `<p class="dnote rvfrom">Asked from relevance review ` +
      `<span class="mono">${esc(job.review)}</span>${job.reviewHeading ? ` (${esc(job.reviewHeading)})` : ''}` +
      ` — a proposed edit to the brief, not another review and not an implementation run.` +
      (job.status === 'applied' ? ' Applied: the decision in the discussion records which sections changed; it does not mark the review’s recommendations as met or resolve any finding.' : '') +
      `</p>` : '');
  if (job.status === 'queued' || job.status === 'running') {
    out += `<p>Reading the card and the code. It proposes; nothing changes until you apply it.</p>` +
      `<div class="racts"><button class="dact" data-act="cancel">Cancel</button></div>`;
  } else if (job.status === 'proposed') {
    const fresh = job.fresh || { fresh: true };
    out += `<p class="rexpl">${esc(job.explanation)}</p>` +
      (job.noChange ? `<p class="dnote">It proposes no change to the brief.</p>` : '') +
      (job.sections && job.sections.length
        ? `<p class="dnote">Touches: ${esc(job.sections.join(', '))}` +
          (job.affected && job.affected.length
            ? ` · acceptance records to recheck: ${esc(job.affected.join(', '))}` : '') + `</p>`
        : '') +
      diffHtml(job.diff) +
      (fresh.fresh
        ? `<div class="racts"><button class="dact" data-act="apply">` +
          `${actLabel('Apply to the brief', 'click again — rewrites the brief', 'applying…')}</button>` +
          `<button class="dact" data-act="discard">Discard</button></div>` +
          `<p class="dnote">Kept on this board until you decide; the brief is unchanged.</p>`
        : `<div class="well bad"><span class="lead">·</span><div class="wbody">` +
          `<b>stale</b> — ${esc(fresh.why)}. It cannot be applied.</div></div>` +
          `<div class="racts"><button class="dact" data-act="retry">` +
          `${actLabel('↻ Regenerate', 'click again — asks an agent', 'asking…')}</button>` +
          `<button class="dact" data-act="discard">Discard</button></div>`);
  } else if (job.status === 'failed' || job.status === 'cancelled') {
    out += `<p>${esc(job.error || '')}</p>` +
      (job.refused && job.refused.length > 1
        ? `<ul class="dnote">${job.refused.slice(1).map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : '') +
      `<p class="dnote">Nothing was applied.</p>` +
      `<div class="racts"><button class="dact" data-act="retry">` +
      `${actLabel('↻ Retry', 'click again — asks an agent', 'asking…')}</button></div>`;
  } else if (job.decidedBy) {
    out += `<p class="dnote">by ${esc(job.decidedBy)} · ${esc(job.decidedAt || '')}` +
      `${job.committed === false ? ' · written, not committed' : ''}</p>`;
  }
  return out + `</div>`;
}

function discussionLive(t) {
  const c = S.disc[t.file];
  if (!c || !c.data) {
    return c && c.error ? `<p class="dnote bad">${esc(c.error)}</p>`
                        : `<p class="dnote">reading the discussion…</p>`;
  }
  const d = c.data;
  const notes = [];
  // a failed re-read keeps the last reading, and says it is the last one
  if (c.stale && c.error) notes.push(staleWords(c.error));
  if (d.blocked) notes.push(d.blocked);
  if (!d.refine.ready) {
    notes.push('Update card with agent is not available on this board: ' +
               (d.refine.blockers[0] || 'the refine intent does not resolve'));
  }
  const persistence = !d.persistence.commits
    ? `<p class="dnote">Comments and decisions are written to the card file; ` +
      `they are not committed, because BOARD_COMMIT_MOVES is off.</p>`
    : !d.persistence.syncs
    ? `<p class="dnote">Comments and decisions are committed to the work state; ` +
      `BOARD_SYNC is off, so they reach other boards when you push.</p>` : '';
  const jobs = jobsByComment(d);
  const rows = d.entries.map(e => e.kind === 'decision' ? decisionRowHtml(t, e)
    : commentRowHtml(t, e, jobs[e.id] || [], d, false)).join('');
  return (notes.length ? `<div class="well bad"><span class="lead">·</span><div class="wbody">` +
           notes.map(esc).join('<br>') + `</div></div>` : '') +
    persistence +
    (rows || `<p class="dnote">No comments yet.</p>`);
}

function jobsByComment(d) {
  const jobs = {};
  for (const job of (d && d.jobs) || []) (jobs[job.comment] = jobs[job.comment] || []).push(job);
  return jobs;
}

/* The relevance review a comment or a decision is about (task 84), as a
   link to that exact review in Reviews — or, where the card no longer
   holds it, the id in words. */
function reviewRef(t, id) {
  if (!id) return '';
  const acc = ((S.acc || {})[t.file] || {}).data;
  const r = acc && (acc.relevance || []).find(x => x.id === id);
  return ` · on relevance review ` + (r
    ? `<button class="dlink" data-goreview="${esc(id)}">${esc(relevanceLine(r))}</button>`
    : `<span class="mono">${esc(id)}</span>`);
}

function decisionRowHtml(t, e) {
  return `<div class="dentry decision"><div class="dhead2">` +
    `<b>${esc(e.outcome)}</b> <span class="mono">${esc(e.proposal || '')}</span>` +
    ` · ${esc(e.author || '')} · ${esc(e.at || '')}${reviewRef(t, e.review)}</div>` +
    `<div class="cbody">${esc(e.body || '')}</div></div>`;
}

/* One comment and the refinement attempts that answer it. `inReview`
   draws it inside its review's thread, where naming the review again
   would only repeat the heading above it. */
function commentRowHtml(t, e, mine, d, inReview) {
  const latest = mine[0];
  const fromReview = !!e.review;
  return `<div class="dentry comment" data-comment="${esc(e.id)}">` +
    `<div class="dhead2"><b>${esc(e.author || 'unattributed')}</b> · ` +
    `${esc(e.at || '')} <span class="mono">${esc(e.id)}</span>` +
    `${inReview ? '' : reviewRef(t, e.review)}</div>` +
    `<div class="cbody">${esc(e.body || '')}</div>` +
    (latest ? jobHtml(latest, t) +
              (mine.length > 1 ? `<p class="dnote">${mine.length - 1} earlier attempt` +
               `${mine.length === 2 ? '' : 's'}: ${mine.slice(1).map(j => esc(j.status)).join(', ')}</p>` : '')
            : `<div class="racts"><button class="dact" data-act="refine"` +
              `${!d || d.blocked || !d.refine.ready ? ' disabled' : ''}>` +
              `${actLabel(fromReview ? '✎ Update card from this review' : '✎ Update card with agent',
                          'click again — asks an agent', 'asking…')}` +
              `</button></div>`) +
    `</div>`;
}

function discussionPanel(t) {
  const c = (S.disc[t.file] || {}).data;
  const cannot = !c || !!c.blocked || !c.refine.ready;
  return `<section class="discussion" aria-label="Discussion">` +
    `<div class="phead">Discussion<span class="mono">history — the brief above is the requirement</span></div>` +
    `<div id="disc-live">${discussionLive(t)}</div>` +
    `<div class="composer">` +
    `<textarea id="commentbox" rows="3" aria-label="Comment or instruction for this card" ` +
    `placeholder="A comment, or an instruction for the brief">${esc(S.commentDraft[t.file] || '')}</textarea>` +
    `<div class="racts"><button class="dact" id="addComment">Add comment</button>` +
    `<button class="dact" id="refineNew"${cannot ? ' disabled' : ''}>` +
    `${actLabel('✎ Update card with agent', 'click again — asks an agent', 'asking…')}</button>` +
    refinePanelButton(t) + `</div>` +
    `<p class="dnote">Add comment only saves it. Update card with agent also asks an ` +
    `agent to propose a revised brief — it reads, it implements nothing, and the ` +
    `brief changes only when you apply the proposal.</p></div></section>`;
}

/* A card write: sent once (lib/api.js `post` never retries a mutation),
   and a refusal is said — toasted and announced — in the board's words. */
async function postCard(url, payload) {
  const { ok, data, error } = await post(url, payload);
  if (!ok) {
    toast(error || 'the board refused that', true);
    if (data.stale && S.selected) loadDiscussion(S.selected, true);
    return null;
  }
  return data;
}

function afterCardWrite(t) {
  loadDiscussion(t, true);
  loadState();
}

function wireDiscussion(t) {
  loadDiscussion(t);
  const box = $('#commentbox');
  if (!box) return;
  box.addEventListener('input', () => {
    S.commentDraft[t.file] = box.value;
    // a different text is a different request; the same text resent is not
    delete S.refineKey[t.file];
  });
  $('#addComment').addEventListener('click', () => saveComment(t, box.value));
  wireAction($('#refineNew'), `${t.file}::refine-new`, {
    label: 'Update card with agent', confirm: true,
    run: () => askRefinement(t, box.value),
  });
  wireLive(t);
}

/* Save a comment, starting nothing: the inline composer and the panel's. */
async function saveComment(t, body) {
  const data = await postCard('/api/card/comment', { file: t.file, stage: t.stage, body });
  if (!data) return false;
  S.commentDraft[t.file] = '';
  savePanel();
  toast(`comment ${data.comment.id} saved` +
        (data.comment.committed ? '' : ' — written to the card, not committed'));
  afterCardWrite(t);
  return true;
}

/* Save the text as a comment and ask for one refinement of it. The request
   identity is kept with the card until an answer arrives (and in the
   panel's slot), so a resend after a lost reply is the same request. */
async function askRefinement(t, text) {
  const key = S.refineKey[t.file] = S.refineKey[t.file] || newRequestKey();
  savePanel();
  const data = await postCard('/api/card/refine',
    { file: t.file, stage: t.stage, text, request: key });
  if (!data) return false;
  S.commentDraft[t.file] = '';
  delete S.refineKey[t.file];
  savePanel();
  toast(data.duplicate ? 'that refinement was already asked for'
                       : `${data.job.name || 'an agent'} is refining ${t.file} — it proposes; nothing changes until you apply`);
  afterCardWrite(t);
  return true;
}

/* ── card refinement in the shared panel (task 206) ─────────────────────

   The same discussion, comment → propose → Apply/Discard contract and
   routes as the Brief's own Discussion, drawn in the conversation panel
   about one card. The panel's composer and the Brief's share the unsent
   text and the request identity, and every row's buttons are wired by
   `wireRows`, so the two cannot act differently. Add comment starts
   nothing; Update card with agent is armed and asks for one proposal;
   only an armed Apply changes the brief. The card's history — comments,
   decisions, a review's link, attribution — is read from the card itself
   (`/api/card/discussion`), so it survives a reload and an upgrade. */
const REFINE = purpose('card-refine');

function boardTask(file) {
  return (S.state?.board.stages || []).flatMap(s => s.tasks).find(x => x.file === file) || null;
}

function refineBody(s) {
  const t = boardTask(s.target.card);
  if (!t) {
    return `<p class="ap-note" role="status">This card is not on the board now — archived, renamed or on another board. ` +
      `Its discussion and decisions stay in the card’s own history.</p>`;
  }
  loadDiscussion(t);
  const c = (S.disc[t.file] || {}).data;
  const cannot = !c || !!c.blocked || !c.refine.ready;
  const review = s.target.review;
  const ref = cardId(t, S.state) || t.file;
  return `<section class="discussion ap-refine" data-pcard="${esc(t.file)}" aria-label="Card refinement">` +
    `<p class="ap-note">The brief is the requirement; this conversation is the card’s history. ` +
    `<a href="/cards/${encodeURIComponent(ref)}?tab=brief" data-route>Open the brief</a>` +
    (review ? ` · <a href="/cards/${encodeURIComponent(ref)}?tab=reviews&amp;review=${encodeURIComponent(review)}" data-route>the relevance review</a>` : '') +
    `</p><div class="ap-disc">${discussionLive(t)}</div>` +
    `<div class="composer">` +
    `<label for="ap-commentbox" class="ap-label">Comment or instruction</label>` +
    `<textarea id="ap-commentbox" rows="3" data-pfile="${esc(t.file)}">${esc(S.commentDraft[t.file] || '')}</textarea>` +
    `<div class="racts"><button type="button" class="dact" data-pref="comment">Add comment</button>` +
    `<button type="button" class="dact" data-pref="refine"${cannot ? ' disabled' : ''}>` +
    `${actLabel('✎ Update card with agent', 'click again — asks an agent', 'asking…')}</button></div>` +
    `<p class="dnote">Add comment only saves it. Update card with agent also asks for one proposed brief; ` +
    `it implements nothing, and the brief changes only when you apply it.</p></div>` +
    limitsHtml(REFINE) + `</section>`;
}

function wireRefinePanel(el) {
  el.addEventListener('input', (e) => {
    const box = e.target;
    if (box.id !== 'ap-commentbox' || !box.dataset.pfile) return;
    S.commentDraft[box.dataset.pfile] = box.value;
    // a different text is a different request; the same text resent is not
    delete S.refineKey[box.dataset.pfile];
  });
}

function refinePainted(el, s) {
  const root = el.querySelector('.ap-refine');
  const t = root && boardTask(root.dataset.pcard);
  if (!t) return;
  const disc = root.querySelector('.ap-disc');
  if (disc) wireRows(disc, t);
  const text = () => (el.querySelector('#ap-commentbox') || {}).value || '';
  const add = root.querySelector('[data-pref="comment"]');
  if (add) add.addEventListener('click', async () => { if (await saveComment(t, text())) renderPanel(true); });
  const ask = root.querySelector('[data-pref="refine"]');
  if (ask) wireAction(ask, `${t.file}::refine-new`, { label: 'Update card with agent', confirm: true,
    run: async () => { const ok = await askRefinement(t, text()); if (ok) renderPanel(true); return ok; } });
}

function registerRefineAgent() {
  registerAgent('refine', {
    label: 'Card refinement',
    capability: REFINE.capability,
    key: (t) => `refine:${t.card}`,
    create: () => ({}),
    about: (s) => {
      const t = boardTask(s.target.card);
      return t ? `${t.number ? '#' + parseInt(t.number, 10) + ' ' : ''}${t.title || t.file}` : s.target.card;
    },
    body: refineBody,
    opened: (s) => { const t = boardTask(s.target.card); if (t) loadDiscussion(t, true); },
    // the unsent text and the request identity this tab keeps for the card
    persist: (s) => ({ text: S.commentDraft[s.target.card] || '', request: S.refineKey[s.target.card] || null }),
    revive: (d, target) => {
      if (!target || typeof target.card !== 'string') return null;
      if (d.text && !S.commentDraft[target.card]) S.commentDraft[target.card] = String(d.text);
      if (d.request && !S.refineKey[target.card]) S.refineKey[target.card] = String(d.request);
      return {};
    },
    painted: refinePainted,
    wire: wireRefinePanel,
  });
}

/* The entry point a card's Discussion and a review's composer carry. */
function refinePanelButton(t, review) {
  const target = review ? { card: t.file, review } : { card: t.file };
  return `<button type="button" class="dact ap-open"${askAttrs('refine', target)}>Open in conversation panel</button>`;
}

/* Every comment row's job buttons — in the Brief's discussion and in each
   relevance review's thread alike (task 84), so the two can never act
   differently. */
function wireLive(t) {
  const roots = [$('#disc-live'), ...document.querySelectorAll('#drawerbody .rvthread')]
    .filter(Boolean);
  for (const root of roots) wireRows(root, t);
}

/* One root's comment rows — the Brief's discussion, a review's thread, or
   the shared panel's conversation (task 206): the same buttons, the same
   routes and the same armed windows wherever the row is drawn. */
function wireRows(root, t) {
  root.querySelectorAll('[data-goreview]').forEach(btn =>
    btn.addEventListener('click', () => openReview(t, btn.dataset.goreview)));
  root.querySelectorAll('.dentry.comment').forEach(row => {
    const comment = row.dataset.comment;
    row.querySelectorAll('button[data-act]').forEach(btn => {
      const jobEl = btn.closest('[data-job]');
      const job = jobEl ? jobEl.dataset.job : null;
      const act = btn.dataset.act;
      const key = `${t.file}::refine-${act}-${job || comment}`;
      const call = (url, payload, said) => async () => {
        const data = await postCard(url, payload);
        if (!data) return false;
        toast(said(data));
        afterCardWrite(t);
        return true;
      };
      const acts = {
        refine: { label: 'Update card with agent', confirm: true,
          run: call('/api/card/refine', { file: t.file, stage: t.stage, comment,
                                          request: newRequestKey() },
                    d => d.duplicate ? 'that refinement was already asked for'
                                     : `${d.job.name || 'an agent'} is refining ${t.file}`) },
        retry: { label: 'Retry', confirm: true,
          run: call('/api/card/refine', { file: t.file, stage: t.stage, comment,
                                          retry: true, request: newRequestKey() },
                    d => `${d.job.name || 'an agent'} is refining ${t.file} again`) },
        apply: { label: 'Apply', confirm: true,
          run: call('/api/card/refine/apply', { id: job },
                    d => d.already ? 'already applied' : `the brief of ${t.file} was updated`) },
        discard: { label: 'Discard',
          run: call('/api/card/refine/discard', { id: job },
                    d => d.already ? 'already discarded' : 'proposal discarded — the brief is unchanged') },
        cancel: { label: 'Cancel',
          run: call('/api/card/refine/cancel', { id: job }, () => 'refinement cancelled') },
      };
      if (acts[act]) wireAction(btn, key, acts[act]);
    });
  });
}

function renderDiscussion(t) {
  const live = $('#disc-live');
  if (!live) return;
  live.innerHTML = discussionLive(t);
  const c = (S.disc[t.file] || {}).data;
  const btn = $('#refineNew');
  if (btn && !btn.classList.contains('busy')) btn.disabled = !c || !!c.blocked || !c.refine.ready;
  // each relevance review's thread and its refine button follow the same
  // read; the composer around them — and the text in it — is left alone
  document.querySelectorAll('#drawerbody .rvthread').forEach(el => {
    el.innerHTML = reviewThreadHtml(t, el.dataset.review);
  });
  document.querySelectorAll('#drawerbody .rvcompose').forEach(el => {
    const why = el.querySelector('.rvwhy');
    if (why) why.innerHTML = reviewRefineWhy(c);
    const b = el.querySelector('[data-rvact="refine"]');
    if (b && !b.classList.contains('busy')) b.disabled = !c || !!c.blocked || !c.refine.ready;
  });
  wireLive(t);
}

/* Four signals a single green tick would collapse, each in its own words
   and with the revision it is about: what bench itself ran, what external
   CI said, what reviewers said, and what the merge gate decides from
   them. Green CI is not a named check, and no gate is not a pass — so each
   row says only what its own authority said. */
/* Review, as the separate records it is: Bench's own verdict (durable,
   bound to the head it was pinned), GitHub's formal review state (the PR
   poll's reading, not bound to a commit here) and the round a reviewer
   filed in the card's text. Each says which revision it is about; one
   about another commit is history and never reads as today's verdict,
   and none of them is inferred from another. */
const REVIEW_WORD = { 'APPROVE': 'approved', 'REQUEST CHANGES': 'changes requested' };
function reviewSignals(d, pr) {
  const rv = (d && d.reviews) || {};
  const cur = d && d.candidate && d.candidate.state === 'present' ? d.candidate.commit : null;
  const where = (rec) => rec.current === true ? ' — the commit that would merge'
    : rec.current === false ? ` — an earlier commit, history${cur ? `; none yet at ${shortSha(cur)}` : ''}`
    : '';
  const at = (rec) => rec.head ? ` at ${shortSha(rec.head)}` : ' at an unrecorded revision';
  const bad = (rec) => rec.verdict === 'REQUEST CHANGES' && rec.current === true;
  const rows = [];
  const b = rv.bench;
  // the headline is the server's one review status (task 152) — the very
  // words the card face wears — and the record it rests on follows it
  const now = d && d.review;
  // with no Bench verdict on record, the status's own sentence says why
  // it reads as it does ("No Bench verdict yet" beside "Review outdated"
  // left the reader to work out what was outdated)
  rows.push(['Bench review', b
    ? (now ? `${now.label}. ` : '') +
      `Newest verdict: ${REVIEW_WORD[b.verdict] || b.verdict}${at(b)}${where(b)}`
    : now ? `${now.label}. ${now.sentence}` : 'No Bench verdict yet',
  (now && now.tone === 'alarm') || (b && bad(b)) ? 'bad' : '']);
  const gh = !pr ? 'not observed — bench has not read this pull request'
    : pr.review === 'approved' ? 'approved on the pull request — not bound to a commit here'
    : pr.review === 'changes' ? 'changes requested on the pull request'
    : 'no formal review state — a comment is not one';
  rows.push(['GitHub review', gh + (pr && pr.copilot ? ` · copilot ${pr.copilot}` : ''),
    pr && pr.review === 'changes' ? 'bad' : '']);
  const r = rv.report;
  // the report that is the prose of the very verdict above adds nothing
  if (r && !(b && b.verdict === r.verdict && b.head && r.head &&
             (b.head.startsWith(r.head) || r.head.startsWith(b.head)))) {
    rows.push(['Reported review', `${REVIEW_WORD[r.verdict] || r.verdict} in the card's ` +
      `report${at(r)}${where(r)}`, bad(r) ? 'bad' : '']);
  }
  return rows;
}

function signalsBlock(t) {
  const rows = [];
  const v = (S.state.verify || {})[t.file];
  const d = deliveryOf(t);
  const full = (S.dfull[t.file] || {}).data;
  if (v || (d && (d.counts || {}).required)) {
    // required obligations only, on the commit that would merge; optional
    // checks are counted apart and never read as this row (design #94 §7)
    const checks = full ? full.requirements.filter(r => r.kind === 'check') : null;
    const at = d && d.candidate && d.candidate.state === 'present'
      ? ` on ${shortSha(d.candidate.commit)}` : '';
    const optional = d ? (d.history || {}).optional || 0 : 0;
    const req = !checks ? 'reading the requirements…'
      : checks.length ? `Required: ${checks.filter(r => r.state === 'met').length} of ` +
                        `${checks.length} passed${at}`
      : 'No check is required';
    const bad = checks && checks.some(r => r.class === 'problem');
    rows.push(['Bench checks', `${req}.${optional ? ` Optional: ${optional} (see ◫).` : ''}`,
      bad ? 'bad' : '']);
  }
  const pr = (S.state.prs || {})[t.file];
  if (t.pr || pr) {
    const ci = pr && pr.ci;
    rows.push(['External CI', ci
      ? ({ pass: 'passing', fail: 'failing', running: 'running' }[ci] || ci) +
        (pr.detail ? ` · ${pr.detail}` : '') + ' — not a Bench check'
      : 'no CI result', ci === 'fail' ? 'bad' : '']);
  }
  if (t.pr || pr || (d && d.reviews && (d.reviews.bench || d.reviews.report))) {
    for (const row of reviewSignals(d, pr)) rows.push(row);
  }
  const r = (S.state.reviews || {})[t.file];
  if (r) {
    const [, word] = REVIEW_LABELS[r.label] || REVIEW_LABELS.none;
    rows.push(['Pinned review', `${word} · ${(r.head || 'unrecorded').slice(0, 12)}`,
      r.label === 'superseded' ? 'bad' : '']);
  }
  const g = (S.state.gates || {})[t.file];
  if (g && d) {
    const bad = d.status.word === 'ready-overridden' || (t.stage === 'review'
      && (d.classes || []).some(c => c === 'problem' || c === 'cannot-assess'));
    rows.push(['Merge requirements', requirementsHeadline(d) +
      (d.status.word === 'ready-overridden' ? ' — overridden, still unmet' : ''), bad ? 'bad' : '']);
  } else if (g) {
    const [cls, , word] = gateState(g.decision);
    rows.push(['Merge requirements', `${word} — ${g.summary}`, cls === 'bad' ? 'bad' : '']);
  } else if (rows.length) {
    rows.push(['Merge requirements', 'none configured — nothing is required before a '
      + 'merge, and that is not a pass', '']);
  }
  if (!rows.length) return '';
  return `<div class="pmembers signals"><div class="phead">Signals` +
    `<span class="mono">kept apart</span></div>` +
    rows.map(([k, v2, cls]) => `<div class="prow flat${cls === 'bad' ? ' bad' : ''}">` +
      `<span class="pstage">${esc(k)}</span>` +
      `<span class="ptitle">${esc(v2)}</span></div>`).join('') +
    `</div>`;
}

export {
  afterCardWrite, commentRowHtml, decisionRowHtml, diffHtml, discStamp,
  DISCUSSION_SECTION, discussionLive, discussionPanel, JOB_WORD, jobHtml,
  askRefinement, jobsByComment, loadDiscussion, newRequestKey, postCard, refinePanelButton,
  registerRefineAgent, renderDiscussion, REVIEW_WORD, reviewRef, reviewSignals, saveComment,
  signalsBlock, wireDiscussion, wireLive, wireRows, withoutDiscussion
};
