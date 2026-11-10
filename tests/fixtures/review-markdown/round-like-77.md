**Verdict: the Agents section works, but two things need fixing before it merges.**

I read the full diff against the brief and ran the page at 1440 and 390px. The
per-purpose rows resolve through `config.agent_choice(intent)`, and the preview
compares two resolutions rather than predicting one.

**What I checked**
- `manager/core/agentsettings.py` — the writer only touches `BOARD_AGENT_*` keys
  and keeps every other line of `local/.env` byte for byte.
- The template contract in `templates.py`:
  - a placeholder such as `{body.x}` is refused;
  - the `NOT READY:` marker must still be asked for.
- The history line written to `state/settings/agents-history.jsonl` carries
  *names and hashes*, never a value.

## Before merging

1. **Typing a model must enable Apply.** Today the button stays disabled until
   a repaint — see `wireAgents()` in `board.html`.
2. **Remove the probe commits** (`PROBE (temporary)`) from the branch.

### To know (not blocking)

| Area | State |
| --- | --- |
| Harness rows | `opencode` drawn as `not installed` with its remedy |
| Preview | only purposes whose launch changes read as changed |

```python
def agent_choice(intent: str) -> dict:
    return {"harness": "claude", "model": None}   # **not bold**, `not code`
```

> Formal GitHub approval is a separate fact: this account opened the PR.

Full log: https://github.com/12vectors/bench/pull/77#issuecomment-1234567890.

PR REVIEW: REQUEST CHANGES
