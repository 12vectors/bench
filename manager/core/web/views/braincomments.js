/* Comments on selected Brain text (task 210).

   Select a passage in a maintained entry's body and a small toolbar
   offers **Comment** and **Ask agent**; **Choose a passage…** lists the
   entry's paragraphs, items and headings as buttons, for keyboard and
   touch. Either way the page sends what the reader saw (the selected text
   and a little either side) and the server maps it onto the entry's
   Markdown — or refuses it, when it is not there or repeats and its
   context does not say which.

   **Post comment** writes the discussion and starts nothing. **Ask agent**
   is armed: it starts one Brain Agent drafting request about the passage,
   whose proposal is reviewed and applied in the shared panel as before.
   Threads are read from `GET /api/brain/comments`; each passage is shown
   where the server located it — current, relocated, or else outdated or
   detached, said in words and never highlighted on a guess — and marked
   in the text by an outlined, labelled block (not colour alone).

   Every write names the record revision the page read and a request
   identity kept until an answer arrives: a resend after no answer is the
   same request, which the board answers once. */

import { fetch, post } from '../lib/api.js';
import { esc } from '../lib/dom.js';

const ARM_MS = 5000;
const STATE_WORD = { current: 'In place', relocated: 'Found again',
                     outdated: 'Outdated', detached: 'Detached' };
const OUTCOME_WORD = { 'not-launched': 'could not start', failed: 'failed', cancelled: 'was cancelled',
                       discarded: 'was discarded', conflict: 'was not applied (conflict)',
                       unrelated: 'was applied elsewhere in the entry',
                       unverified: 'was applied; not verified against this passage' };
const LIVE_WORD = { running: 'drafting', questions: 'waiting for your answers', proposed: 'proposal ready',
                    ready: 'ready', applying: 'applying', applied: 'applied', failed: 'failed',
                    cancelled: 'cancelled', discarded: 'discarded', expired: 'expired',
                    'not-on-this-board': 'not on this board' };

const cm = {
  entry: null, data: null, at: 0, error: null, loading: false,
  pending: null,          // {mode, quote, prefix, suffix, entryRevision, thread?}
  text: '', sent: null,   // the body a write was sent with, kept until an answer
  busy: null, msg: null, picker: null, armed: null, replies: {}, focus: null,
};
let hooks = { repaint: () => {}, openDraft: () => {} };

function setCommentHooks(h) { hooks = { ...hooks, ...h }; }

const rid = () => 'cm-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);

/* ── reading ────────────────────────────────────────────────────────── */

async function load(id) {
  if (cm.loading) return;
  cm.loading = true;
  try {
    const res = await fetch('/api/brain/comments?entry=' + encodeURIComponent(id));
    const data = await res.json().catch(() => ({}));
    if (cm.entry !== id) return;
    if (res.ok) { cm.data = data.comments; cm.error = null; } else cm.error = data.error || 'the comments could not be read';
  } catch (e) {
    if (cm.entry === id) cm.error = 'the board could not be asked';
  } finally {
    cm.loading = false;
    cm.at = Date.now();
    hooks.repaint('live');
  }
}

function forgetComments() { cm.at = 0; }

/* ── the skeleton: letters and digits, as the server maps them ───────── */

const ALNUM = /[\p{L}\p{N}]/u;

function skel(text) {
  let out = '';
  for (const c of String(text || '')) if (ALNUM.test(c)) out += c;
  return out;
}

/* every text node of `root`, and a skeleton of their letters and digits
   with each one's node and offset */
function domSkeleton(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const chars = []; const where = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.nodeValue;
    for (let k = 0; k < t.length; k++) {
      const cp = t.codePointAt(k);
      const c = String.fromCodePoint(cp);
      if (ALNUM.test(c)) { chars.push(c); where.push([n, k, c.length]); }
      if (cp > 0xffff) k++;
    }
  }
  return { s: chars.join(''), chars, where };
}

