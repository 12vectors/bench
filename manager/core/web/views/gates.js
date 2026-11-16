/* May this merge happen? The merge requirements sheet. */
import { loadState } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { toast } from '../lib/toast.js';
import { closeSheet, verifySheet, wireAction } from './card.js';
import { deliveryActions, deliveryFullKey, deliveryOf, deliveryStatusHtml, nextDfullSeq, requirementsHeadline, requirementsHtml, storeDeliveryFull, wireDeliveryButtons } from './delivery.js';

/* ── may this merge happen? ────────────────────────────────────────────

   The gate, at card altitude. Three rules it follows and the design
   system is the reason for all three: the word carries the meaning and
   the colour only ever means state, so `ungated` is drawn in the settled
   register rather than the colour of a pass; no evidence never looks
   green; and an override wears the alarm, because it is a decision
   somebody made over an unmet gate rather than a gate that was met. */

const GATE_STATES = {
  allowed: ['ok', '✓', 'met'],
  ungated: ['dim', '·', 'ungated'],
  pending: ['accent', '◌', 'waiting'],
  blocked: ['bad', '✕', 'blocked'],
  overridden: ['bad', '!', 'overridden'],
};

function gateState(word) {
  return GATE_STATES[word] || ['', '·', word || 'unknown'];
}

/* The merge requirements, at card altitude: one ⚖ chip whose count is the
   delivery reading's required obligations only. It is on the face in
   review/, where the next delivery step lives, and whenever an override
   is in force; before review, requirements that simply lie ahead are in
   the tools in the neutral register — listed and enforced, never an
   alarm (design #94 §3.6). */
function gateChip(task) {
  const g = (S.state.gates || {})[task.file];
  if (!g) return null;
  const d = deliveryOf(task);
  const title = `Merge requirements — ${d ? requirementsHeadline(d) : g.summary}. `
    + `Evidence is ${g.location === 'local' ? 'local to this board' : g.location}. `
    + `Opening this runs nothing.`;
  if (!d) {
    const [cls, glyph, word] = gateState(g.decision);
    return { label: word, pre: '⚖', glyph, cls, gate: g.scope, title };
  }
  const c = d.counts || { required: 0, met: 0 };
  const classes = d.classes || [];
  const overridden = d.status.word === 'ready-overridden';
  const review = task.stage === 'review';
  if (overridden) {
    return { label: 'overridden', pre: '⚖', glyph: '!', cls: 'bad', gate: g.scope,
             face: true, title };
  }
  if (!c.required || c.met >= c.required) {
    return { label: c.required ? `${c.met}/${c.required}` : 'none apply', pre: '⚖',
             glyph: c.required ? '✓' : '', cls: c.required ? 'ok' : '', gate: g.scope,
             face: false, title };
  }
  const problem = review && (classes.includes('problem') || classes.includes('cannot-assess'));
  return {
    label: problem ? '✕' : `${c.required - c.met} needed`, pre: '⚖', glyph: '',
    cls: problem ? 'bad' : review && classes.includes('running') ? 'accent' : '',
    gate: g.scope, face: review, title,
  };
}

/* One requirement, as a row. The reason and the remedy are the point of
   the panel: "it failed", "nobody asked for it" and "a dependency would
   not install" send a reader to three different places. */
/* A check requirement names the coverage it needs: a bare check is its
   unscoped full run, and a scope is only itself — so a scoped pass on the
   ◫ chip beside a gate requiring the full run is never left unexplained. */
function requirementLabel(r) {
  if (r.kind === 'review') return `review · ${r.name}`;
  if (r.kind !== 'check') return r.name;
  return `${r.check || r.name} · ${r.scope ? 'scope ' + r.scope : 'full run'}`;
}

