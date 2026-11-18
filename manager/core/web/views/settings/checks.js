/* Settings → Checks. */
import { S } from '../../app/state.js';
import { esc } from '../../lib/dom.js';
import { checkEditor, ensureDraft } from './draft.js';
import { sline, spanel, sref } from './overview.js';
import { stateWord } from './shell.js';

/* ── verification ─────────────────────────────────────────────────────── */

/* One registered check, as configured — never as a result. Name and
   purpose first, then the facts that decide where and for how long it
   runs, its action, and the command itself, which stays readable in full
   in the disclosure's own line. */
function checkBlock(page, c, editor) {
  const v = page.verification;
  const scopes = Object.keys(c.scopes || {});
  const fact = (k, val, help) => `<div><dt>${esc(k)}</dt><dd>${val}` +
    (help ? `<span class="shelp">${esc(help)}</span>` : '') + `</dd></div>`;
  return `<article class="scard" aria-label="${esc('check ' + c.name)}">` +
    `<div class="shd"><h3>${esc(c.name)}</h3>` +
    // registered is not required, and neither is a pass: the badge says
    // which, and the definition rides on its title
    // No state word on a registered check (#92 §3, retained by #94 §13):
    // registration is not a result, and a badge in a state slot reads as
    // one. The backend keeps `state`; the row draws it only when it is a
    // refusal, which is a problem to fix rather than a badge to clear.
    (c.requiredBy.length
      ? `<span class="sbadge ok" title="A merge rule needs a current pass.">` +
        `Required: ${esc(c.requirement.join(', '))}</span>`
      : `<span class="sbadge dim" title="No merge needs this check.">` +
        `Optional</span>`) +
    (c.state === 'invalid' ? stateWord(c.state, page.states) : '') +
    `<button class="sb" data-sedit="check" data-sname="${esc(c.name)}" ` +
    `aria-label="${esc('edit the check ' + c.name)}">Edit</button></div>` +
    (c.help ? `<p class="lead">${esc(c.help)}</p>`
      : `<p>No description is recorded for this check in the policy.</p>`) +
    `<dl class="sfacts">` +
    fact('Runs in', `the <code>${esc(c.root)}</code> checkout`, c.rootWhy || '') +
    fact('Preparation', c.prepare.length
      ? c.prepare.map(p => `<code>${esc(p)}</code>`).join(', ') : 'None') +
    fact('Time limit', `${esc(String(c.timeout))} seconds`) +
    fact('May be asked for by', c.intents.length
      ? esc(c.intents.join(', ')) : 'No launch intent') +
    (scopes.length ? fact('Scopes', scopes.map(s => `<code>${esc(s)}</code>`).join(', ')) : '') +
    // what its result is bound to beyond its code and programs: names
    // only — an input's content and a variable's value are never shown
    ((c.inputs || []).length ? fact('Declared inputs',
      c.inputs.map(s => `<code>${esc(s)}</code>`).join(', '),
      'handed to the check as pinned copies; a change to one makes its pass stale') : '') +
    ((c.env || []).length ? fact('Bound environment',
      c.env.map(s => `<code>${esc(s)}</code>`).join(', '),
      'values are bound by a keyed hash on this machine, never stored') : '') +
    ((c.accepts || []).length ? fact('Accepts evidence of',
      c.accepts.map(s => `<code>${esc(s)}</code>`).join(', '),
      'only a run whose definition is the same in every dimension, in the ' +
      'same kind of checkout, counts — a name alone never does') : '') +
    // the verifier report contract (task 135): how its exit status
    // becomes an outcome, and whose words its findings are
    (c.report ? fact('Verifier report', `contract <code>${esc(c.report)}</code>`,
      'the check writes a report to $BENCH_VERIFY_REPORT; it passes only with ' +
      'exit 0 and a passing report. Its summary and findings are the ' +
      'verifier\'s own account, not something bench observed') : '') +
    `</dl>` +
    `<details class="fold scmd"><summary>Command <code>${esc(c.run)}</code></summary>` +
    `<div class="srefbody"><div class="skv">` +
    `<dt>executable</dt><dd><code>${esc(c.executable || '')}</code></dd>` +
    `<dt>arguments</dt><dd><code>${esc(JSON.stringify(c.argv))}</code></dd>` +
    scopes.map(s => `<dt>scope ${esc(s)}</dt><dd><code>` +
      `${esc(JSON.stringify(c.scopes[s]))}</code></dd>`).join('') +
    `<dt>check identity</dt><dd><code>${esc(String(c.checkId))}</code></dd>` +
    (c.checkSubstance ? `<dt>substance</dt><dd><code>${esc(String(c.checkSubstance))}</code></dd>` : '') +
    `</div></div></details>` + editor + `</article>`;
}

