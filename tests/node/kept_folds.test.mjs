// A kept fold's choice across a redraw (task 155), run as code.
//
// lib/folds.js records whether a detail-view fold is open. The browser
// tells the page through a `toggle` event it queues after the click, so a
// redraw can land before it. These fakes queue their toggle the same way
// and fire it only when the test says the queued task runs — which is how
// each ordering below is made deterministic rather than hoped for.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const { wireFolds, harvestFolds, recordFold, foldKeys } =
  await import(pathToFileURL(join(WEB, 'lib/folds.js')).href);

// a <details> as far as lib/folds.js sees one: `open`, its data-*, its
// listeners, and a toggle the browser has queued but not yet delivered
function fold(dataset, open = false) {
  const listeners = [];
  return {
    dataset, open, queued: false,
    addEventListener(type, fn) { if (type === 'toggle') listeners.push(fn); },
    // the reader's click, Enter or Space: the state changes now, the event later
    set(v) { if (this.open !== v) { this.open = v; this.queued = true; } },
    deliver() { if (this.queued) { this.queued = false; listeners.forEach(fn => fn()); } },
  };
}
const root = (...folds) => ({ querySelectorAll: () => folds });

// a redraw as renderDrawer does it: harvest, then new folds drawn from the
// store, then wired — the old folds are detached but keep their queue
function redraw(store, file, old, make) {
  harvestFolds(store, old);
  const fresh = make(store);
  wireFolds(store, file, fresh);
  return fresh;
}

const wb = (store, file = 'a.md') =>
  root(fold({ keep: 'workbase' }, !!store[`${file}:workbase`]));

test('a fold opened, then redrawn before its queued toggle, is drawn open', () => {
  const store = {};
  const first = wb(store);
  wireFolds(store, 'a.md', first);
  const [d] = first.querySelectorAll();
  d.set(true);                                  // the click; toggle still queued
  const second = redraw(store, 'a.md', first, s => wb(s));
  assert.equal(second.querySelectorAll()[0].open, true, 'the redraw drew the fold shut');
  assert.equal(store['a.md:workbase'], true);
  d.deliver();                                  // the detached fold's late event
  assert.equal(store['a.md:workbase'], true);
});

test('a fold closed, then redrawn before its queued toggle, is drawn closed', () => {
  const store = { 'a.md:workbase': true };
  const first = wb(store);
  wireFolds(store, 'a.md', first);
  const [d] = first.querySelectorAll();
  assert.equal(d.open, true);
  d.set(false);
  const second = redraw(store, 'a.md', first, s => wb(s));
  assert.equal(second.querySelectorAll()[0].open, false);
  d.deliver();
  assert.equal(store['a.md:workbase'], false);
});

test("a detached fold's late event does not overwrite a newer choice", () => {
  const store = {};
  const first = wb(store);
  wireFolds(store, 'a.md', first);
  const [old] = first.querySelectorAll();
  old.set(true);
  const second = redraw(store, 'a.md', first, s => wb(s));
  const [now] = second.querySelectorAll();
  now.set(false); now.deliver();               // the reader closes the new one
  old.deliver();                                // then the old event arrives
  assert.equal(store['a.md:workbase'], false);
});

test('the choice is filed under the card the fold was drawn for, not the next', () => {
  const store = {};
  const a = wb(store, 'a.md');
  wireFolds(store, 'a.md', a);
  const [d] = a.querySelectorAll();
  d.set(true);
  const b = redraw(store, 'b.md', a, s => wb(s, 'b.md'));   // another card opened
  assert.equal(store['a.md:workbase'], true);
  assert.equal(store['b.md:workbase'], undefined);
  assert.equal(b.querySelectorAll()[0].open, false, "card a's choice crossed to card b");
  d.deliver();
  assert.equal(store['b.md:workbase'], undefined);
});

test('a choice the page set before redrawing is not undone by an untouched fold', () => {
  const store = {};
  const first = root(fold({ keep: 'rv-1' }));
  wireFolds(store, 'a.md', first);
  store['a.md:rv-1'] = true;                    // openReview: set, then redraw
  harvestFolds(store, first);
  assert.equal(store['a.md:rv-1'], true);
});

test('a fold nothing wired is left alone, and the toggle that drew it records nothing new', () => {
  const store = {};
  const loose = fold({ keep: 'x' });
  loose.open = true;
  harvestFolds(store, root(loose));
  assert.deepEqual(store, {});
  const drawn = fold({ keep: 'y' }, true);
  wireFolds(store, 'a.md', root(drawn));
  assert.equal(recordFold(store, drawn), false);
  assert.deepEqual(store, {});
});

test("Show the result's folds are filed under the key their renderer reads too", () => {
  assert.deepEqual(foldKeys({ dataset: { keep: 'res-d-x-1', resFold: '1' } }), ['res-d-x-1', 'res-1']);
  assert.deepEqual(foldKeys({ dataset: { fold: 'ov-more' } }), ['ov-more']);
  const store = {};
  const d = fold({ keep: 'res-edit', resFold: 'edit' });
  wireFolds(store, 'a.md', root(d));
  d.set(true); d.deliver();
  assert.equal(store['a.md:res-edit'], true);
});