function gateRow(r) {
  const cls = { met: 'pass', unmet: 'fail', pending: 'accent',
                overridden: 'fail' }[r.state] || '';
  const glyph = { met: '✓', unmet: '✕', pending: '◌',
                  overridden: '!' }[r.state] || '·';
  const name = requirementLabel(r);
  return `<div class="check-row ${cls}" title="${esc(r.why || '')}">` +
    `<span class="glyph">${glyph}</span>` +
    `<span class="name">${esc(name)}</span>` +
    `<span class="state">${esc(r.state)}${r.commit ? ' · ' + esc(String(r.commit).slice(0, 12)) : ''}` +
    `</span></div>` +
    `<div class="check-row ${cls}" style="padding-left:18px">` +
    `<span class="glyph">·</span><span class="name">why</span>` +
    `<span class="state">${esc(r.why || '')}</span></div>` +
    (r.remedy
      ? `<div class="check-row" style="padding-left:18px">` +
        `<span class="glyph">→</span><span class="name">remedy</span>` +
        `<span class="state">${esc(r.remedy)}</span></div>`
      : '');
}

function gatePanel(decision) {
  const [cls, , word] = gateState(decision.decision);
  const head = decision.head || {};
  const row = (glyph, name, value, kls) =>
    `<div class="check-row ${kls || ''}"><span class="glyph">${glyph}</span>` +
    `<span class="name">${esc(name)}</span>` +
    `<span class="state">${esc(value)}</span></div>`;
  const applied = (decision.overrides || {}).applied;
  return `<div class="panel"><div class="phead">` +
    `<span class="label">${esc(decision.scope)} — ${esc(word)}</span>` +
    `<span class="r">${esc(decision.policyId || '')}</span></div>` +
    row('·', 'what it guards', decision.why) +
    row('⚖', 'the commit it is about',
        `${head.commit ? String(head.commit).slice(0, 12) : 'not resolved'} `
        + `(${head.ref || 'unknown branch'})`,
        head.commit ? '' : 'fail') +
    // A policy that does not resolve is not `gated` either, and it is not
    // absent: its refusal is the requirement, and it is unmet.
    (decision.gated || (decision.blockers || []).length
      ? (decision.requirements || []).map(gateRow).join('')
      : row('·', 'no requirement',
            'this board declares no gate for this, so nothing is required '
            + 'here and nothing is proved')) +
    (applied
      ? row('!', 'overridden',
            `${applied.actor} at ${String(applied.head || '').slice(0, 12)} — `
            + `${applied.reason} (gates still unmet: `
            + `${(applied.gates || []).join(', ')})`, 'fail')
      : '') +
    `</div>`;
}

/* The detail panel, and — where the policy allows one — the single
   armed confirmation that records an override. A GET of stored records:
   opening it runs no check, relaunches nothing and reaches no remote. */