/* the one place a located passage sits in the rendered body, or null */
function findInDom(root, anchor) {
  const q = Array.from(skel(anchor.quote));
  if (q.length < 3) return null;
  const { chars, where } = domSkeleton(root);
  const before = Array.from(anchor.before || ''); const after = Array.from(anchor.after || '');
  const eq = (at, arr) => arr.every((c, j) => chars[at + j] === c);
  const hits = [];
  for (let i = 0; i + q.length <= chars.length; i++) {
    if (chars[i] !== q[0] || !eq(i, q)) continue;
    const okB = !before.length || (i >= before.length && eq(i - before.length, before));
    const okA = !after.length || eq(i + q.length, after);
    hits.push({ i, ctx: okB && okA });
  }
  const told = hits.length === 1 ? hits : hits.filter(h => h.ctx);
  if (told.length !== 1) return null;
  return { from: where[told[0].i], to: where[told[0].i + q.length - 1] };
}

function markRange(found, tid, label) {
  const range = document.createRange();
  range.setStart(found.from[0], found.from[1]);
  range.setEnd(found.to[0], found.to[1] + found.to[2]);
  // one mark per text node the range crosses, so blocks are never split
  const nodes = [];
  const walker = document.createTreeWalker(range.commonAncestorContainer.nodeType === 3
    ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (range.intersectsNode(n)) nodes.push(n);
  nodes.forEach((n, k) => {
    const a = n === found.from[0] ? found.from[1] : 0;
    const b = n === found.to[0] ? found.to[1] + found.to[2] : n.nodeValue.length;
    if (b <= a) return;
    const tail = n.splitText(a);
    tail.splitText(b - a);
    const m = document.createElement('mark');
    m.className = 'b-anchor';
    m.dataset.thread = tid;
    if (k === 0) { m.tabIndex = -1; m.setAttribute('aria-label', label); m.id = 'bcm-' + tid; }
    tail.parentNode.replaceChild(m, tail);
    m.appendChild(tail);
  });
}

/* after the page is painted: each open thread's passage marked where the
   server and the rendered text agree on exactly one place */
function paintAnchors(root) {
  const prose = root.querySelector('.b-entry .b-prose');
  if (!prose || !cm.data) return;
  cm.unplaced = new Set();
  for (const t of cm.data.threads) {
    if (t.state !== 'open' || !['current', 'relocated'].includes(t.location.state)) continue;
    const found = findInDom(prose, t.anchor);
    if (!found) { cm.unplaced.add(t.id); continue; }
    markRange(found, t.id, `Commented passage: ${t.anchor.quote.slice(0, 80)}`);
  }
  if (cm.focus) {
    const m = prose.querySelector(`#bcm-${CSS.escape(cm.focus)}`);
    cm.focus = null;
    if (m) { m.scrollIntoView({ block: 'center' }); m.focus(); }
  }
}

/* ── the selection ──────────────────────────────────────────────────── */

function selectionIn(prose) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  if (!prose.contains(r.commonAncestorContainer)) return null;
  const quote = sel.toString().replace(/\s+/g, ' ').trim();
  if (skel(quote).length < 3) return null;
  const pre = document.createRange();
  pre.setStart(prose, 0); pre.setEnd(r.startContainer, r.startOffset);
  const post = document.createRange();
  post.setStart(r.endContainer, r.endOffset); post.setEnd(prose, prose.childNodes.length);
  return { quote: quote.slice(0, 600), prefix: pre.toString().slice(-120), suffix: post.toString().slice(0, 120),
           rect: r.getBoundingClientRect() };
}

function placeBar(root) {
  const bar = root.querySelector('#bselbar');
  const prose = root.querySelector('.b-entry .b-prose');
  if (!bar || !prose) return;
  const s = selectionIn(prose);
  if (!s || !cm.data || !cm.data.commentable) { bar.hidden = true; return; }
  cm.selection = s;
  bar.hidden = false;
  // fixed to the viewport, just under the selection, kept on screen
  bar.style.top = Math.max(4, Math.min(s.rect.bottom + 6, window.innerHeight - 48)) + 'px';
  bar.style.left = Math.max(4, Math.min(s.rect.left, window.innerWidth - 220)) + 'px';
}

