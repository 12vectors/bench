/* A kept fold's open or closed choice, recorded as the reader made it
   (task 155).

   The detail view is redrawn from a string on every live update, so a
   `<details data-keep|data-fold>` is drawn open or closed from the choice
   recorded for it (`store['<card>:<key>']`). The browser tells a page that
   a fold changed only through a `toggle` event it *queues* — after the
   click, the Enter or the Space that opened it has finished. A redraw
   landing in between (a stream frame, a reading arriving) replaced the
   fold before that event was delivered, and drew it from the choice
   before the click: the reader's fold snapped shut, and the evidence under
   it went with it. The old node's event arrived later, on a node no
   longer in the page.

   So the choice is recorded by whichever comes first — the event, or the
   redraw reading the fold it is about to replace (`harvestFolds`) — and
   only once: each fold remembers the choice last recorded for it, and an
   event or a harvest that finds nothing new records nothing. That keeps a
   detached fold's late event from overwriting a newer choice made on the
   fold that replaced it, and keeps a choice the page set in the store
   before a redraw (open one review, say) from being undone by a fold the
   reader never touched. Every choice is filed under the card the fold was
   drawn for, never the card drawn next.

   Pure: the store and the elements are passed in, so it runs under Node. */

const recorded = new WeakMap();   // fold -> { file, open } last recorded
const FOLDS = 'details[data-keep], details[data-fold]';

/* The keys a fold's choice is filed under: its data-keep or data-fold,
   and — for Show the result's folds — the key their renderer reads. */
export function foldKeys(d) {
  const ds = d.dataset || {};
  const keys = [];
  if (ds.keep || ds.fold) keys.push(ds.keep || ds.fold);
  if (ds.resFold) keys.push(`res-${ds.resFold}`);
  return keys;
}

/* Record a fold's current choice when it is not the one last recorded.
   Answers whether anything was written. */
export function recordFold(store, d) {
  const was = recorded.get(d);
  if (!was || was.open === d.open) return false;
  recorded.set(d, { file: was.file, open: d.open });
  for (const k of foldKeys(d)) store[`${was.file}:${k}`] = d.open;
  return true;
}

/* Every kept fold under `root`, drawn for `file`: its drawn state is what
   is on record, and its toggle records a change. */
export function wireFolds(store, file, root) {
  root.querySelectorAll(FOLDS).forEach(d => {
    recorded.set(d, { file, open: d.open });
    d.addEventListener('toggle', () => recordFold(store, d));
  });
}

/* Before `root` is redrawn: record every wired fold's choice its queued
   toggle has not recorded yet. Folds nothing wired are left alone. */
export function harvestFolds(store, root) {
  if (!root) return;
  root.querySelectorAll(FOLDS).forEach(d => recordFold(store, d));
}
