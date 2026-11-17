"""Board cards updated by key rather than rebuilt (task 130).

The page used to rebuild every card on every paint. This case measures,
in a real browser, what a paint costs at 100, 500 and 1000 cards — the
first draw, one changed card, an unrelated session event, a reordered
column, a moved card, a burst of readings and a click on a card's tools —
and keeps every raw timing in `keyed-cards-measurements.json` beside the
screenshots, printed into the log as well.

The boards are synthetic: the fixture's own cards, cloned in the page into
a state of N cards and painted through the page's own `render()`. So the
numbers are the page's rendering and the browser's layout and paint, not
the server's read (task 127 measures that).

Each sample records three times: `script` (the synchronous `render()`),
`layout` (the style and layout a read of `offsetHeight` forces straight
after it) and `visible` (from the start of the paint until the next frame
has run, which includes the browser's paint). They are measurements, not
assertions: the target and how it was met are reported in
`develop/perf/README.md`.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
SIZES = (100, 500, 1000)
REPS = 5

BENCH = r"""(() => {
window.__kbench = async function (n, reps) {
  const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const orig = window.__kbOrig || (window.__kbOrig = clone(S.state));
  const make = () => {
    const st = clone(orig);
    const tmpl = {};
    for (const s of st.board.stages) if (s.tasks[0]) tmpl[s.slug] = s.tasks[0];
    const any = Object.values(tmpl)[0];
    for (const s of st.board.stages) s.tasks = [];
    for (let i = 0; i < n; i++) {
      const s = st.board.stages[i % st.board.stages.length];
      const t = clone(tmpl[s.slug] || any);
      t.file = `${5000 + i}-bench-card-${i}.md`;
      t.number = String(5000 + i);
      t.title = `Bench card ${i} with a title of an ordinary length`;
      t.stage = s.slug;
      s.tasks.push(t);
    }
    return st;
  };
  const cards = () => [...document.querySelectorAll('#board article.card')];
  const mark = () => cards().forEach(c => { c.__kbMark = 1; });
  const kept = () => cards().filter(c => c.__kbMark).length;
  const time = async (fn) => {
    const t0 = performance.now(); fn(); const t1 = performance.now();
    void document.body.offsetHeight; const t2 = performance.now();
    await frame(); const t3 = performance.now();
    return { script: +(t1 - t0).toFixed(2), layout: +(t2 - t1).toFixed(2),
             visible: +(t3 - t0).toFixed(2) };
  };
  S.selected = null; S.cardMenu = null;
  if (S.view !== 'board') setView('board');
  const out = { n, initial: [], single: [], unrelatedEvent: [], sameReading: [],
                reorder: [], move: [], burst: [], toolsClick: [] };
  for (let r = 0; r < reps; r++) {
    document.querySelector('#board').innerHTML = '';
    const st = make();
    out.initial.push(await time(() => { S.state = st; render(); }));
  }
  out.cardsDrawn = cards().length;
  for (let r = 0; r < reps; r++) {
    const st = clone(S.state);
    const col = st.board.stages[r % st.board.stages.length];
    const t = col.tasks[Math.floor(col.tasks.length / 2)];
    t.title = t.title.replace(/( · changed \d+)?$/, ` · changed ${r}`);
    mark();
    const s = await time(() => { S.state = st; render(); });
    s.kept = kept(); s.total = cards().length;
    s.changedText = document.querySelector(`#board article.card[data-file="${t.file}"] .title`)
      ?.textContent || null;
    out.single.push(s);
  }
  for (let r = 0; r < reps; r++) {
    // a session event: the stream files it and asks for a paint; the
    // board's own reading has not changed
    (S.events['kbench-session'] = S.events['kbench-session'] || []).push(
      { kind: 'tool', ts: Date.now() / 1000, tool: 'Read', summary: `read ${r}` });
    mark();
    const s = await time(() => render());
    s.kept = kept(); s.total = cards().length;
    out.unrelatedEvent.push(s);
  }
  for (let r = 0; r < reps; r++) {
    const st = clone(S.state);
    mark();
    const s = await time(() => { S.state = st; render(); });
    s.kept = kept(); s.total = cards().length;
    out.sameReading.push(s);
  }
  for (let r = 0; r < reps; r++) {
    const st = clone(S.state);
    st.board.stages[0].tasks.reverse();
    mark();
    const s = await time(() => { S.state = st; render(); });
    s.kept = kept(); s.total = cards().length;
    s.order = [...document.querySelectorAll('#board .drop[data-stage="backlog"] article.card')]
      .slice(0, 3).map(c => c.dataset.file);
    s.want = st.board.stages[0].tasks.slice(0, 3).map(t => t.file);
    out.reorder.push(s);
  }
  for (let r = 0; r < reps; r++) {
    const st = clone(S.state);
    const from = st.board.stages[0], to = st.board.stages[1];
    const t = from.tasks.shift(); t.stage = to.slug; to.tasks.unshift(t);
    mark();
    const s = await time(() => { S.state = st; render(); });
    s.kept = kept(); s.total = cards().length;
    s.landed = document.querySelector(`#board .drop[data-stage="${to.slug}"] article.card`)
      ?.dataset.file === t.file;
    out.move.push(s);
  }
  for (let r = 0; r < reps; r++) {
    mark();
    const t0 = performance.now();
    for (let i = 0; i < 20; i++) {
      const st = clone(S.state);
      const col = st.board.stages[i % st.board.stages.length];
      const t = col.tasks[i % col.tasks.length];
      t.title = t.title.replace(/( · burst \d+)?$/, ` · burst ${i}`);
      S.state = st; scheduleRender();
      if (i % 5 === 4) await frame();
    }
    await frame();
    out.burst.push({ visible: +(performance.now() - t0).toFixed(2), readings: 20,
                     kept: kept(), total: cards().length });
  }
  for (let r = 0; r < reps; r++) {
    const card = cards()[Math.floor(cards().length / 2)];
    const more = card && card.querySelector('[data-more]');
    if (!more) break;
    const file = card.dataset.file;
    const t0 = performance.now(); more.click(); const t1 = performance.now();
    await frame(); const t2 = performance.now();
    const open = !!document.querySelector(
      `#board article.card[data-file="${file}"] .cardtools.open`);
    out.toolsClick.push({ script: +(t1 - t0).toFixed(2), visible: +(t2 - t0).toFixed(2), open });
    S.cardMenu = null; render(); await frame();
  }
  return out;
};
return true;
})()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