/* ── markup ─────────────────────────────────────────────────────────── */

const btn = (act, label, { arg = '', cls = '', disabled = false, title = '' } = {}) =>
  `<button type="button" class="b-btn ${cls}" data-cm="${esc(act)}"` +
  (arg ? ` data-cmarg="${esc(arg)}"` : '') + (title ? ` title="${esc(title)}"` : '') +
  (disabled ? ' disabled' : '') + `>${esc(label)}</button>`;

const isArmed = (k) => cm.armed && cm.armed.key === k && cm.armed.until > Date.now();

/* the first click of a costly action: armed for a few seconds, then at
   rest again — a later click is a first click, never an unconfirmed fire */
function arm(k) {
  cm.armed = { key: k, until: Date.now() + ARM_MS };
  hooks.repaint(true);
  setTimeout(() => { if (cm.armed && cm.armed.key === k && cm.armed.until <= Date.now()) { cm.armed = null; hooks.repaint(true); } },
    ARM_MS + 50);
}

function when(at) { return at ? esc(String(at).slice(0, 16).replace('T', ' ')) + ' UTC' : ''; }

function messageHtml(m) {
  return `<li class="b-cm-msg"><p class="b-cm-who"><strong>${esc(m.actor || 'board')}</strong> ` +
    `<span class="b-note">${when(m.at)}${m.kind === 'open' ? ` · ${m.mode === 'ask' ? 'asked the Brain Agent' : 'commented'}` : ''}</span></p>` +
    `<p class="b-cm-text">${esc(m.text || '')}</p></li>`;
}

function linkHtml(t, l) {
  const live = l.live || {};
  const word = LIVE_WORD[live.state] || live.state || 'unknown';
  const why = live.error || live.why || (live.conflict ? `conflict: ${live.conflict}` : '');
  return `<li class="b-cm-link"><span>Brain Agent request <span class="mono">${esc(l.draft || '')}</span>: ` +
    `<strong>${esc(word)}</strong>${why ? ` — <span class="b-note">${esc(why)}</span>` : ''}</span> ` +
    (live.state && live.state !== 'not-on-this-board'
      ? `<button type="button" class="b-btn" data-bact="ag-open" data-barg="${esc(l.draft)}">Open the conversation</button>` : '') +
    (live.state === 'applied' ? ` <a href="/brain/e/${encodeURIComponent(cm.entry)}?change=${encodeURIComponent(l.draft)}" ` +
      'data-route>View changes</a>' : '') + '</li>';
}

