# 32 — Serve bench.12vectors.com from a Cloudflare Worker

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/27
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


---

## Work report — 2026-07-31 11:49 (Ada)

four" — they are acceptance criteria 1, 2 and 4, and they are the ones no test in this repo can reach.
3. **Replace the account line** in `site/README.md`'s "Where the site lives" table with what `wrangler whoami` prints. I described the account by its defining property (the one holding the `12vectors.com` zone) rather than inventing a name or ID.
4. **Open the follow-up card** for a GitHub Action on merge, if you still want one.

## What to know

The card's open question carried the author's own recommendation — manual deploy for v1 — so I built that rather than sending the card back: `site/wrangler.jsonc` and a documented command sequence, no workflow file, no Cloudflare token in repository secrets.

Serving the site needed three things from the builder first, which is the first commit (`ad639a3`):

- **A real 404.** Cloudflare's `not_found_handling: "404-page"` wants a literal `404.html` at the root of the assets directory, so a manifest route may now name an `.html` file instead of ending in `/`. `/404.html` is a normal entry with a new `notfound` layout — the site's design and nav, a link back to the landing page, `noindex`, no canonical, and a `null` section that keeps it off the nav it renders.
- **`site/root/`**, copied verbatim to the top of the build the way `static/` is copied into a subdirectory. It holds `_headers`, which the host reads from the root and nowhere else. A file in `root/` that a route would also write stops the build.
- **Fingerprinted asset urls.** The templates link the stylesheet and icon through `$stylesheet`/`$icon`, carrying `?v=<hash>` of the file's contents. Nothing in `static/` is renamed, so the tree and the `/static/*` glob are unchanged — but the url moves when the file does, which is what makes the year-long `immutable` cache safe rather than a way to ship an invisible deploy.

Two judgement calls worth a look. I added a `default-src 'none'` **Content-Security-Policy** to `site/root/_headers` beyond the three headers the card named: it turns "no analytics, no third-party anything" into something the browser enforces, and a test asserts no built page contains an inline `<script>` or `style=` that the policy would block. And I could not reach the Cloudflare docs from this run, so two behaviours are asserted from memory: that Workers static assets reads `_headers` at all, and that a later rule wins on a header it repeats. Both are written to fail safe — if either is wrong, HTML still revalidates and the static assets merely revalidate more often than needed.

**Review first:** `site/wrangler.jsonc` and `site/root/_headers` — everything the live site does is decided in those two files. Then `site/build.py`'s `target_for`, `stamp` and `root_files`, which are the builder's side of them. `tests/test_site_deploy.py` covers the config, the built artefacts, and the fact that `wrangler.jsonc`, `pages.json` and `README.md` cannot drift apart about which domain this is; its docstring says plainly what it does not cover.
