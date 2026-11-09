#!/usr/bin/env node
// The Playwright half of verify/browser's remote path. benchverify.py
// starts this with an endpoint it has already validated as loopback, and
// speaks one JSON object per line to it on stdin/stdout.
//
//   node adapter.cjs --ws <ws://127.0.0.1:port/> --modules <node_modules>
//                    --expect-version <x.y.z>
//
// The browser is launched by the run-server at the far end. What it is
// launched with is what @playwright/test sends there for
// `connectOptions` + `launchOptions`: the x-playwright-launch-options
// header. The server honours chromiumSandbox: true only when it runs with
// --unsafe; without it the option is filtered and --no-sandbox added, which
// this side cannot see — so the sandbox is proved out of band (renderer
// user namespaces), never assumed from this file.
//
// exposeNetwork '<loopback>' routes the browser's requests for localhost
// and 127.0.0.1 back through this process, which is how a browser in a
// container reaches the disposable backend on the box's loopback.

'use strict';

const path = require('path');
const readline = require('readline');

const LAUNCH_OPTIONS = Object.freeze({ chromiumSandbox: true });
const EXPOSE_NETWORK = '<loopback>';

function arg(name) {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : undefined;
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function fatal(message) {
  emit({ event: 'fatal', error: message });
  process.exit(2);
}

async function main() {
  const ws = arg('--ws');
  const modules = arg('--modules');
  const expect = arg('--expect-version');
  if (!ws || !modules || !expect) fatal('usage: --ws URL --modules DIR --expect-version V');

  let pkg, pw;
  const where = path.join(path.resolve(modules), 'playwright-core');
  try {
    pkg = require(path.join(where, 'package.json'));
    pw = require(where);
  } catch (e) {
    fatal(`playwright-core is not installed at ${where} (${e.code || e.message}): ` +
          `run PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci in verify/playwright, or point ` +
          `BENCH_VERIFY_PLAYWRIGHT_MODULES at a node_modules made from its lockfile`);
  }
  if (pkg.version !== expect) {
    fatal(`playwright-core ${pkg.version} at ${where} is not the pinned ${expect}; ` +
          `client and run-server must be the same version`);
  }

  const browser = await pw.chromium.connect(ws, {
    headers: { 'x-playwright-launch-options': JSON.stringify(LAUNCH_OPTIONS) },
    exposeNetwork: EXPOSE_NETWORK,
    timeout: 30000,
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  let console = [];
  // the shape benchverify.Browser collects from Runtime.exceptionThrown
  page.on('pageerror', (e) => console.push({ exceptionDetails: { text: String(e && e.message || e) } }));
  // Accessibility.getFullAXTree has no Playwright API of its own; this is a
  // CDP session tunnelled over the Playwright connection, not a raw socket
  const cdp = await context.newCDPSession(page);
  await cdp.send('Accessibility.enable');

  emit({ event: 'ready', version: browser.version(), client: pkg.version,
         launchOptions: LAUNCH_OPTIONS, exposeNetwork: EXPOSE_NETWORK });

  const ops = {
    goto: (r) => page.goto(r.url, { waitUntil: 'load', timeout: 15000 }).then(() => null),
    eval: (r) => page.evaluate(r.expression),
    viewport: async (r) => {
      // undo a zoom first: the override would otherwise outlive it
      await cdp.send('Emulation.clearDeviceMetricsOverride');
      return page.setViewportSize({ width: r.width, height: r.height });
    },
    // Chromium's own page zoom on a width×height window: the CSS viewport
    // shrinks by the factor, so media queries see it, and the device
    // pixel ratio grows by it
    zoom: (r) => cdp.send('Emulation.setDeviceMetricsOverride', {
      width: Math.round(r.width / r.factor), height: Math.round(r.height / r.factor),
      deviceScaleFactor: r.factor, mobile: false }),
    key: (r) => page.keyboard.press(r.shift ? `Shift+${r.key}` : r.key),
    click: (r) => page.mouse.click(r.x, r.y),
    screenshot: async () => (await page.screenshot({ type: 'png' })).toString('base64'),
    ax: () => cdp.send('Accessibility.getFullAXTree'),
  };

  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    let req;
    try { req = JSON.parse(line); } catch { continue; }
    if (req.op === 'close') break;
    const op = ops[req.op];
    let reply;
    try {
      if (!op) throw new Error(`unknown op ${req.op}`);
      const result = await op(req);
      reply = { id: req.id, ok: true, result: result === undefined ? null : result };
    } catch (e) {
      // a page script that threw is the page's answer; anything else is the harness
      reply = { id: req.id, ok: false, kind: req.op === 'eval' ? 'page' : 'harness',
                error: String(e && e.message || e).slice(0, 2000) };
    }
    reply.console = console;
    console = [];
    emit(reply);
  }
  await browser.close().catch(() => {});
  process.exit(0);
}

main().catch((e) => fatal(String(e && e.stack || e).slice(0, 2000)));