function threadHtml(t, commentable) {
  const loc = t.location || {};
  const placed = ['current', 'relocated'].includes(loc.state);
  const st = t.state === 'resolved' ? 'Resolved' : 'Open';
  const r = cm.replies[t.id] || { text: '' };
  const res = t.resolution;
  const resolvedBy = res ? `<p class="b-cm-res" role="note">Resolved by ` +
    (res.by === 'edit' ? `the applied edit <span class="mono">${esc(res.change || '')}</span> ` +
      `(<a href="/brain/e/${encodeURIComponent(cm.entry)}?change=${encodeURIComponent(res.draft || '')}" data-route>View changes</a>)`
      : `<strong>${esc(res.actor || 'a person')}</strong>`) +
    ` · ${when(res.at)}${res.text ? ' — ' + esc(res.text) : ''}</p>` : '';
  const outcomes = t.outcomes.length ? '<ul class="b-cm-outcomes">' + t.outcomes.map(o =>
    `<li>Agent request ${esc(OUTCOME_WORD[o.outcome] || o.outcome)}: <span class="b-note">${esc(o.why || '')}</span></li>`).join('') +
    '</ul>' : '';
  const where = `<p class="b-cm-where"><span class="b-chip b-cm-${esc(loc.state || 'detached')}">` +
    `${esc(STATE_WORD[loc.state] || loc.state)}</span> ` +
    (placed ? `<span class="b-note">in “${esc(loc.section || 'Opening')}”, line ${(loc.lineStart || 0) + 1}` +
      `${cm.unplaced && cm.unplaced.has(t.id) ? ' — not highlighted on this page' : ''}</span> ` +
      (t.state === 'open' ? btn('show', 'Show in text', { arg: t.id }) : '')
      : `<span class="b-note">${esc(loc.why || '')}</span>`) + '</p>';
  const acts = !commentable ? '' :
    `<div class="b-cm-acts"><label class="b-field"><span>Reply, or a note for resolving</span>` +
    `<textarea rows="2" data-cmf="reply" data-cmarg="${esc(t.id)}">${esc(r.text)}</textarea></label><p>` +
    btn('reply', 'Reply', { arg: t.id }) + ' ' +
    (t.state === 'open'
      ? btn('resolve', 'Resolve with this note', { arg: t.id }) + ' ' +
        (placed ? btn('followup', isArmed('f:' + t.id) ? 'Confirm: ask the Brain Agent' : 'Ask agent with this text',
          { arg: t.id, cls: isArmed('f:' + t.id) ? 'armed' : '' }) : '') +
        (!placed || loc.state === 'relocated'
          ? ' ' + btn('reanchor', 'Re-anchor to a passage…', { arg: t.id }) : '')
      : btn('reopen', 'Reopen with this note', { arg: t.id })) + '</p></div>';
  return `<li class="b-cm-thread b-cm-${t.state}" id="bct-${esc(t.id)}" aria-labelledby="bcth-${esc(t.id)}">` +
    `<h3 class="b-cm-h" id="bcth-${esc(t.id)}"><span class="b-chip">${st}</span> ` +
    `${t.mode === 'ask' ? 'Agent request' : 'Comment'} on</h3>` +
    `<blockquote class="b-cm-quote">${esc(t.anchor ? t.anchor.quote : '')}</blockquote>` + where +
    '<ul class="b-cm-msgs">' + t.messages.map(messageHtml).join('') + '</ul>' +
    (t.links.length ? '<ul class="b-cm-links">' + t.links.map(l => linkHtml(t, l)).join('') + '</ul>' : '') +
    outcomes + resolvedBy + acts + '</li>';
}

function composerHtml() {
  const p = cm.pending;
  if (!p) return '';
  const re = p.mode === 'reanchor';
  const armed = isArmed('ask');
  return `<div class="b-panel b-cm-compose" role="group" aria-labelledby="bcmc-h">` +
    `<h3 id="bcmc-h">${re ? 'Re-anchor the comment to' : 'About this passage'}</h3>` +
    `<label class="b-field"><span>The selected text (narrow it if you like — it must be in the entry)</span>` +
    `<textarea rows="2" data-cmf="quote">${esc(p.quote)}</textarea></label>` +
    `<label class="b-field"><span>${re ? 'A note (optional)' : 'Your comment, or your instruction for the agent'}</span>` +
    `<textarea rows="3" data-cmf="text" id="bcm-text">${esc(cm.text)}</textarea></label>` +
    (re ? `<p>${btn('send-reanchor', 'Re-anchor here')} ${btn('cancel', 'Cancel')}</p>`
      : `<p>${btn('post', 'Post comment', { cls: 'b-primary' })} ` +
        `${btn('ask', armed ? 'Confirm: ask the Brain Agent' : 'Ask agent', { cls: armed ? 'armed' : '' })} ` +
        `${btn('cancel', 'Cancel')}</p>` +
        '<p class="b-note">Post comment saves the discussion with this entry and starts nothing. Ask agent ' +
        'starts one Brain Agent drafting request about this passage; the entry is unchanged until you ' +
        'review its proposal and Apply it.</p>') +
    (cm.sent ? `<p class="b-warn" role="status">No answer arrived for the last send. ${btn('resend', 'Send again (the same request)')}</p>` : '') +
    '</div>';
}

