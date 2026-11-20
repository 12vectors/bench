// The Overview by a reader's questions (task 149), rendered from what the
// server computes for the fixture cards (tests/fixtures/task-information/
// readings.json, held current by tests/test_task_information.py). Each
// test asks one of the reader's questions of a fixture and checks the
// answer is there, in the brief's own words, and that nothing is invented
// where the card has nothing to say.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const FIX = fileURLToPath(new URL('../fixtures/task-information/readings.json', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);
const READINGS = JSON.parse(readFileSync(FIX, 'utf8'));

globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], getElementById: () => null };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = () => 0;
// nothing in these renderers may ask the board for anything it was not
// already given: every request is refused loudly, and counted
const asked = [];
globalThis.fetch = (url) => { asked.push(String(url)); return new Promise(() => {}); };

// the words a person reads: every tag a break between them
const text = (html) => String(html).replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&')
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/\s+/g, ' ').replace(/ ([).,:]) ?/g, '$1 ').replace(/\( /g, '(').trim();

async function setup(name, over = {}) {
  const { S } = await load('app/state.js');
  const r = READINGS[name];
  const listed = { file: r.file, stage: r.stage, rev: over.rev || r.rev, number: r.file.split('-')[0],
                   title: 'unused', dependsOn: over.dependsOn || [], mtime: 1 };
  S.state = { board: { stages: [{ tasks: over.tasks || [] }] }, progress: {}, information: {},
              acceptance: {}, gates: {}, delivery: {} };
  S.detail[r.file] = over.detail || { data: { file: r.file, stage: r.stage, rev: r.rev, body: r.body,
                                              parts: r.parts, outline: r.outline, words: 10 } };
  return { S, r, listed };
}

test('a long legacy card explains its work from the brief, not its title or its history', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const { listed } = await setup('164-legacy-long.md');
  const html = workHtml(listed);
  const words = text(html);
  assert.match(words, /^The work Make first installation layout-aware/);
  assert.match(words, /Problem External-workspace support .*…continues in the brief — from “Context”/);
  assert.match(words, /Scope Installation-mode-aware initialization Preservation, reinstall and mode changes Legacy installations — from “What to build”/);
  assert.match(words, /Not in scope Changing how the default work root is resolved/);
  assert.match(words, /Quoted from the brief as it reads now \(revision [0-9a-f]{12}\)/);
  assert.match(html, /data-goto="brief"/, 'the way to the whole brief');
  assert.doesNotMatch(words, /Committed on task\/164|approving this change/, 'history is not the requirement');
  assert.doesNotMatch(words, /Initialize work directories only at/, 'the title is not the summary');
});

test('a brief with nothing to quote says so, and writes nothing in its place', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const { listed } = await setup('205-missing-summary.md');
  const words = text(workHtml(listed));
  assert.match(words, /has no opening paragraph, problem section or scope section to quote/);
  assert.match(words, /nothing is written in its place/);
  assert.doesNotMatch(words, /Tidy up/, 'the title is never offered as the summary');
});

test('a rough backlog card shows its lead and says what the brief does not have', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const { listed } = await setup('201-backlog-rough.md');
  const words = text(workHtml(listed));
  assert.match(words, /Let a person pin a card/);
  assert.match(words, /The brief has no scope section to quote/);
});

test('open questions are raised where the work is described', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const { listed } = await setup('203-blocked-questions.md');
  const html = workHtml(listed);
  assert.match(html, /class="ovq" role="note"/);
  assert.match(text(html), /The brief lists 2 under “Open questions”/);
});

test('a brief that changed since it was read is not shown as current', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const { listed } = await setup('202-active-run.md', { rev: 'a-newer-revision' });
  const html = workHtml(listed);
  assert.match(html, /data-stale="1"/);
  assert.match(text(html), /the summary below is not current until it arrives/);
});

test('an unread or unreadable brief summarises nothing', async () => {
  const { workHtml } = await load('views/detail/sections.js');
  const a = await setup('202-active-run.md', { detail: { loading: true } });
  assert.match(workHtml(a.listed), /aria-busy="true"[^>]*>Reading the brief…/);
  const b = await setup('202-active-run.md', { detail: { error: 'the board did not answer' } });
  const words = text(workHtml(b.listed));
  assert.match(words, /could not be read — the board did not answer\. Nothing is summarised in its place/);
});

