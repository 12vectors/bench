# 32 — Serve bench.12vectors.com from a Cloudflare Worker

**Status:** In Progress
**Assignee:** istos
**Priority:** High — the site is not a site until it has an address
**Type:** Feature
**Depends on:** 31 — there must be a `site/dist/` to serve

Put the built minisite behind `bench.12vectors.com` on Cloudflare Workers,
with a deploy anyone on the team can run and a written record of what the
domain is wired to.

## Context

- Task 31 produces `site/dist/`: plain static files, no server-side
  rendering, no API. Workers **static assets** (`assets` in
  `wrangler.jsonc`) covers this without a KV namespace or a fetch
  handler; reach for a handler only if something below genuinely needs
  one.
- Preconditions that are a human's, not an agent's: the `12vectors.com`
  zone must be on Cloudflare, and whoever deploys needs `wrangler login`
  against an account with access to it. If the zone is elsewhere, this
  card becomes a DNS card first and should be sent back.
- bench has no CI deploy story of any kind today — no workflows deploy
  anything; releases are cut by hand with `../../release.sh`. Whatever
  this card picks is the first one.

**Affected areas:** `site/` (wrangler config, deploy docs) and possibly
`.github/workflows/`. No `manager/` code changes.

## What to build

- `site/wrangler.jsonc` — a Worker serving `site/dist/` as static
  assets, bound to `bench.12vectors.com` as a custom domain.
- Clean URLs: `/start` serves the page, not `/start.html`, and a
  trailing-slash variant does not become a second URL with duplicate
  content.
- A real 404: unknown paths render the site's own not-found page in the
  site's own design, with a link back to the landing page.
- Caching that suits the two kinds of file: long-lived immutable caching
  for fingerprinted static assets, short or revalidated for HTML, so a
  deploy is visible without a hard refresh.
- Baseline response headers a public page should carry
  (`X-Content-Type-Options`, a referrer policy, HSTS) — no analytics, no
  third-party anything.
- `site/README.md`: how to build, how to preview locally, how to deploy,
  and what the domain currently points at. Short.

**Out of scope** — tempting neighbours left alone:

- Redirecting or touching `12vectors.com` itself, or any other subdomain.
- Analytics, cookie banners, consent — the site collects nothing, and
  that should stay true.
- Serving the board itself, or anything dynamic. This is static hosting.

## Acceptance

- [ ] `https://bench.12vectors.com` serves the built landing page over
      TLS, with `www`-less canonical URLs.
- [ ] Given a request for a path that does not exist, when it is served,
      then the site's own 404 page renders with a 404 status — not the
      landing page with a 200.
- [ ] A deploy from a clean checkout is one documented command sequence,
      and re-running it is safe.
- [ ] Edge case: a stale HTML page does not survive a deploy — a
      published change is visible on reload without clearing the cache.
- [ ] `site/README.md` names the account, the Worker and the route, so
      the next person does not have to guess where the site lives.

## Open questions

- **Manual deploy or on merge?** `wrangler deploy` run by a human is the
  smaller thing and matches how releases already work here; a GitHub
  Action on merge to `main` keeps the site honest but means putting a
  Cloudflare API token in repo secrets and giving this repo its first
  deploy pipeline. Recommendation: manual for v1, and open a follow-up
  card for the Action once the pages stop changing hourly.

## Notes

**Risks** — a custom domain on Workers takes the hostname over at the
zone level. Do it knowing nothing else answers on `bench.12vectors.com`
today.