function pickerHtml() {
  if (!cm.picker) return '';
  return `<div class="b-panel b-cm-picker" role="group" aria-labelledby="bcmp-h"><h3 id="bcmp-h">Choose a passage</h3>` +
    '<p class="b-note">Each is a paragraph, item or heading of the entry as shown above.</p><ol class="b-cm-blocks">' +
    cm.picker.map((b, i) => `<li><button type="button" class="b-cm-block" data-cm="pickblock" data-cmarg="${i}">` +
      `${esc(b.length > 160 ? b.slice(0, 157) + '…' : b)}</button></li>`).join('') +
    `</ol><p>${btn('cancel', 'Cancel')}</p></div>`;
}

function commentsHtml(e) {
  if (cm.entry !== e.id) {
    Object.assign(cm, { entry: e.id, data: null, at: 0, error: null, pending: null, text: '', sent: null,
                        busy: null, msg: null, picker: null, armed: null, replies: {} });
  }
  if (!cm.loading && Date.now() - cm.at > 4000) load(e.id);
  const d = cm.data;
  const head = '<h2 id="bcm-h">Comments on passages</h2>';
  if (!d) {
    return `<section class="b-panel b-comments" aria-labelledby="bcm-h">${head}` +
      (cm.error ? `<p class="b-warn" role="alert">${esc(cm.error)}</p>`
        : '<p class="b-loading" role="status" aria-busy="true">Reading the comments…</p>') + '</section>';
  }
  const msg = cm.msg ? `<p class="${cm.msg.bad ? 'b-warn' : 'b-note'}" role="${cm.msg.bad ? 'alert' : 'status'}">` +
    `${esc(cm.msg.text)}${cm.msg.retry ? ' ' + btn('retry-commit', 'Retry commit', { arg: cm.msg.retry }) : ''}` +
    `${cm.msg.push ? ' ' + btn('retry-push', cm.msg.pushFailed ? 'Retry push' : 'Push now', { arg: cm.msg.push }) : ''}</p>` : '';
  const open = d.threads.filter(t => t.state === 'open');
  const closed = d.threads.filter(t => t.state !== 'open');
  return `<section class="b-panel b-comments" aria-labelledby="bcm-h">${head}` +
    `<p class="b-note">${d.counts.open} open · ${d.counts.resolved} resolved · kept in ` +
    `<span class="mono">${esc(d.file)}</span>, shared with the work state like every Brain write.</p>` +
    (d.commentable
      ? `<p>Select text in the entry to comment on it or ask the Brain Agent, or ${btn('pick', 'Choose a passage…')}</p>`
      : `<p class="b-note" role="note">${esc(d.why || '')}</p>`) +
    (d.malformed ? `<p class="b-warn">${d.malformed} record line(s) could not be read and are not shown.</p>` : '') +
    msg + pickerHtml() + composerHtml() +
    (open.length ? '<ol class="b-cm-list">' + open.map(t => threadHtml(t, d.commentable)).join('') + '</ol>'
      : '<p class="b-none">No open comments.</p>') +
    (closed.length ? `<details class="b-cm-closed"><summary>${closed.length} resolved</summary><ol class="b-cm-list">` +
      closed.map(t => threadHtml(t, d.commentable)).join('') + '</ol></details>' : '') +
    `<div class="b-selbar" id="bselbar" role="toolbar" aria-label="Selected text" hidden>` +
    `<button type="button" class="b-btn" data-cm="sel-comment">Comment</button>` +
    `<button type="button" class="b-btn" data-cm="sel-ask">Ask agent</button></div></section>`;
}

/* ── acting ─────────────────────────────────────────────────────────── */

// Saved, Committed and Shared, said apart as for any Brain write
function opWords(op) {
  const c = (op && op.commit) || {};
  const p = (op && op.push) || {};
  const shared = p.state === 'shared' ? '; shared' : p.state === 'pending' ? '; not shared yet (push pending)'
    : p.state === 'failed' ? `; not shared — the push failed (${p.detail || 'sync could not publish it'})`
    : p.state === 'off' ? '; not shared (sync is off)' : '';
  return c.state === 'committed' ? 'saved and committed' + shared
    : c.state === 'off' ? 'saved, not committed (committing board edits is off)'
    : c.state === 'failed' ? `saved; the commit failed (${c.detail || 'git refused it'})` : 'saved';
}