test('the last result is the run\'s own account, and its absence is said', async () => {
  const { lastResultHtml } = await load('views/detail/sections.js');
  const { r } = await setup('164-legacy-long.md');
  const words = text(lastResultHtml({ file: r.file, stage: 'done' }, r.acc));
  assert.match(words, /^Committed on task\/164-initialize-work-directories and tested/);
  assert.match(words, /Work report by Juno, 2026-11-09 18:02: the run's own account, not a check result/);
  const none = await setup('206-unavailable-evidence.md');
  assert.match(text(lastResultHtml({ file: none.r.file, stage: 'done' }, none.r.acc)),
               /No work report is on record on this card, so its result is not stated here/);
  // a card nobody has worked yet has no result to speak of, and says nothing
  const fresh = await setup('201-backlog-rough.md');
  assert.equal(lastResultHtml({ file: fresh.r.file, stage: 'backlog' }, fresh.r.acc), '');
});

test('what the work needs is apart from what a merge needs; dependencies say where they are', async () => {
  const { requiredHtml } = await load('views/detail/sections.js');
  const { listed, r } = await setup('203-blocked-questions.md', {
    dependsOn: ['202', '999'],
    tasks: [{ file: '202-active-run.md', number: '202', stage: 'in-progress' }] });
  const html = requiredHtml(listed, null, null, r.acc);
  const words = text(html);
  assert.ok(words.indexOf('To do the work') < words.indexOf('To merge it'));
  assert.match(words, /Depends on #202 In progress · #999 not on this board/);
  assert.match(words, /2 criteria in the brief · Acceptance results not recorded/);
  assert.match(html, /href="\/cards\/202"/, 'a dependency is a link to it');
});

test('the review section leads with what is open, and says when nothing was filed', async () => {
  const { reviewFactsHtml } = await load('views/detail/sections.js');
  const rv = await setup('204-review-findings.md');
  assert.match(text(reviewFactsHtml({ file: rv.r.file }, rv.r.acc)),
               /Last review Olive asked for changes at 7d1e0aa94c21/);
  const none = await setup('202-active-run.md');
  assert.match(text(reviewFactsHtml({ file: none.r.file }, none.r.acc)), /No review has been filed on this card/);
});

test('the Overview orders the reader\'s questions, and reading it asks the board for nothing new', async () => {
  const { overviewHtml } = await load('views/detail/sections.js');
  const { S, listed, r } = await setup('204-review-findings.md');
  S.acc[r.file] = { data: r.acc };
  S.result[r.file] = { data: { status: 'off', label: 'Off', artifacts: [], checks: [], availability: [],
                               suggest: false, setting: { mode: 'off' } } };
  S.dfull[r.file] = { data: { requirements: [] } };
  const before = asked.length;
  const html = overviewHtml(listed, r.acc, '', '', '', '');
  const at = (s) => html.indexOf(s);
  for (const h of ['The work', 'Where it stands · what happens next', 'What is required',
                   'Reviews and findings', 'More about this card'])
    assert.ok(at(h) > 0, `missing ${h}`);
  assert.ok(at('The work') < at('Where it stands') && at('Where it stands') < at('What is required') &&
            at('What is required') < at('Reviews and findings') && at('Reviews and findings') < at('More about this card'),
            'the sections are in the reader\'s order');
  // the tools are no longer at the foot of the Overview: by the owner's
  // review they sit once at the top of the whole detail view (drawer.js)
  assert.equal(at('drawertools'), -1, 'the Overview carries a second tools slot');
  assert.doesNotMatch(html, /<h3>Tools<\/h3>/, 'an outer Tools heading over the tool group');
  // the delivery reading is the server's; with none in hand it is being read
  assert.match(html, /Reading the delivery status…/);
  const extra = asked.slice(before).filter(u => !/^\/api\/card\/(delivery|inputs|result|acceptance)\?/.test(u) && !/^\/api\/card\?/.test(u));
  assert.deepEqual(extra, [], 'the Overview asked for something beyond its own readings');
  assert.ok(asked.slice(before).every(u => !/launch|verify|move|refine|check-remote/.test(u)), 'nothing was started');
});

// the split is the server's own: wrapped-head.json's offsets are held to
// taskfiles.part_offsets by tests/test_task_information.py, and the server
// ends the head at the wrapped line, so the continuation arrives in the brief
const WRAPPED = JSON.parse(readFileSync(fileURLToPath(
  new URL('../fixtures/task-information/wrapped-head.json', import.meta.url)), 'utf8'));

async function briefOf(name) {
  const { briefHtml } = await load('views/detail/sections.js');
  const { S } = await setup('202-active-run.md');
  const c = WRAPPED[name];
  const file = `9-${name}.md`;
  S.detail[file] = { data: { file, stage: 'review', rev: 'r1', body: c.body, parts: c.parts,
                             outline: { state: 'none', sections: [] }, words: 3 } };
  const html = briefHtml({ file, stage: 'review', rev: 'r1', mtime: 1 });
  const grid = html.slice(html.indexOf('class="dfacts bmeta"'), html.indexOf('</dl>'));
  return { html, grid: text(grid), below: text(html.slice(html.indexOf('</dl>'))) };
}

test('a header field wrapped onto a second line stays that field\'s value', async () => {
  const { html, grid, below } = await briefOf('wrapped');
  assert.match(grid, /Status Review Priority High - the reason runs on onto a second line Type Feature/);
  assert.equal(html.split('onto a second line').length, 2, 'the continuation is not drawn again below the grid');
  assert.doesNotMatch(below, /Type/, 'the fields after the wrapped one are in the grid, not the brief');
  assert.match(below, /The brief\./);
});

test('a brief that follows a blank line is never absorbed into the last field', async () => {
  const { grid, below } = await briefOf('unwrapped');
  assert.match(grid, /Status Review Type Feature$/);
  assert.match(below, /^The brief opens here and runs on\./);
});

test('the work base sits under where it stands, a held new start in view', async () => {
  const { overviewHtml } = await load('views/detail/sections.js');
  const { S, listed, r } = await setup('204-review-findings.md');
  S.acc[r.file] = { data: r.acc };
  S.result[r.file] = { data: { status: 'off', label: 'Off', artifacts: [], checks: [], availability: [],
                               suggest: false, setting: { mode: 'off' } } };
  S.dfull[r.file] = { data: { requirements: [] } };
  S.state.plans = { [r.file]: { workBase: { headline: 'Starting from main', withdrawn: 'the prerequisite moved',
                                            accepted: { decision: 'normal', summary: 'nothing to build on' },
                                            latest: { decision: 'normal' } } } };
  const html = overviewHtml(listed, r.acc, '', '', '', '');
  const at = (s) => html.indexOf(s);
  assert.ok(at('Where it stands') < at('class="workbase"') && at('class="workbase"') < at('What is required'),
            'the work base is part of where the card stands');
  assert.match(text(html), /A new start is held: the prerequisite moved/);
});

// ── the owner's visual review: compact header controls, tools at the top ──

test('the header controls are three named icon buttons in one group, Expand saying its state', async () => {
  const { viewControls } = await load('views/detail/drawer.js');
  const { S } = await load('app/state.js');
  S.dwide = false;
  const html = viewControls('/cards/9?tab=reviews', "Copy a link to this card's reviews section", true);
  assert.equal(html.match(/class="dctl"/g).length, 1, 'one group');
  assert.match(html, /role="group" aria-label="Detail view controls"/);
  const buttons = [...html.matchAll(/<button ([^>]*)>(.*?)<\/button>/g)];
  assert.deepEqual(buttons.map(b => /id="([^"]+)"/.exec(b[1])[1]), ['dcopylink', 'dwide', 'closeDrawer']);
  for (const [, attrs, inner] of buttons) {
    assert.match(attrs, /aria-label="[A-Z][^"]+"/, 'every icon has a name in words');
    assert.match(attrs, /title="[A-Z][^"]+"/, 'and a tooltip');
    assert.match(inner, /^<svg [^>]*aria-hidden="true"/, 'the icon itself is decoration');
    assert.equal(text(inner), '', 'no visible text beside the icon');
  }
  assert.match(html, /data-copy-link="\/cards\/9\?tab=reviews"/, 'the copy link is the section\'s address');
  assert.match(html, /id="dwide"[^>]*aria-pressed="false" aria-label="Expand the reading view"/);
  assert.match(html, /data-icon="expand"/);
  S.dwide = true;
  const wide = viewControls('/cards/9', 'Copy a link to this card', true);
  assert.match(wide, /id="dwide"[^>]*aria-pressed="true" aria-label="Narrow the reading view"/);
  assert.match(wide, /data-icon="narrow"/);
  assert.doesNotMatch(wide, /data-icon="expand"/);
  S.dwide = false;
  // an address the board does not list has no reading view to widen
  assert.doesNotMatch(viewControls('/cards/9', 'Copy this address', false), /id="dwide"/);
});

test('the detail view places its one tools slot under the tab row, before every section', () => {
  const src = readFileSync(join(WEB, 'views/detail/drawer.js'), 'utf8');
  const render = src.slice(src.indexOf('function renderDrawer('), src.indexOf('function renderCardLink('));
  const at = (s) => render.indexOf(s);
  assert.equal(render.split('id="drawertools"').length, 2, 'exactly one tools slot');
  assert.ok(at('dtabsHtml(t, tab, acc)') < at('id="drawertools"') &&
            at('id="drawertools"') < at("dsec('overview'"), 'the tools are under the tabs and above the sections');
  assert.match(render, /label\.textContent = 'Tools'/, 'the group carries the one Tools label');
  assert.doesNotMatch(render, /<h3>Tools<\/h3>/);
});
