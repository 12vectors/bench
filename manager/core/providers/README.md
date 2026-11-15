# Hosting providers

A **hosting provider** is how bench opens, observes, reviews and merges a
change request (a GitHub pull request) on the host the application's
remote lives on. It is not a coding-agent adapter and not a project
driver:

| Kind | Location | Contract |
| --- | --- | --- |
| Coding-agent adapters | `adapters/` | executables, an environment contract |
| Project drivers | `local/driver/` | an executable |
| Hosting providers | `providers/` | Python functions |

Providers are functions because core calls them many times a minute and
needs structured answers. Two ship: `github` (the default) and `none`.
There is no plugin discovery and no local override: `githost` loads
`providers/<name>/provider.py` from core's own tree, by path.

## Selection

`BOARD_GIT_PROVIDER` in `manager/local/.env`:

- empty or `github` — today's behaviour. The GitHub provider recognises
  a remote on `github.com` (and any host named in `BOARD_GITHUB_HOSTS`,
  for GitHub Enterprise) and tells every `gh` command which repository it
  acts on. A local path or `file://` remote has no host to mistake, so gh
  is left to infer the repository, as it always was. **A remote on any
  other host is unrecognized:** Settings and the configuration check say
  so, every capability reads unavailable with that reason, and `gh` is
  never run against it.
- `none` — the application is worked with git alone. No change request
  is opened, observed, reviewed on a host or merged there; no chip
  appears; no launch is granted a host verb.

A value bench does not ship acts as `none` and is reported.

## The directory

```
providers/<name>/
├── provider.py         standard library only; imports no core module
└── capabilities.json   what it implements, and the functions it defines
```

`capabilities.json` declares `operations` (the capabilities the provider
implements) and `functions` (checked at load: a declared function that is
missing refuses the load).

## The functions

Every function takes the `ctx` dict `githost` builds and returns a plain
value. `ctx` carries `run(argv, timeout)` — in the application checkout,
never raising (a failure to start is 128; a timeout is 1 with
`<cli> timed out`) — `cli`, the binary (`BOARD_GH_BIN`), `slug`
(`owner/name` or None) and `hosts`.

| Function | Kind | Returns |
| --- | --- | --- |
| `detect(ctx, url)` | read, local | `{applies, kind, slug, reason}`; `kind` is `recognized`, `local`, `none` or `unrecognized` |
| `probe(ctx, active=False)` | read, local, never the network | per capability `{state: available \| unavailable \| unknown, reason}` |
| `change_ref(url)` / `is_change_url(url)` | pure | the change number, or None / whether a url is one |
| `find_change(ctx, branch)` | read | `{url, base}` or None |
| `changes_targeting(ctx, base)` | read | `[{url, head}]` for the open changes whose base is `base`, `[]` for none, None when it cannot say. Cleanup keeps a branch while the answer is not `[]` (task 117); `none` answers `[]`, since it observes no change request |
| `change_identity(ctx, number)` | read | `{ok, state, merged, base, baseCommit, head, headCommit, crossRepository}` |
| `change_status(ctx, number)` | read | the normalized status below, or None |
| `review_records(ctx, number)` | read | `[{id, reviewer, state, commit, submitted}]`, or None |
| `head_refs(number, head_ref)` | pure | refs that can name the head |
| `open_change(ctx, head, base, title, body)` | mutation | `{ok, url}`, or `{ok: False, exists, detail}` |
| `merge_change(ctx, number, expect_head, timeout)` | mutation | `{ok, detail, returncode}` |
| `request_external_review(ctx, number)` | mutation | `{ok, detail}` |
| `agent_verbs(intent)` | pure | `{provider, allow, publish, fragments}` |
| `probe_stacking(ctx, active=False)` | read, local, never the network | per stacking capability (`branch-stack`, `retarget`, `native-stack`, `parent-merge-effect`, `parent-delete-effect`) `{state, reason, source}`, plus `matrix`, the revision of the provider's written evidence matrix (task 118). Passive runs no process; `active` may read the CLI's help text. A host behaviour that cannot be observed locally is always `unknown` |
| `WORDS` | data | the words a sentence about the host uses |

Normalized values use words core already has: change state `open` /
`merged` / `closed`; mergeable `yes` / `no` / `unknown`; check `pass` /
`fail` / `running`; review `approved` / `changes-requested` /
`commented` / `dismissed`. The provider maps its native values at the
edge; `changes._fold` computes the verdict.

## Capabilities and refusal

`githost.capabilities()` is passive: no probe process, no network. The
one capability that needs a process to learn — whether a merge can be
made conditional on a head (`gh pr merge --help`) — reads `unknown` until
a merge actively asks. **`unknown` never authorizes a mutation**:
`githost.require` refuses anything but `available`, by name, with the
reason.

## Stacking: a discovery reading, not a capability to act on

`githost.stacking(active=False)` reads `probe_stacking` through the
provider in force (an unrecognized remote reads every row unavailable).
Each row's `source` says what kind of knowledge it is — `installed-cli`,
`local`, `documentation` or `git` — matching the columns of
`providers/github/STACKING.md`, the dated evidence matrix that explains
it. No code path mutates a host on this reading; WP10b's actions will
ask for `available` exactly as `require` does.

## What reaches a coding agent

`agent_verbs(intent)` gives the command prefixes a launch intent is
granted on this host (`allow`) and the publication verbs a profile that
denies `publish` takes away (`publish`). Bench hands them to the adapter
as `AGENT_HOST`; both shipped adapters render them in their own rule
syntax beside their git prefixes, so a GitHub board's permission
documents are byte for byte what they were. `fragments` are the
`{host_*}` placeholders of `act-pr.md` and `review-pr.md`, formatted with
the card's branch.

## Persisted records

Nothing persisted is renamed: the `**PR:**` header, the `github` and
`separate-reviewer` review-policy words, the review record's state words
(`APPROVED`, …) and every record key stay as they were. New PR snapshots
carry `provider`; a record without one reads as `github`.
