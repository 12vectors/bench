/* An on-demand reading, keyed, with one race policy (task 125, WP16).

   The detail view reads three things the state payload does not carry —
   a card's acceptance, its discussion and its full delivery reading —
   and each used to carry its own answer to the same three races. They
   share this one:

   - **One request in flight, one queued.** Asked again while a read is
     out — forced, or because what the reading is about has changed — the
     ask is remembered and made once the read lands: exactly one
     follow-up however many asks arrive, never dropped. That is what makes
     a comment saved during a read always appear: the read in flight may
     have read the card before the write.
   - **The newest reading stands.** Every request is numbered, and a reply
     older than the reading already stored is dropped, repainting nothing.
   - **A failure keeps the last good data, marked.** The entry keeps its
     data, gains the error and `stale: true`, and is asked again after
     `retryMs` — on a timer, not on every repaint.

   Nothing here is a mutation: a reading is a GET, and a GET is the only
   thing this ever repeats. Entries live in a store the caller owns (an
   object on the page state), keyed by id: `{data, error, stale, loading,
   key, retryAt}`. `key` is the stamp of what the stored data answers. */

const DEFAULT_RETRY_MS = 5000;

function defaultAccept(ok, body) {
  return ok ? { data: body }
            : { error: (body && body.error) || 'the board did not answer' };
}

function resource({ store, url, accept = defaultAccept, retryMs = DEFAULT_RETRY_MS,
                    ask = (u) => globalThis.fetch(u), now = () => Date.now(),
                    later = (fn, ms) => setTimeout(fn, ms) }) {
  const runs = {};   // id -> {seq, stored, inflight, queued}

  function runOf(id) {
    return runs[id] || (runs[id] = { seq: 0, stored: 0, inflight: null, queued: null });
  }

  function fresh(entry, key) {
    if (!entry || entry.key !== key) return false;
    return !entry.error || now() < (entry.retryAt || 0);
  }

  /* Ask for the reading of `id` as of `key`. Returns the entry as it
     stands now — the last good data while a new one is asked for. */
  function read(id, key, { force = false, arg, done } = {}) {
    const run = runOf(id);
    if (run.inflight) {
      if (force || key !== run.inflight.key) run.queued = { key, arg, done };
      else if (done) run.inflight.done = done;
      return store[id];
    }
    if (!force && fresh(store[id], key)) return store[id];
    start(id, { key, arg, done });
    return store[id];
  }

  function start(id, req) {
    const run = runOf(id);
    const seq = ++run.seq;
    run.inflight = { ...req, seq };
    const prior = store[id] || {};
    store[id] = { ...prior, data: prior.data ?? null, loading: true };
    Promise.resolve()
      .then(() => ask(url(id, req.arg, req.key)))
      .then(res => res.json().catch(() => ({})).then(body => accept(res.ok, body, res)))
      .catch(() => ({ error: 'the board could not be asked' }))
      .then(answer => land(id, seq, run.inflight && run.inflight.seq === seq
        ? run.inflight : req, answer));
  }

  function land(id, seq, req, answer) {
    const run = runOf(id);
    if (run.inflight && run.inflight.seq === seq) run.inflight = null;
    let painted = false;
    if (seq > run.stored) {
      run.stored = seq;
      const prior = store[id] || {};
      store[id] = answer.error == null
        ? { data: answer.data, error: null, stale: false, loading: false, key: req.key }
        : { data: prior.data ?? null, error: answer.error, stale: prior.data != null,
            loading: false, key: req.key, retryAt: now() + retryMs };
      painted = true;
      if (answer.error != null && req.done) later(req.done, retryMs + 50);
    }
    const next = run.queued;
    run.queued = null;
    if (next) start(id, next);
    else if (store[id]) store[id].loading = false;
    if (painted && req.done) req.done(store[id]);
  }

  /* Drop a reading: an in-flight reply for it is dropped too. */
  function forget(id) {
    const run = runOf(id);
    run.stored = ++run.seq;
    run.queued = null;
    run.inflight = null;
    delete store[id];
  }

  /* Store a reading obtained elsewhere (another surface read the same
     thing): it counts as the newest. */
  function put(id, key, data) {
    const run = runOf(id);
    run.stored = ++run.seq;
    store[id] = { data, error: null, stale: false, loading: !!run.inflight, key };
  }

  function pending(id) {
    const run = runs[id];
    return { inflight: !!(run && run.inflight), queued: !!(run && run.queued) };
  }

  return { forget, pending, put, read };
}

/* What a surface says above a reading it kept after a failed re-read. */
function staleWords(error) {
  return `Could not be read again — ${error}. Showing the last reading, ` +
    'which may be out of date; it is asked for again shortly.';
}

export {
  DEFAULT_RETRY_MS, defaultAccept, resource, staleWords
};