// the message after a write or a retry, with the retry it calls for:
// Retry commit while the commit failed, Push now / Retry push while a
// committed write is not on the remote yet — each by the request identity
function opMsg(lead, op, request) {
  const c = (op && op.commit) || {};
  const p = (op && op.push) || {};
  const unpushed = c.state === 'committed' && (p.state === 'pending' || p.state === 'failed');
  return { text: `${lead}${opWords(op)}.`, bad: c.state === 'failed' || p.state === 'failed',
           retry: c.state === 'failed' ? request || null : null,
           push: unpushed ? request || null : null, pushFailed: p.state === 'failed' };
}

async function send(url, body, onOk) {
  cm.busy = url;
  cm.sent = body;
  hooks.repaint(true);
  const res = await post(url, body);
  cm.busy = null;
  if (res.status === 0) {
    cm.msg = { bad: true, text: 'No answer from the board — nothing is known to have happened. Send again: it is the same request.' };
  } else {
    cm.sent = null;
    if (res.ok) onOk(res.data);
    else {
      cm.msg = { bad: true, text: res.error || 'refused' };
      if (res.data && res.data.current && res.data.current.threads) cm.data = res.data.current;
      if (res.data && res.data.code === 'comments-changed') cm.at = 0;
      if (res.data && res.data.thread && cm.data) cm.at = 0;
    }
  }
  hooks.repaint(true);
}

function base(extra) {
  return { request: rid(), entry: cm.entry, expected: cm.data.revision, ...extra };
}

function fromSelection(mode, s, thread) {
  cm.pending = { mode, quote: s.quote, prefix: s.prefix, suffix: s.suffix, thread };
  cm.picker = null;
  cm.msg = null;
  hooks.repaint(true);
  setTimeout(() => document.getElementById('bcm-text')?.focus(), 0);
}

