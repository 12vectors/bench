// The opencode adapter's event bridge, imported as the real ES module it
// is and driven under Node's built-in test runner (WP02's real-module
// example). No DOM, no npm, no network: `fetch` is replaced with a
// recorder, so the module's translation from opencode's events to the
// board's normalized schema is what is under test.
//
// Run by tests/test_node_modules.py, which keeps the suite one command.

import { test, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { BenchBoard } from "../../manager/core/adapters/opencode/plugin.js"

let posts
let realFetch
let saved
let dir

beforeEach(() => {
  posts = []
  realFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    posts.push({ url, body: JSON.parse(init.body), method: init.method })
    return { ok: true }
  }
  saved = { ...process.env }
  delete process.env.BOARD_PORT
  delete process.env.BOARD_AGENT_ID
  delete process.env.BOARD_TASK
  delete process.env.BOARD_RUN_TOKEN
  dir =fs.mkdtempSync(path.join(os.tmpdir(), "bench-opencode-"))
})

afterEach(() => {
  globalThis.fetch = realFetch
  for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k]
  Object.assign(process.env, saved)
  fs.rmSync(dir, { recursive: true, force: true })
})

test("posts to the port BOARD_PORT names, on loopback, with the agent forwarded", async () => {
  process.env.BOARD_PORT = "4567"
  process.env.BOARD_AGENT_ID = "wren"
  process.env.BOARD_TASK = "108-x.md"
  const hooks = await BenchBoard({ directory: dir })
  await hooks["tool.execute.before"]({ tool: "read", sessionID: "s1" })
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "s1" }, { title: "src/a.py" })
  assert.equal(posts.length, 3)
  for (const p of posts) {
    assert.equal(p.url, "http://127.0.0.1:4567/api/events")
    assert.equal(p.method, "POST")
    assert.equal(p.body.v, 1)
    assert.equal(p.body.session, "s1")
    assert.equal(p.body.agent, "wren")
    assert.equal(p.body.task, "108-x.md")
  }
  // the first sight of a session announces it; the finished tool call is
  // its own event and announces nothing
  assert.deepEqual([posts[0].body.kind, posts[0].body.summary], ["session", "session started"])
  assert.deepEqual([posts[1].body.kind, posts[1].body.running], ["command", true])
  assert.equal(posts[2].body.kind, "read")
  assert.equal(posts[2].body.file, "src/a.py")
  assert.equal(posts[2].body.summary, "read: src/a.py")
})

test("forwards the run's own token beside the agent it proves (WP05)", async () => {
  process.env.BOARD_PORT = "4567"
  process.env.BOARD_AGENT_ID = "wren"
  process.env.BOARD_RUN_TOKEN = "run-capability-xyz"
  const hooks = await BenchBoard({ directory: dir })
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "s1" }, { title: "a" })
  assert.ok(posts.length >= 1)
  for (const p of posts) assert.equal(p.body.token, "run-capability-xyz")
})

test("sends no token where there is no agent to prove, or no token to send", async () => {
  process.env.BOARD_PORT = "4567"
  process.env.BOARD_RUN_TOKEN = "run-capability-xyz"
  let hooks = await BenchBoard({ directory: dir })
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "s1" }, { title: "a" })
  for (const p of posts) assert.equal("token" in p.body, false)
  posts.length = 0
  delete process.env.BOARD_RUN_TOKEN
  process.env.BOARD_AGENT_ID = "wren"
  hooks = await BenchBoard({ directory: dir })
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "s2" }, { title: "a" })
  for (const p of posts) assert.equal("token" in p.body, false)
})

test("falls back to the install's local .env, then to the pinned port", async () => {
  const envDir = path.join(dir, ".task-manager", "manager", "local")
  fs.mkdirSync(envDir, { recursive: true })
  fs.writeFileSync(path.join(envDir, ".env"), "# settings\nBOARD_PORT='31337'\n")
  const fromEnv = await BenchBoard({ directory: dir })
  await fromEnv["tool.execute.before"]({ tool: "bash", sessionID: "s2" })
  assert.ok(posts.every((p) => p.url === "http://127.0.0.1:31337/api/events"))

  posts = []
  const bare = fs.mkdtempSync(path.join(os.tmpdir(), "bench-opencode-bare-"))
  try {
    const pinned = await BenchBoard({ directory: bare })
    await pinned["tool.execute.before"]({ tool: "bash", sessionID: "s3" })
  } finally {
    fs.rmSync(bare, { recursive: true, force: true })
  }
  assert.ok(posts.length > 0)
  assert.ok(posts.every((p) => p.url === "http://127.0.0.1:26071/api/events"))
})

test("maps tool names to normalized kinds, and anything else to command", async () => {
  process.env.BOARD_PORT = "1"
  const hooks = await BenchBoard({ directory: dir })
  const cases = [
    ["list", "read"], ["glob", "search"], ["grep", "search"], ["edit", "edit"],
    ["write", "edit"], ["patch", "edit"], ["bash", "command"], ["webfetch", "web"],
    ["todowrite", "plan"], ["task", "subagent"], ["mystery", "command"],
  ]
  for (const [tool, kind] of cases) {
    posts = []
    await hooks["tool.execute.after"]({ tool, sessionID: "s1" }, { title: "t" })
    const last = posts.at(-1).body
    assert.equal(last.kind, kind, tool)
    // only reads and edits carry the title as a file
    assert.equal("file" in last, kind === "read" || kind === "edit", tool)
  }
})

test("clips a long summary to 120 characters and tolerates a missing title", async () => {
  process.env.BOARD_PORT = "1"
  const hooks = await BenchBoard({ directory: dir })
  await hooks["tool.execute.after"]({ tool: "bash", sessionID: "s1" }, { title: "x".repeat(400) })
  assert.equal(posts.at(-1).body.summary.length, 120)
  await hooks["tool.execute.after"]({ sessionID: "s1" }, { title: 7 })
  assert.equal(posts.at(-1).body.summary, "tool")
  assert.equal(posts.at(-1).body.kind, "command")
})

test("announces a session once, then idles and ends it", async () => {
  process.env.BOARD_PORT = "1"
  const hooks = await BenchBoard({ directory: dir })
  await hooks.event({ event: { type: "session.updated", properties: { info: { id: "s9" } } } })
  await hooks.event({ event: { type: "session.updated", properties: { info: { id: "s9" } } } })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s9" } } })
  await hooks.event({ event: { type: "session.deleted", properties: { sessionID: "s9" } } })
  assert.deepEqual(posts.map((p) => p.body.kind), ["session", "idle", "end"])
  await hooks["tool.execute.before"]({ tool: "bash", sessionID: "s9" })
  assert.equal(posts.at(-1).body.running, true)
  assert.equal(posts.at(-1).body.summary, "running: bash")
})

test("a board that is not running never breaks the session", async () => {
  globalThis.fetch = async () => { throw new TypeError("fetch failed") }
  process.env.BOARD_PORT = "1"
  const hooks = await BenchBoard({ directory: dir })
  await assert.doesNotReject(hooks["tool.execute.after"]({ tool: "read", sessionID: "s1" }, {}))
  await assert.doesNotReject(hooks.event({ event: { type: "session.idle", properties: {} } }))
})
