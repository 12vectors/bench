/* One way to repaint (task 125, WP16).

   The page redraws by demolition, and a burst of stream frames used to
   ask for a whole redraw per frame. Every repaint now goes through here:
   `scheduleRender()` asks for one on the next animation frame however
   many times it is called before then, and `flushRender()` paints now —
   cancelling the frame already asked for, so a reading that has just
   landed and a frame that was already queued are still one paint.

   The painter is registered by the composition root (`onRender`), so a
   module that wants a repaint asks for one without importing the
   renderer — and without the cycle that importing it would make. */
let painter = null;
let queued = false;
let frame = 0;
let paints = 0;

function onRender(fn) { painter = fn; }

function frameFn() {
  return globalThis.requestAnimationFrame
    ? globalThis.requestAnimationFrame.bind(globalThis)
    : (fn) => setTimeout(fn, 16);
}

function cancelFn() {
  return globalThis.cancelAnimationFrame
    ? globalThis.cancelAnimationFrame.bind(globalThis)
    : (id) => clearTimeout(id);
}

function paint() {
  queued = false;
  frame = 0;
  if (!painter) return;
  paints++;
  painter();
}

function scheduleRender() {
  if (queued) return;
  queued = true;
  frame = frameFn()(paint);
}

function flushRender() {
  if (queued) cancelFn()(frame);
  paint();
}

/* how many paints have run: for a test, and for a person at the console */
function renderCount() { return paints; }
function renderPending() { return queued; }

export {
  flushRender, onRender, renderCount, renderPending, scheduleRender
};