function settingsVerification(page) {
  const v = page.verification, states = page.states;
  const ed = S.sedit && S.sedit.kind === 'check' ? (ensureDraft(page), S.sedit) : null;
  const rows = v.checks.map(c => checkBlock(page, c,
    ed && ed.anchor === c.name ? checkEditor(page, ed.index) : '')).join('');
  const orphan = ed && !v.checks.some(c => c.name === ed.anchor)
    ? checkEditor(page, ed.index) : '';
  const steps = v.prepare.map(s => sline({
    label: s.name, value: s.run, code: true, states,
    help: (s.usedBy.length ? `prepares ${s.usedBy.join(', ')}`
      : 'prepares nothing — no check references it') +
      (s.network ? ' · uses the network, unbounded' : ''),
    tag: s.network ? ['warn', 'Network: not bounded'] : null,
  })).join('');
  return spanel('Registered checks', `${v.checks.length} registered`,
      (v.checks.length
        ? `<p>These are your checks, not their results. Nothing to do here. ` +
          `To see or run a check, open its card.</p>`
        : `<p>No checks yet. Bench runs none of its own.</p>`) +
      (v.problems.length ? v.problems.map(p =>
        `<p class="bad">${esc(p.code)}${p.field ? ` (${esc(p.field)})` : ''} — ` +
        `${esc(p.why)}</p>`).join('') : '') +
      (v.checks.length ? rows : '') +
      orphan +
      `<div class="acts"><button class="sb primary" data-sedit="new-check" ` +
      `aria-label="add a check">Add a check</button>` +
      `<button class="sb link" data-ssection="policy" aria-label="edit checks in ` +
      `the advanced policy editor">Advanced editor</button></div>`) +
    // what to do with a registered check — usually nothing. Closed by
    // default; it explains, it asks for no action (#92 §4, revised by
    // #94 §13: reuse, and results that are per card)
    sref('What to do with a registered check',
      `<ul>` +
      `<li><b>Nothing, by default.</b> Registering a check needs no action. ` +
      `Do not rerun, edit or start an agent to tidy this page — there is no ` +
      `badge here to clear.</li>` +
      `<li><b>Results are per card.</b> A check's result belongs to the card ` +
      `and the exact commit it ran on. Open the card: its Overview lists the ` +
      `merge requirements, and its ◫ evidence holds every run.</li>` +
      `<li><b>A pass is reused, not repeated.</b> A recorded pass keeps ` +
      `counting while nothing it depends on has changed — the code, the ` +
      `check's command, its programs, preparation, declared inputs and ` +
      `environment. An unrelated policy edit, such as context added for ` +
      `another card, does not make it stale.</li>` +
      `<li><b>Get a fresh result.</b> On the card, <b>Run required checks</b> ` +
      `asks for every required check that needs one (it arrives with a later ` +
      `card; until then, use <b>↻</b> per check in the ◫ evidence sheet). It ` +
      `runs in that card's checkout, preparation first, and writes a new ` +
      `record.</li>` +
      `<li><b>Why a merge may still wait.</b> A required check needs a ` +
      `current pass on the exact commit that would merge. A pass on an ` +
      `older commit does not count.</li>` +
      `<li><b>Something looks wrong?</b> Fix the line in red, or use ` +
      `<b>Check configuration</b>. That reads the setup; it runs nothing.</li>` +
      `</ul>` +
      (v.checks.length ? '' : `<p>Without a registered check, an agent saying ` +
        `the tests pass is its own word, not evidence.</p>`)) +
    // the opt-in contract and what it does and does not mean (task 135)
    sref('Verifier reports — an opt-in contract',
      `<ul>` +
      `<li><b>Opt in per check.</b> Add <code>"report": "v1"</code> to a check ` +
      `in the policy. Nothing becomes required, and no run gains a ` +
      `permission: whether a merge needs the check is still the merge rule's ` +
      `decision, and a run can neither switch it on nor off.</li>` +
      `<li><b>What the check is handed.</b> <code>$BENCH_VERIFY_REPORT_DIR</code>, ` +
      `an empty directory for this run alone, and <code>$BENCH_VERIFY_REPORT</code>, ` +
      `the report file inside it. It writes a small JSON report: an outcome ` +
      `(passed, failed, setup-failed, refused, incomplete), a summary, and ` +
      `optional findings and artifacts.</li>` +
      `<li><b>How the outcome is decided.</b> A pass needs exit 0 <i>and</i> a ` +
      `passing report. A missing, malformed or contradicting report is ` +
      `<b>no verdict reached</b>, never a pass. A timeout, a cancellation or a ` +
      `program that could not start is bench's reading whatever the report ` +
      `says.</li>` +
      `<li><b>Whose words.</b> The summary, the findings and any refusal or ` +
      `setup failure the verifier describes are the project's own account. ` +
      `Bench records them and does not observe the verifier's internal checks ` +
      `or safety controls.</li>` +
      `<li><b>What is kept.</b> The report and the text, JSON and image files ` +
      `it declares inside its directory, beside the log, on this board only. ` +
      `Text is scrubbed of anything credential-shaped; that is not a promise ` +
      `that every secret is found, and an image is kept as made.</li>` +
      `</ul>`) +
    spanel('Preparation steps', `${v.prepare.length} declared`,
      `<p>${esc(v.preparationIsNotVerification)}</p>` +
      (v.prepare.length ? steps
        : `<p>No preparation step is declared. Add one in the advanced editor ` +
          `when a check needs dependencies installed first.</p>`)) +
    sref(v.ownership,
      `<p>${esc(v.ownershipDetail)}</p>` +
      `<div class="skv">` + v.responsibilities.map(r =>
        `<dt>${esc(r.who)}</dt><dd>${esc(r.what)}</dd>`).join('') + `</div>` +
      `<p>${esc(v.registrationIsNotRequirement)}</p>` +
      `<p>${esc(v.evidenceScope)}</p>`) +
    sref('Where a check runs — a location, not a sandbox',
      `<div class="skv">` + Object.entries(v.roots).map(([w, why]) =>
        `<dt>${esc(w)}</dt><dd>${esc(why)}</dd>`).join('') + `</div>` +
      `<p>${esc(v.rootsNote)}</p>` +
      `<ul>${v.trustLimits.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` +
      `<p>The environment a check is not given:</p>` +
      `<pre>${esc(v.envDenied.join('\n'))}</pre>`) +
    sref('A worked example, and what to ask a developer for',
      `<p>${esc(v.example.why)}</p>` +
      `<pre>${esc(JSON.stringify(v.example.document, null, 2))}</pre>` +
      `<p>${esc(v.example.then)}</p>` +
      `<ul>${v.checklist.map(c => `<li>${esc(c)}</li>`).join('')}</ul>`);
}

export {
  checkBlock, settingsVerification
};
