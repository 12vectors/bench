// The project handbook in the Focus view's Permissions & inputs panel
// (task 203): what a frozen run was given, beside the handbook as it
// reads today, in neutral words — never an alarm, never a claim that the
// run read or followed it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], getElementById: () => null };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = () => 0;
const asked = [];
globalThis.fetch = (url) => { asked.push(String(url)); return new Promise(() => {}); };

const text = (html) => String(html).replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&')
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/\s+/g, ' ').trim();

const manifest = (handbook, context = []) => ({
  intent: 'work', policyId: 'pol-1', cwd: { policy: 'worktree' }, commands: [], verify: [],
  denied: [], writable: [{ root: 'worktree' }], publication: { commit: true },
  guarantees: { pending: [] }, capabilities: {}, context, handbook, v: 1, revision: 'rev-1',
  runner: { module: 'profiles', version: 1 }, adapter: { name: 'claude', declared: true },
  source: { kind: 'legacy', precedence: [] },
});

const SUPPLIED = {
  state: 'supplied', label: 'Handbook supplied', supplied: 1, revision: 'r-then',
  brainRevision: 'b-1', delivery: 'exact-read', deliveryNote: 'pinned read-only and granted by one exact read',
  pinnedSha256: 'a'.repeat(64), omitted: [], diagnostics: [],
  observed: 'bytes supplied, nothing more: whether the run read or followed them is not observed',
  limits: { bytes: 49152, entryBytes: 16384, order: ['overview'] },
  sections: [{ id: 'overview', title: 'Overview', items: [
    { id: 'e-what', title: 'What the project is', state: 'supplied', supplied: true,
      revision: '0123456789abcdef', note: 'Current · standing: deployed',
      qualifications: ['it says deployed, and no deployment identity is recorded'] }] }],
};

async function panel(handbook, now, context) {
  const { S } = await load('app/state.js');
  const { permissionsPanel } = await load('views/focus.js');
  S.manifests['run-a'] = { manifest: manifest(handbook, context), freshness: { fresh: true, changed: [] },
                           handbookNow: now || null };
  return permissionsPanel({ id: 'run-a', mode: 'work' });
}

test('a supplied handbook is one quiet line, the selection one fold away', async () => {
  const html = await panel(SUPPLIED, { label: 'Handbook supplied', revision: 'r-now', state: 'supplied' },
    [{ name: 'project-handbook', kind: 'handbook', status: 'pinned', readable: true, required: false }]);
  const words = text(html);
  assert.match(words, /Handbook Handbook supplied/);
  assert.match(words, /1 entry supplied/);
  assert.match(html, /data-state="supplied"/);
  assert.match(html, /<details class="fold"><summary>Handbook selection/);
  assert.match(words, /qualified: it says deployed, and no deployment identity is recorded/);
  // the frozen run and today's handbook are never read as one
  assert.match(words, /today the handbook reads handbook supplied at revision r-now; this run keeps what it was given/);
  // supplied is not read: no claim of comprehension
  assert.match(words, /whether the run read or followed them is not observed/);
  assert.doesNotMatch(words, /\bread and understood\b|\bcomplied\b/i);
  assert.deepEqual(asked, []);
});

test('no content yet and a delivery problem are notes, not failures', async () => {
  const none = await panel({ state: 'none', label: 'No handbook content yet', supplied: 0, sections: [],
                             omitted: [], diagnostics: [] });
  assert.match(text(none), /No handbook content yet/);
  assert.match(text(none), /nothing to supply; the run starts normally/);
  const broken = await panel(
    { ...SUPPLIED, state: 'unreadable', label: 'Handbook could not be delivered',
      diagnostics: [{ code: 'handbook-not-delivered', message: 'the handbook selection was not delivered: x',
                      remedy: 'nothing is needed for this run' }] },
    null,
    [{ name: 'project-handbook', kind: 'handbook', status: 'unreadable', readable: false, required: false }]);
  const words = text(broken);
  assert.match(words, /Handbook could not be delivered/);
  assert.match(words, /note handbook-not-delivered/);
  assert.match(words, /not delivered/);
  // the handbook row is never drawn as a failing input
  const hbRow = broken.split('project-handbook')[1] || '';
  assert.doesNotMatch(hbRow.slice(0, 200), /class="[^"]*\bfail\b/);
  assert.doesNotMatch(broken.split('Permissions & inputs')[1].split('<div class="check-row')[0], /alarm/);
});

test('a run that recorded no handbook says so rather than inventing one', async () => {
  const html = await panel(undefined);
  assert.match(text(html), /Handbook Not recorded/);
  assert.match(text(html), /not recorded for this run/);
});
