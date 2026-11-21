// An unfinished completion, as the merge sheet draws it (task 166): the
// server's outcome and next step, and — folded — the identities it keeps
// apart, the operation that left them, and an actor said to be unverified
// rather than invented. The real views/card.js, imported under Node.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [] };
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null),
                            setItem: (k, v) => store.set(k, String(v)) };
globalThis.matchMedia = () => ({ matches: false });

const { unfinishedPanel } = await import(pathToFileURL(join(WEB, 'views/card.js')).href);

const HEAD = 'a'.repeat(40), MERGE = 'b'.repeat(40), REMOTE = 'c'.repeat(40);

test('a merge delivered elsewhere says so, with the next step and the identities', () => {
  const html = unfinishedPanel({
    branch: 'task/63-x', target: 'main', mode: 'local', remote: 'origin',
    commit: HEAD, merge: MERGE, remoteTip: REMOTE, publication: 'failed',
    outcome: 'diverged', deliveredElsewhere: true, diverged: true,
    detail: '! [rejected] main -> main (fetch first) <script>',
    action: 'Keep it under a reference of your own (git branch recovery/63-x bbbbbbbbbbbb)',
    operation: { id: 'cmp-0123456789ab', source: 'api', actor: null },
    history: [{ id: 'cmp-0123456789ab', source: 'api', actor: null, outcome: 'diverged' }],
  });
  assert.match(html, /Delivered elsewhere — the merge made here is not published/);
  assert.match(html, /already holds aaaaaaaaaaaa through another merge/);
  assert.match(html, /recovery\/63-x/);
  assert.match(html, /<details class="cmp-evidence">/);
  assert.match(html, /merge made here<\/dt><dd class="mono">bbbbbbbbbbbb/);
  assert.match(html, /remote's main<\/dt><dd class="mono">cccccccccccc/);
  assert.match(html, /cmp-0123456789ab/);
  assert.match(html, /actor not verified/);
  assert.doesNotMatch(html, /<script>/, 'the remote\'s words are escaped');
  assert.match(html, /data-outcome="diverged"/);
});

test('a rejected push names the merge it did not publish, and never says published', () => {
  const html = unfinishedPanel({
    branch: 'task/63-x', target: 'main', mode: 'local', remote: 'origin',
    commit: HEAD, merge: MERGE, publication: 'failed', outcome: 'rejected',
    detail: 'remote: main is protected', operation: { id: 'cmp-1', source: 'orchestrator', actor: 'ada' },
  });
  assert.match(html, /Merged, not published — the remote refused the push/);
  assert.match(html, /does not hold the merge bbbbbbbbbbbb/);
  assert.match(html, /orchestrator · ada/);
  assert.doesNotMatch(html, /published and|was published/);
});

test('a record from an older board, with no outcome, still reads honestly', () => {
  const html = unfinishedPanel({
    branch: 'task/63-x', target: 'main', mode: 'local', remote: 'origin',
    commit: HEAD, publication: 'unconfirmed', detail: 'origin could not be read',
  });
  assert.match(html, /not established/);
  assert.match(html, /merge made here<\/dt><dd class="mono">none/);
});
