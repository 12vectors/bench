'use strict';
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

function loadDiscussion(t, force) {
  const cur = S.disc[t.file] || {};
  // a read already in flight may have read the card before the write that
  // asked for this one, so the request is kept and made when it ends —
  // dropping it is how a saved comment never appeared
  if (cur.loading) {
    if (force || cur.loadingStamp !== discStamp(t)) S.disc[t.file] = { ...cur, again: t };
    return;
  }
  if (!force && cur.stamp === discStamp(t)) return;
  const stamp = discStamp(t);
  S.disc[t.file] = { ...cur, loading: true, loadingStamp: stamp, again: null };
  fetch(`/api/card/discussion?file=${encodeURIComponent(t.file)}` +
        `&stage=${encodeURIComponent(t.stage)}`)
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => ok ? { data, stamp } : { data: cur.data, error: data.error, stamp })
    .catch(() => ({ data: cur.data, stamp, error: 'the board could not be asked' }))
    .then(next => {
      const again = (S.disc[t.file] || {}).again;
      S.disc[t.file] = next;
      if (again) loadDiscussion(again, true);
      if (S.selected && S.selected.file === t.file) renderDiscussion(S.selected);
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
    `${actLabel('✎ Update card with agent', 'click again — asks an agent', 'asking…')}</button></div>` +
    `<p class="dnote">Add comment only saves it. Update card with agent also asks an ` +
    `agent to propose a revised brief — it reads, it implements nothing, and the ` +
    `brief changes only when you apply the proposal.</p></div></section>`;
}

async function postCard(url, payload) {
  const res = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    toast(data.error || 'the board refused that', true);
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
  $('#addComment').addEventListener('click', async () => {
    const data = await postCard('/api/card/comment',
      { file: t.file, stage: t.stage, body: box.value });
    if (!data) return;
    S.commentDraft[t.file] = '';
    toast(`comment ${data.comment.id} saved` +
          (data.comment.committed ? '' : ' — written to the card, not committed'));
    afterCardWrite(t);
  });
  wireAction($('#refineNew'), `${t.file}::refine-new`, {
    label: 'Update card with agent', confirm: true,
    run: async () => {
      const key = S.refineKey[t.file] = S.refineKey[t.file] || newRequestKey();
      const data = await postCard('/api/card/refine',
        { file: t.file, stage: t.stage, text: box.value, request: key });
      if (!data) return false;
      S.commentDraft[t.file] = '';
      delete S.refineKey[t.file];
      toast(data.duplicate ? 'that refinement was already asked for'
                           : `${data.job.name || 'an agent'} is refining ${t.file} — it proposes; nothing changes until you apply`);
      afterCardWrite(t);
      return true;
    },
  });
  wireLive(t);
}

/* Every comment row's job buttons — in the Brief's discussion and in each
   relevance review's thread alike (task 84), so the two can never act
   differently. */
function wireLive(t) {
  const roots = [$('#disc-live'), ...document.querySelectorAll('#drawerbody .rvthread')]
    .filter(Boolean);
  for (const root of roots) wireRows(root);
  function wireRows(root) {
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
  rows.push(['Bench review', b
    ? `${REVIEW_WORD[b.verdict] || b.verdict}${at(b)}${where(b)}`
    : 'no Bench verdict yet', b && bad(b) ? 'bad' : '']);
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

function allRecentEvents() {
  const merged = [];
  for (const evs of Object.values(S.events)) merged.push(...evs.slice(-40));
  merged.push(...(S.state.boardEvents || []).slice(-60));
  for (const run of Object.values(S.running)) merged.push(run);
  merged.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  return merged;
}

function evRow(ev) {
  const cls = ev.running ? 'run' : ev.ok === false ? 'bad' : ev.ok === true ? 'ok' : '';
  const glyph = GLYPHS[ev.kind] || '·';
  const meta = ev.session ? sessionMeta(ev.session) : null;
  const who = meta ? `<span class="who-b">${esc((meta.label || '').split(' · ')[0])}</span> ` : '';
  return `<div class="ev ${cls}"><time>${fmtClock(ev.ts)}</time>` +
    `<span class="glyph">${glyph}</span><span class="what">${who}${esc(ev.summary)}</span></div>`;
}

function renderBar() {
  const bar = $('#statusbar');
  const drag = !!S.dragging;
  const hot = drag && S.dockHot;
  bar.classList.toggle('drag', drag);
  bar.classList.toggle('hot', hot);

  const merged = allRecentEvents();
  $('#actbtn').innerHTML =
    `<span class="chev">${S.logOpen ? '▾' : '▴'}</span>` +
    `<span class="dot live"></span><span>Activity</span>` +
    `<span class="mono">${merged.length}</span>`;

  const tail = merged[merged.length - 1];
  $('#bartail').innerHTML = tail
    ? `<span>${fmtClock(tail.ts)}</span>` +
      `<span style="color:${tail.ok === false ? 'var(--alarm)' : tail.ok === true ? 'var(--calm)' : 'var(--muted)'}">${GLYPHS[tail.kind] || '·'}</span>` +
      `<span class="t-what">${tail.session && sessionMeta(tail.session) ? `<span class="t-who">${esc((sessionMeta(tail.session).label || '').split(' · ')[0])}</span> ` : ''}${esc(tail.summary)}</span>`
    : `<span style="color:var(--dim)">quiet — events tick along here as they happen</span>`;

  $('#barhinttext').textContent = hot
    ? 'Release to archive — ⌘Z brings it back'
    : 'Drop anywhere along this bar to archive';

  const count = S.state.archivedCount || 0;
  $('#tray').innerHTML =
    `<span class="glyph2">${hot ? '↓' : '⌸'}</span>` +
    `<b>${hot ? 'Let go' : 'Archive'}</b>` +
    (count > 0 && !hot ? `<span class="count">${count}</span>` : '');
}

function renderLog() {
  $('#logpanel').classList.toggle('open', S.logOpen);
  if (!S.logOpen) return;
  const active = FILTERS.find(([key]) => key === S.logFilter);
  const kinds = active ? active[2] : null;
  const merged = allRecentEvents();
  const events = merged.filter(ev => !kinds || kinds.has(ev.kind)).slice(-150);
  const extras = Object.entries(S.state.board.extras).map(([name, files]) =>
    `<details><summary>${name}/ ${files.length}</summary><ul>` +
    files.map(f => `<li class="xfile" data-dir="${esc(name)}" data-name="${esc(f)}">${esc(f)}</li>`).join('') +
    `</ul></details>`).join('');
  $('#loghead').innerHTML =
    `<span class="label">Everything that happened</span>` +
    `<span class="mono">${merged.length} events</span>` +
    `<span class="spacer"></span>` +
    FILTERS.filter(f => f[3] !== 'session').map(([key, label]) =>
      `<button class="${S.logFilter === key ? 'on' : ''}" data-lf="${key}">${label}</button>`).join('') +
    extras;
  document.querySelectorAll('#loghead [data-lf]').forEach(b =>
    b.addEventListener('click', () => { S.logFilter = b.dataset.lf; renderLog(); }));
  document.querySelectorAll('#loghead .xfile').forEach(li =>
    li.addEventListener('click', () => openExtra(li.dataset.dir, li.dataset.name)));
  const body = $('#logbody');
  markScroll('log', body);
  body.innerHTML = events.length ? events.map(evRow).join('')
    : '<div class="ev quiet"><time></time><span class="glyph">·</span><span class="what">Nothing here. Good.</span></div>';
  restoreScroll('log', body);
  // and stuck to the bottom stays stuck to the bottom: that reading was
  // already right, and it outranks the mark
  if (S.logStick) body.scrollTop = body.scrollHeight;
}

async function archiveCard(file, from) {
  const res = await fetch('/api/archive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, from }),
  });
  const data = await res.json();
  toast(res.ok ? `Archived ${file} — ⌘Z brings it back` : (data.error || 'archive failed'), !res.ok);
  await loadState();
  return res.ok;
}

async function unarchiveLast() {
  const res = await fetch('/api/unarchive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  const data = await res.json();
  toast(res.ok ? `${data.file} brought back to ${data.to}/` : (data.error || 'nothing to bring back'), !res.ok);
  await loadState();
}