class KeyedCardsMeasured(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.measured = {"sizes": {}, "reps": REPS}

    @classmethod
    def tearDownClass(cls):
        out = S["shots"] / "keyed-cards-measurements.json"
        out.write_text(json.dumps(cls.measured, indent=1, sort_keys=True), encoding="utf-8")
        summary = {n: {k: median([s.get("visible") for s in v])
                       for k, v in m.items() if isinstance(v, list) and v}
                   for n, m in cls.measured["sizes"].items()}
        print("\nkeyed-cards measurements (median visible ms):", json.dumps(summary, sort_keys=True))
        # every raw sample, one short line per size and scenario, so the
        # whole record survives a log reader that clips long lines
        for n, m in cls.measured["sizes"].items():
            for k, v in sorted(m.items()):
                if not isinstance(v, list) or not v:
                    continue
                fields = [f"{f}=" + ",".join(str(s.get(f)) for s in v)
                          for f in ("visible", "script", "layout", "kept")
                          if any(f in s for s in v)]
                print(f"kc {n} {k} " + " ".join(fields))
        b = S["browser"]
        b.eval("(async () => { if (window.__kbOrig) { S.state = window.__kbOrig; } "
               "await loadState(); render(); return true; })()")

    def test_measure_100_500_1000(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('#board article.card').length > 0", 15),
                        "the board never drew a card")
        self.measured["userAgent"] = b.eval("navigator.userAgent")
        self.measured["viewport"] = [1440, 900]
        b.eval(BENCH)
        for n in SIZES:
            result = b.eval(f"window.__kbench({n}, {REPS})")
            self.assertEqual(result["cardsDrawn"], n, f"{n} cards were not all drawn")
            self.measured["sizes"][str(n)] = result
            if n == SIZES[-1]:
                path = S["shots"] / "keyed-cards-1000.png"
                b.screenshot(path)
                S["required"].append(path)
