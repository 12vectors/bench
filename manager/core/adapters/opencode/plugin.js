// opencode → board bridge (the opencode adapter's event edge).
//
// Subscribes to opencode's event bus and tool hooks, translates them into
// the board's NORMALIZED event schema (v1) — the fixed contract every
// adapter speaks, documented in core/adapters/README.md — and POSTs them
// to /api/events with the BOARD_* env forwarded.
//
// Coverage is deliberately coarse for now (session/idle/end plus one
// event per tool call, kinds mapped from the tool name); refine kinds
// before inventing new ones. Fails silently and fast: a session must
// never slow down or break because the board isn't running.

import fs from "node:fs"
import path from "node:path"

function boardPort(directory) {
  if (process.env.BOARD_PORT) return process.env.BOARD_PORT
  try {
    const env = fs.readFileSync(
      path.join(directory, ".task-manager/manager/local/.env"), "utf8")
    for (const line of env.split("\n")) {
      const m = line.match(/^\s*BOARD_PORT\s*=\s*['"]?(\d+)/)
      if (m) return m[1]
    }
  } catch {}
  return "26071"
}

// opencode tool name → normalized kind (anything else: "command").
const KINDS = {
  read: "read", list: "read", glob: "search", grep: "search",
  edit: "edit", write: "edit", patch: "edit",
  bash: "command", webfetch: "web", todowrite: "plan", task: "subagent",
}

export const BenchBoard = async ({ directory }) => {
  const port = boardPort(directory)
  const seen = new Set()

  const post = async (session, body) => {
    try {
      await fetch(`http://127.0.0.1:${port}/api/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          v: 1,
          session: session || "unknown",
          agent: process.env.BOARD_AGENT_ID,
          task: process.env.BOARD_TASK,
          ...body,
        }),
        signal: AbortSignal.timeout(1000),
      })
    } catch {}
  }

  // The bus has no single "session started" moment we can rely on across
  // versions, so announce a session the first time we see its id.
  const announce = (id) => {
    if (!id || seen.has(id)) return
    seen.add(id)
    post(id, { kind: "session", summary: "session started" })
  }

  return {
    event: async ({ event }) => {
      const id = event?.properties?.sessionID || event?.properties?.info?.id
      if (event?.type === "session.idle") {
        announce(id)
        post(id, { kind: "idle", summary: "finished responding — idle" })
      } else if (event?.type === "session.deleted") {
        post(id, { kind: "end", summary: "session ended" })
      } else if (event?.type === "session.updated") {
        announce(id)
      }
    },
    "tool.execute.before": async (input) => {
      announce(input?.sessionID)
      post(input?.sessionID, {
        kind: "command", running: true,
        summary: `running: ${input?.tool || "tool"}`,
      })
    },
    "tool.execute.after": async (input, output) => {
      const tool = input?.tool || "tool"
      const kind = KINDS[tool] || "command"
      const title = typeof output?.title === "string" ? output.title : ""
      const body = {
        kind,
        summary: `${tool}${title ? `: ${title}` : ""}`.slice(0, 120),
      }
      if ((kind === "edit" || kind === "read") && title) body.file = title
      post(input?.sessionID, body)
    },
  }
}