function act(name, arg, root, entry) {
  const p = cm.pending;
  if (name === 'pick' || name === 'reanchor') {
    const prose = root.querySelector('.b-entry .b-prose');
    cm.picker = prose ? Array.from(prose.querySelectorAll('p, li, h2, h3, h4, h5, h6, td, th, blockquote, pre'))
      .filter(n => !n.querySelector('p, li')).map(n => n.textContent.replace(/\s+/g, ' ').trim())
      .filter(t => skel(t).length >= 3) : [];
    cm.reanchoring = name === 'reanchor' ? arg : null;
    cm.pending = null;
    hooks.repaint(true);
    setTimeout(() => root.querySelector('.b-cm-block')?.focus(), 0);
    return;
  }
  if (name === 'pickblock') {
    const quote = (cm.picker || [])[Number(arg)] || '';
    fromSelection(cm.reanchoring ? 'reanchor' : 'comment', { quote: quote.slice(0, 600), prefix: '', suffix: '' },
      cm.reanchoring);
    return;
  }
  if (name === 'sel-comment' || name === 'sel-ask') {
    if (cm.selection) fromSelection(cm.reanchoring ? 'reanchor' : 'comment', cm.selection, cm.reanchoring);
    if (name === 'sel-ask') arm('ask');
    return;
  }
  if (name === 'cancel') { Object.assign(cm, { pending: null, picker: null, sent: null, armed: null, reanchoring: null }); hooks.repaint(true); return; }
  if (name === 'resend' && cm.sent) { send(cm.busyUrl || '/api/brain/comments/open', cm.sent, cm.onOk || (() => {})); return; }
  if (name === 'show') {
    const m = root.querySelector(`#bcm-${CSS.escape(arg)}`);
    if (m) { m.scrollIntoView({ block: 'center' }); m.focus(); }
    return;
  }
  if (name === 'retry-commit' || name === 'retry-push') {
    const action = name === 'retry-commit' ? 'commit' : 'push';
    post('/api/brain/retry', { request: arg, action }).then(r => {
      cm.msg = r.ok ? opMsg('Asked again — ', (r.data || {}).operation, arg)
        : { bad: true, text: r.error || `the ${action} was refused`, [action === 'commit' ? 'retry' : 'push']: arg };
      cm.at = 0; hooks.repaint(true);
    });
    return;
  }
  // a write's request identity is the one the page sent: the writer's
  // answer says the operation's state, and a retry asks for it by that
  const done = (what, request) => (data) => {
    if (data.comments) cm.data = data.comments;
    cm.msg = opMsg(`${what} — `, data.operation, request);
    cm.at = Date.now();
  };
  const go = (url, body, what, after) => {
    cm.busyUrl = url;
    cm.onOk = (data) => { done(what, body && body.request)(data); if (after) after(data); };
    send(url, body, cm.onOk);
  };
  if ((name === 'post' || name === 'ask' || name === 'send-reanchor') && p) {
    const body = base({ entryRevision: entry.revision, quote: p.quote, prefix: p.prefix, suffix: p.suffix, text: cm.text });
    if (name === 'ask' && !isArmed('ask')) { arm('ask'); return; }
    const clear = () => { cm.pending = null; cm.text = ''; cm.armed = null; cm.reanchoring = null; };
    if (name === 'post') go('/api/brain/comments/open', body, 'Comment saved', clear);
    else if (name === 'send-reanchor') go('/api/brain/comments/reanchor', { ...body, thread: p.thread }, 'Re-anchored', clear);
    else {
      go('/api/brain/comments/ask', body, 'Asked the Brain Agent', (data) => {
        clear();
        cm.msg = { text: 'Asked the Brain Agent — one drafting request started. Its conversation is open beside the page.' };
        if (data.draft) hooks.openDraft(data.draft);
      });
    }
    return;
  }
  const r = cm.replies[arg] || { text: '' };
  if (name === 'reply' || name === 'resolve' || name === 'reopen') {
    const body = base({ thread: arg, text: r.text });
    go('/api/brain/comments/' + name, body, { reply: 'Reply saved', resolve: 'Resolved', reopen: 'Reopened' }[name],
      () => { delete cm.replies[arg]; });
    return;
  }
  if (name === 'followup') {
    if (!isArmed('f:' + arg)) { arm('f:' + arg); return; }
    const body = base({ thread: arg, entryRevision: entry.revision, text: r.text });
    go('/api/brain/comments/ask', body, 'Asked the Brain Agent', (data) => {
      delete cm.replies[arg];
      cm.armed = null;
      if (data.draft) hooks.openDraft(data.draft);
    });
  }
}

let wiredRoot = null;

function wireComments(root, currentEntry) {
  if (wiredRoot === root) return;
  wiredRoot = root;
  // the toolbar keeps the selection: pressing it does not move focus
  root.addEventListener('mousedown', (ev) => {
    if (ev.target.closest && ev.target.closest('#bselbar')) ev.preventDefault();
  });
  root.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-cm]');
    if (!b || !root.contains(b) || b.disabled) return;
    const e = currentEntry();
    if (!e || !cm.data) return;
    act(b.dataset.cm, b.dataset.cmarg, root, e);
  });
  root.addEventListener('input', (ev) => {
    const f = ev.target.dataset && ev.target.dataset.cmf;
    if (!f) return;
    if (f === 'text') cm.text = ev.target.value;
    else if (f === 'quote' && cm.pending) { cm.pending.quote = ev.target.value; cm.pending.prefix = ''; cm.pending.suffix = ''; }
    else if (f === 'reply') cm.replies[ev.target.dataset.cmarg] = { text: ev.target.value };
  });
  document.addEventListener('selectionchange', () => {
    if (!document.body.contains(root)) return;
    placeBar(root);
  });
}

export { commentsHtml, findInDom, forgetComments, paintAnchors, setCommentHooks, skel, wireComments };