async function gateSheet(task) {
  const wrap = $('#sheetwrap');
  const head = (tail) =>
    `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}` +
    `⚖ ${esc(tail)}</span></div>`;
  // Escape (or Close) gives focus back to what opened the dialog
  if (!wrap.classList.contains('open') && document.activeElement
      && document.activeElement !== document.body) S.sheetReturn = document.activeElement;
  wrap.innerHTML = `<div class="sheet" role="dialog" aria-label="Merge requirements">` +
    `${head('merge requirements')}<p>Reading the records…</p></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });

  // the gate's own records, and the card's delivery reading — both GETs,
  // and neither writes, fetches from a remote or starts anything
  const [res, dres] = await Promise.all([
    fetch('/api/gates?file=' + encodeURIComponent(task.file) +
          '&stage=' + encodeURIComponent(task.stage)),
    fetch('/api/card/delivery?file=' + encodeURIComponent(task.file) +
          '&stage=' + encodeURIComponent(task.stage)),
  ]);
  const data = res.ok ? await res.json() : {};
  const full = dres.ok ? (await dres.json()).delivery : null;
  // the status and its actions are the board's compact reading — the very
  // one the face and the Overview draw — so the three cannot disagree;
  // the full reading adds only the rows under it
  const d = deliveryOf(task) || full;
  // This read is the newest word on the rows, so the Overview shares it:
  // when it is about the compact reading the page holds, it is that
  // reading's rows; when it is not, the page's compact reading is the one
  // behind, and asking for the board state again (a GET) brings both up.
  if (full && d && full.detail && full.detail === d.detail) {
    storeDeliveryFull(task.file, deliveryFullKey(task), nextDfullSeq(), { data: full, error: null });
  } else if (full && d && full.detail !== d.detail) {
    loadState();
  }
  const scopes = data.scopes || [];
  const policy = data.policy || {};
  const blocked = scopes.filter(s => s.decision === 'blocked');
  // Whether an override can be recorded is the server's answer
  // (`overrides.eligible`, from the function the override route refuses
  // with) — never worked out here. A blocked scope with no implementation
  // commit, for one, is not overridable, and the sheet says why.
  const eligible = blocked.filter(s => (s.overrides || {}).eligible);
  const first = eligible[0] || blocked[0] || scopes[0] || null;
  const canOverride = eligible.length > 0;
  const refusal = !canOverride && blocked.length &&
    (policy.overrides || {}).allowed
    ? ((first.overrides || {}).ineligible || {}) : null;

  const verdicts = (data.verdicts || []).slice(0, 4).map(v =>
    `<div class="check-row ${v.verdict === 'APPROVE' ? 'pass' : 'fail'}">` +
    `<span class="glyph">${v.verdict === 'APPROVE' ? '✓' : '✕'}</span>` +
    `<span class="name">bench · ${esc(v.verdict)}</span>` +
    `<span class="state">at ${esc(String(v.head || 'an unrecorded head').slice(0, 12))}` +
    `${v.published ? ' · ' + esc(v.published) : ''}</span></div>`).join('')
    || `<div class="check-row none"><span class="glyph">—</span>` +
       `<span class="name">no Bench verdict</span>` +
       `<span class="state">◔ review PR records one, bound to the head it ` +
       `was pinned</span></div>`;

  const gh = data.github || null;
  const formal = gh && (gh.formal || []).length
    ? gh.formal.map(r =>
        `<div class="check-row ${r.state === 'APPROVED' ? 'pass' : 'fail'}">` +
        `<span class="glyph">${r.state === 'APPROVED' ? '✓' : '✕'}</span>` +
        `<span class="name">github · ${esc(r.login || 'unknown')}</span>` +
        `<span class="state">${esc(r.state)} of ` +
        `${esc(String(r.commit || 'an unrecorded commit').slice(0, 12))}` +
        `${r.self ? ' · the account that proposed the head' : ''}</span></div>`).join('')
    : `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no formal GitHub review state</span>` +
      `<span class="state">${gh ? 'bench has read the pull request and GitHub holds none'
                                : 'bench has not read this pull request'}</span></div>`;
  const limitation = gh && gh.limitation
    ? `<div class="check-row fail"><span class="glyph">!</span>` +
      `<span class="name">same account</span>` +
      `<span class="state">${esc(gh.limitation.why)}</span></div>`
    : '';
  const comments = gh && (gh.comments || []).length
    ? `<div class="check-row"><span class="glyph">·</span>` +
      `<span class="name">comments</span>` +
      `<span class="state">${gh.comments.length} commented review` +
      `${gh.comments.length === 1 ? '' : 's'} — a comment is not a review ` +
      `state, however it is worded</span></div>`
    : '';

  const history = (data.overrides || []).map(o =>
    `<div class="check-row fail"><span class="glyph">!</span>` +
    `<span class="name">${esc(o.actor)}</span>` +
    `<span class="state">${esc(o.scope)} at ` +
    `${esc(String(o.head || '').slice(0, 12))} over ` +
    `${esc((o.gates || []).join(', '))} — ${esc(o.reason)}</span></div>`).join('');

  wrap.innerHTML =
    `<div class="sheet msheet" role="dialog" aria-label="Merge requirements">${head('merge requirements')}` +
    deliveryStatusHtml(task, d, 'dlg') +
    requirementsHtml(task, d, full, 'dlg') +
    `<details class="fold dreqfold"><summary>Technical details: every merge rule, ` +
    `read against its commit</summary><div class="dfoldbody">` +
    `<p>A merge rule asks one question: may this merge happen? It is answered ` +
    `from bench's own recorded evidence, bound to the exact commit that ` +
    `would move — not from an agent's report, not from its exit code, and ` +
    `not from the absence of a CI result. Reading it here runs nothing.</p>` +
    scopes.map(gatePanel).join('') + `</div></details>` +
    `<div class="panel"><div class="phead"><span class="label">` +
    `Review — two records, kept apart</span>` +
    `<span class="r">bench's own, and GitHub's</span></div>` +
    verdicts + formal + limitation + comments + `</div>` +
    (history
      ? `<div class="panel"><div class="phead"><span class="label">` +
        `Overrides</span><span class="r">recorded, never a pass</span></div>` +
        history + `</div>`
      : '') +
    `<p class="why">What this board requires comes from ` +
    `${esc(policy.source && policy.source.identity || 'the legacy settings')}` +
    `. ${(policy.blockers || []).length
      ? 'That policy does not resolve, so every gate here is blocked until it ' +
        'is fixed — that is not the same as having no gate. '
      : policy.configured ? '' : 'No gate is configured, so nothing here is ' +
        'required and nothing is proved. '}` +
    `Evidence and these records are local to this board.</p>` +
    (policy.blockers || []).map(b =>
      `<div class="check-row fail"><span class="glyph">✕</span>` +
      `<span class="name">policy</span><span class="state">${esc(b)}</span>` +
      `</div>`).join('') +
    `<div class="sbtns">` +
    (canOverride
      ? `<textarea id="sh-why" placeholder="Why are you proceeding over an ` +
        `unmet gate? Recorded against you, with the commit and the gates ` +
        `that are unmet."></textarea>` +
        `<button id="sh-override">Override<small>Record that ` +
        `${esc(data.actor || 'you')} proceeded over ` +
        `${esc((first.unmet || []).map(u => u.name).join(', '))} at ` +
        `${esc(String((first.head || {}).commit || '').slice(0, 12))}. It ` +
        `forges no evidence, makes no gate met, and authorises this commit ` +
        `only — the next one is a different question.</small></button>`
      : refusal
        ? `<button id="sh-override" disabled aria-describedby="sh-override-why">` +
          `Override<small id="sh-override-why">Not available: ` +
          `${esc(refusal.why || 'this gate cannot be overridden here')}` +
          `</small></button>`
        : '') +
    `<button id="sh-nogate">Close<small>Nothing was run.</small></button>` +
    `</div></div>`;
  $('#sh-nogate').addEventListener('click', closeSheet);
  wireDeliveryButtons(wrap, task, deliveryActions(d, 'dlg'));
  wrap.querySelectorAll('[data-dview="evidence"]').forEach(b =>
    b.addEventListener('click', () => verifySheet(task)));
  const btn = $('#sh-override');
  if (btn && canOverride) {
    wireAction(btn, `${task.file}::override`, {
      label: 'Override', confirm: 'record the override?', busy: 'recording…',
      run: async () => {
        const reason = ($('#sh-why') || {}).value || '';
        const r = await fetch('/api/gates/override', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: task.file, scope: first.scope,
                                 reason, head: (first.head || {}).commit }),
        });
        const out = await r.json();
        if (!r.ok) { toast(out.error || 'the override was refused', true); return false; }
        toast(out.duplicate
          ? 'that override was already on record — nothing was recorded twice'
          : 'override recorded: the gate is still unmet');
        await loadState();
        gateSheet(task);
        return true;
      },
    });
  }
}

export {
  GATE_STATES, gateChip, gatePanel, gateRow, gateSheet, gateState,
  requirementLabel
};
