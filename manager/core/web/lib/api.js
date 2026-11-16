/* The page's one way to the board (task 124, WP15).

   Every module asks the board through `fetch` from here rather than the
   browser's own, so every mutation it sends names the build this page was
   served as (`X-Bench-Build`). After a restart into another build the
   board refuses a browser's mutation from an older page — `stale-page`,
   409, nothing changed — instead of letting old code write against new
   rules. A shim or a script states no Origin and is not asked for a
   build; only a request that says it is a browser's is.

   The global `fetch` is looked up at call time, so whatever stands at
   `window.fetch` when a request is made is what sends it. A reading is a
   GET and carries nothing extra; a refusal is the caller's reply as ever,
   and is also told to whoever registered with `onStalePage` — the page's
   reload banner — once per page. */
const PAGE_BUILD = document.querySelector('meta[name="bench-build"]')?.content || '';
const BUILD_HEADER = 'X-Bench-Build';

let staleListener = null;
let staleTold = false;

function onStalePage(listener) { staleListener = listener; }

function withBuild(opts) {
  const method = String(opts.method || 'GET').toUpperCase();
  if (method === 'GET' || method === 'HEAD') return opts;
  if (opts.headers instanceof Headers) {
    const headers = new Headers(opts.headers);
    headers.set(BUILD_HEADER, PAGE_BUILD);
    return { ...opts, headers };
  }
  // a plain object stays one, so a reader of the request sees its keys
  return { ...opts, headers: { ...(opts.headers || {}), [BUILD_HEADER]: PAGE_BUILD } };
}

async function fetch(url, opts = {}) {
  const res = await globalThis.fetch(url, withBuild(opts));
  if (res.status === 409 && !staleTold) {
    const data = await res.clone().json().catch(() => null);
    if (data && data.code === 'stale-page') {
      staleTold = true;
      if (staleListener) staleListener(data);
    }
  }
  return res;
}

/* A mutation, in one shape (task 125, WP16): JSON out, JSON back, and
   `{ok, status, data, error}` whatever happened — a refusal's own words
   in `error`, a network failure as `status: 0`. It is sent exactly once:
   a mutation is never retried here, because a write that may have landed
   is not this helper's to repeat. */
async function post(url, body) {
  let res;
  try {
    res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                             body: JSON.stringify(body || {}) });
  } catch (e) {
    return { ok: false, status: 0, data: {}, error: 'the board could not be asked' };
  }
  const data = await res.json().catch(() => ({}));
  const ok = res.ok;
  return { ok, status: res.status, data,
           error: ok ? null : (data && data.error) || `the board answered ${res.status}` };
}

export {
  BUILD_HEADER, fetch, onStalePage, PAGE_BUILD, post, staleListener, staleTold,
  withBuild
};
