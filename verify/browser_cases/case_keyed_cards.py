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

The synthetic boards are this case's own (task 176). The page keeps its
stream and its 30s safety net, so a live reading could land between two
awaited frames and replace a 1000-card board with the fixture's real one
— the benchmark then measured the wrong board, or threw `Cannot set
properties of undefined` moving a card out of a column that board did
not have. So every test here that paints a synthetic board holds the
page's readings for its duration (`holdReadings`, the console seam) and
releases them afterwards, failing or not; the release is verified by
reading the live board back. Only this case holds: every other case, and
the archive test below, runs with live updates as a person has them.
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
  // the board measured is this benchmark's, or the run says it was not
  // (task 176): a reading that replaced it would make every later sample
  // about another board
  let step = 'initial';
  const ours = () => {
    if (!S.state || S.state.__kbench !== n)
      throw new Error(`a live reading replaced the ${n}-card benchmark board during '${step}'` +
                      ` (readings held: ${JSON.stringify(window.readingsHeld ? readingsHeld() : null)})`);
  };
  for (let r = 0; r < reps; r++) {
    document.querySelector('#board').innerHTML = '';
    const st = make();
    st.__kbench = n;
    out.initial.push(await time(() => { S.state = st; render(); }));
    ours();
  }
  out.cardsDrawn = cards().length;
  for (let r = 0; r < reps; r++) {
    step = 'single'; ours();
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
    step = 'unrelatedEvent'; ours();
    (S.events['kbench-session'] = S.events['kbench-session'] || []).push(
      { kind: 'tool', ts: Date.now() / 1000, tool: 'Read', summary: `read ${r}` });
    mark();
    const s = await time(() => render());
    s.kept = kept(); s.total = cards().length;
    out.unrelatedEvent.push(s);
  }
  for (let r = 0; r < reps; r++) {
    step = 'sameReading'; ours();
    const st = clone(S.state);
    mark();
    const s = await time(() => { S.state = st; render(); });
    s.kept = kept(); s.total = cards().length;
    out.sameReading.push(s);
  }
  for (let r = 0; r < reps; r++) {
    step = 'reorder'; ours();
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
    step = 'move'; ours();
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
    step = 'burst'; ours();
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
    step = 'toolsClick'; ours();
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
  step = 'end'; ours();
  return out;
};
return true;
})()"""


KC = r"""(() => {
const clone = (o) => JSON.parse(JSON.stringify(o));
window.__kc = {
  clone,
  frame: () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0))),
  orig: window.__kc && window.__kc.orig || clone(S.state),
  // a board of n cards (the fixture's own, cloned), and optionally a phase
  // card in to-do/ listing the first two backlog cards
  board(n, phase) {
    const st = clone(this.orig);
    const tmpl = {};
    for (const s of st.board.stages) if (s.tasks[0]) tmpl[s.slug] = s.tasks[0];
    const any = Object.values(tmpl)[0];
    for (const s of st.board.stages) s.tasks = [];
    for (let i = 0; i < n; i++) {
      const s = st.board.stages[i % st.board.stages.length];
      const t = clone(tmpl[s.slug] || any);
      Object.assign(t, { file: `${7000 + i}-kc-card-${i}.md`, number: String(7000 + i),
                         title: `Keyed card ${i}`, stage: s.slug, phase: null,
                         isPhase: false, members: [], assignee: null });
      s.tasks.push(t);
    }
    if (phase) {
      const todo = st.board.stages.find(s => s.slug === 'to-do');
      const back = st.board.stages.find(s => s.slug === 'backlog');
      const p = clone(todo.tasks[0] || any);
      const mem = back.tasks.slice(0, 2);
      Object.assign(p, { file: '7999-kc-phase.md', number: '7999', title: 'Keyed phase',
                         stage: 'to-do', isPhase: true, phase: null,
                         members: mem.map(m => ({ file: m.file, number: m.number, title: m.title })) });
      mem.forEach((m, i) => { m.phase = { file: p.file, number: p.number, title: p.title,
                                           index: i + 1, total: mem.length }; });
      todo.tasks.unshift(p);
    }
    return st;
  },
  paint(st) { if (st) S.state = st; render(); },
  cards(scope = '#board') { return [...document.querySelectorAll(scope + ' article.card')]; },
  card(file, scope = '#board') {
    return document.querySelector(`${scope} article.card[data-file="${file}"]`);
  },
  mark(scope = '#board') { this.cards(scope).forEach(c => { c.__kcMark = 1; }); },
  kept(scope = '#board') { return this.cards(scope).filter(c => c.__kcMark).length; },
  dupes(scope = '#board') {
    const f = this.cards(scope).map(c => c.dataset.file);
    return f.length - new Set(f).size;
  },
  // every column's count against the cards it draws, and its note
  columns() {
    return [...document.querySelectorAll('#board .kcol')].map(c => ({
      stage: c.querySelector('.drop').dataset.stage,
      count: +c.querySelector('h2 .count').textContent,
      drawn: c.querySelectorAll('.drop article.card').length,
      note: c.querySelector('h2 .note').textContent,
      empty: (c.querySelector('.drop > .empty') || {}).textContent || null }));
  },
  order(stage) {
    return [...document.querySelectorAll(`#board .drop[data-stage="${stage}"] article.card`)]
      .map(c => c.dataset.file);
  },
  stateOrder(stage) {
    return S.state.board.stages.find(s => s.slug === stage).tasks.map(t => t.file);
  },
  retitle(file, title) {
    const st = clone(S.state);
    for (const s of st.board.stages) for (const t of s.tasks) if (t.file === file) t.title = title;
    return st;
  },
  active() {
    const a = document.activeElement;
    const card = a && a.closest && a.closest('article.card');
    return { file: card ? card.dataset.file : null, isCard: a === card,
             more: !!(a && a.dataset && a.dataset.more), mark: !!(a && a.__kcMark),
             tag: a ? a.tagName : null };
  },
  session() {
    (S.events['kc-session'] = S.events['kc-session'] || []).push(
      { kind: 'tool', ts: Date.now() / 1000, tool: 'Read', summary: 'read' });
    render();
  },
};
S.selected = null; S.cardMenu = null;
if (S.view !== 'board') setView('board');
return true;
})()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


SYNTHETIC = "(f) => /^(5\\d{3}-bench-card|7\\d{3}-kc-)/.test(f)"


def hold(b, why: str) -> None:
    """The page's readings held for a synthetic board (task 176)."""
    held = b.eval(f"(() => {{ holdReadings({json.dumps(why)}); return readingsHeld(); }})()")
    if not held or held.get("held") != why:
        raise bv.SetupError(f"the page did not hold its readings for {why}: {held!r}")


def release(b) -> dict:
    """Release the hold and prove the live board is back: no hold left,
    and a reading of the board painted with none of the synthetic cards.
    Called whether or not the test passed; raises when restoration did
    not happen, so a benchmark can never leave its board behind."""
    out = b.eval(f"""(async () => {{
      const was = readingsHeld();
      if (window.__kbOrig) S.state = window.__kbOrig;
      if (window.__kc) S.state = __kc.orig;
      const r = await releaseReadings();
      await loadState();
      const files = [...document.querySelectorAll('article.card')].map(c => c.dataset.file);
      const synthetic = ({SYNTHETIC});
      return {{ was, released: r, now: readingsHeld(),
                stateSynthetic: S.state.board.stages.flatMap(s => s.tasks).filter(t => synthetic(t.file)).length,
                drawnSynthetic: files.filter(synthetic).length, drawn: files.length }};
    }})()""")
    if not out or out["now"]["held"] is not None:
        raise bv.SetupError(f"the page's readings were not released: {out!r}")
    if out["stateSynthetic"] or out["drawnSynthetic"]:
        raise bv.SetupError(f"the live board was not restored after the benchmark: {out!r}")
    return out


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

    def test_0_a_live_reading_cannot_replace_the_benchmark_board(self):
        """The race reproduced on purpose, both ways: a live reading of the
        board landing while a synthetic board is painted replaces it —
        what broke the benchmark — and, held, it does not."""
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('#board article.card').length > 0", 15),
                        "the board never drew a card")
        b.eval(BENCH)
        probe = f"""(async (held) => {{
          const synthetic = ({SYNTHETIC});
          if (held) holdReadings('keyed-cards reproduction');
          window.__kbOrig = window.__kbOrig || JSON.parse(JSON.stringify(S.state));
          const st = JSON.parse(JSON.stringify(window.__kbOrig));
          st.board.stages.forEach((s, i) => {{ s.tasks = s.tasks.slice(0, 1).map(t =>
            ({{ ...t, file: `${{5000 + i}}-bench-card-${{i}}.md`, number: String(5000 + i), stage: s.slug }})); }});
          S.state = st; render();
          await loadState();          // the stream's, the safety net's, the focus's reading
          const kept = S.state === st;
          const drawn = [...document.querySelectorAll('#board article.card')].map(c => c.dataset.file);
          return {{ kept, drawnSynthetic: drawn.filter(synthetic).length, held: readingsHeld() }};
        }})"""
        try:
            unheld = b.eval(f"{probe}(false)")
            self.assertFalse(unheld["kept"], "reproduction: without a hold a live reading "
                             "replaces a synthetic board")
            self.assertEqual(0, unheld["drawnSynthetic"])
            held = b.eval(f"{probe}(true)")
            self.assertTrue(held["kept"], "held, a live reading replaced the benchmark board")
            self.assertGreater(held["drawnSynthetic"], 0)
            self.assertTrue(held["held"]["deferred"], "the ask made while held was not noted")
        finally:
            release(b)

    def test_measure_100_500_1000(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('#board article.card').length > 0", 15),
                        "the board never drew a card")
        self.measured["userAgent"] = b.eval("navigator.userAgent")
        self.measured["viewport"] = [1440, 900]
        b.eval(BENCH)
        hold(b, "keyed-cards benchmark")
        try:
            for n in SIZES:
                result = b.eval(f"window.__kbench({n}, {REPS})")
                self.assertEqual(result["cardsDrawn"], n, f"{n} cards were not all drawn")
                self.measured["sizes"][str(n)] = result
                if n == SIZES[-1]:
                    path = S["shots"] / "keyed-cards-1000.png"
                    b.screenshot(path)
                    S["required"].append(path)
        finally:
            self.measured["restored"] = release(b)
        # the measured target, as an observation and not a gate: a single
        # changed card at 1000 cards, visible within 50 ms
        single = median([s["visible"] for s in self.measured["sizes"]["1000"]["single"]])
        self.measured["target50msSingleAt1000"] = {"medianVisibleMs": single,
                                                   "met": single is not None and single < 50}


class KeyedCards(unittest.TestCase):
    """What keying the paint must keep, and what it must still change —
    in Board and Phases, against the page's own render()."""

    def setUp(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('#board article.card').length > 0", 15),
                        "the board never drew a card")
        b.eval(KC)
        self.b = b
        # synthetic boards are painted across awaited frames and separate
        # evals; the one test that works the live backend is not held
        self.held = not self._testMethodName.endswith("_through_the_backend")
        if self.held:
            hold(b, f"keyed-cards {self._testMethodName}")

    def tearDown(self):
        try:
            self.b.eval("(async () => { S.selected = null; S.cardMenu = null; S.acts = S.acts || {}; "
                        "if (S.view !== 'board') setView('board'); "
                        "if (window.__kc) S.state = __kc.orig; render(); return true; })()")
        finally:
            if self.held:
                release(self.b)

    def ev(self, js):
        return self.b.eval(js)

    def test_1_a_full_first_paint_draws_every_card_counted_and_named(self):
        r = self.ev("""(() => { document.querySelector('#board').innerHTML = '';
          __kc.paint(__kc.board(40)); const cards = __kc.cards();
          return { n: cards.length, dupes: __kc.dupes(), columns: __kc.columns(),
                   names: cards.map(c => [c.dataset.file, c.tabIndex,
                     (c.querySelector('[data-more]') || {}).getAttribute
                       ? c.querySelector('[data-more]').getAttribute('aria-label') : null]) }; })()""")
        self.assertEqual(r["n"], 40)
        self.assertEqual(r["dupes"], 0)
        for col in r["columns"]:
            self.assertEqual(col["count"], col["drawn"], col)
        for file, tab, label in r["names"]:
            self.assertEqual(tab, 0, f"{file} is not reachable by Tab")
            if label is not None:
                self.assertEqual(label, f"Tools for #{file.split('-')[0]}")

    def test_2_an_unrelated_event_recreates_no_card_and_a_changed_card_is_redrawn(self):
        r = self.ev("""(() => { __kc.paint(__kc.board(60)); __kc.mark();
          __kc.session(); const afterEvent = __kc.kept();
          __kc.paint(__kc.clone(S.state)); const afterSame = __kc.kept();
          const file = __kc.cards()[17].dataset.file;
          __kc.paint(__kc.retitle(file, 'A title that changed'));
          return { total: __kc.cards().length, afterEvent, afterSame, afterOne: __kc.kept(),
                   changedKept: !!__kc.card(file).__kcMark,
                   title: __kc.card(file).querySelector('.title').textContent,
                   dupes: __kc.dupes(), columns: __kc.columns() }; })()""")
        self.assertEqual(r["afterEvent"], r["total"], "a session event recreated card nodes")
        self.assertEqual(r["afterSame"], r["total"], "an identical reading recreated card nodes")
        self.assertEqual(r["afterOne"], r["total"] - 1, "one changed card, one new node")
        self.assertFalse(r["changedKept"])
        self.assertEqual(r["title"], "A title that changed")
        self.assertEqual(r["dupes"], 0)

    def test_3_order_moves_removal_and_return_update_the_columns(self):
        r = self.ev("""(() => { __kc.paint(__kc.board(30)); __kc.mark();
          const out = {};
          let st = __kc.clone(S.state); st.board.stages[0].tasks.reverse(); __kc.paint(st);
          out.reversed = { got: __kc.order('backlog'), want: __kc.stateOrder('backlog'),
                           kept: __kc.kept(), total: __kc.cards().length };
          st = __kc.clone(S.state);
          const t = st.board.stages[0].tasks.shift(); t.stage = 'to-do';
          st.board.stages[1].tasks.unshift(t); __kc.paint(st);
          out.moved = { first: __kc.order('to-do')[0], file: t.file,
                        inBacklog: __kc.order('backlog').includes(t.file),
                        columns: __kc.columns(), dupes: __kc.dupes() };
          st = __kc.clone(S.state);
          const gone = st.board.stages[2].tasks.splice(1, 1)[0]; __kc.paint(st);
          out.removed = { present: !!__kc.card(gone.file), columns: __kc.columns() };
          st = __kc.clone(S.state); st.board.stages[2].tasks.splice(1, 0, gone); __kc.paint(st);
          out.back = { at: __kc.order(gone.stage).indexOf(gone.file),
                       want: __kc.stateOrder(gone.stage).indexOf(gone.file),
                       columns: __kc.columns(), dupes: __kc.dupes() };
          st = __kc.clone(S.state); st.board.stages[4].tasks = []; __kc.paint(st);
          out.emptied = __kc.columns().find(c => c.stage === 'done');
          return out; })()""")
        self.assertEqual(r["reversed"]["got"], r["reversed"]["want"])
        self.assertEqual(r["reversed"]["kept"], r["reversed"]["total"],
                         "reordering a column recreated cards it only had to move")
        self.assertEqual(r["moved"]["first"], r["moved"]["file"])
        self.assertFalse(r["moved"]["inBacklog"])
        self.assertEqual(r["moved"]["dupes"], 0)
        self.assertFalse(r["removed"]["present"])
        self.assertEqual(r["back"]["at"], r["back"]["want"])
        self.assertEqual(r["back"]["dupes"], 0)
        for key in ("moved", "removed", "back"):
            for col in r[key]["columns"]:
                self.assertEqual(col["count"], col["drawn"], (key, col))
        self.assertEqual(r["emptied"]["drawn"], 0)
        self.assertEqual(r["emptied"]["empty"], "Nothing here. Good.")

    def test_4_focus_and_keyboard_survive_an_update(self):
        r = self.ev("""(() => { __kc.paint(__kc.board(30)); const out = {};
          const card = __kc.cards()[7]; const file = card.dataset.file; card.focus();
          __kc.session(); __kc.paint(__kc.clone(S.state));
          out.unrelated = { same: document.activeElement === card, ...__kc.active() };
          __kc.paint(__kc.retitle(file, 'Retitled while focused'));
          out.changed = __kc.active();
          const more = __kc.card(file).querySelector('[data-more]'); more.focus();
          __kc.paint(__kc.retitle(file, 'Retitled again'));
          out.moreKept = __kc.active();
          const st = __kc.clone(S.state);
          const col = st.board.stages.find(s => s.tasks.some(t => t.file === file));
          col.tasks.reverse(); __kc.paint(st);
          out.reordered = __kc.active();
          out.file = file; return out; })()""")
        self.assertTrue(r["unrelated"]["same"], "an unrelated paint moved focus")
        self.assertEqual(r["changed"]["file"], r["file"])
        self.assertTrue(r["changed"]["isCard"], "focus did not follow the redrawn card")
        self.assertEqual(r["moreKept"]["file"], r["file"])
        self.assertTrue(r["moreKept"]["more"], "focus did not follow to the redrawn ⋯")
        self.assertEqual(r["reordered"]["file"], r["file"])
        self.assertTrue(r["reordered"]["more"], "a reorder dropped focus")
        # the keyboard still walks the board from there
        self.b.key("Tab")
        after = self.ev("__kc.active()")
        self.assertIsNotNone(after["tag"])
        self.assertNotEqual(after["tag"], "BODY", "Tab from a kept card went nowhere")

    def test_5_open_tools_and_armed_and_busy_controls_stay(self):
        r = self.ev("""(async () => { __kc.paint(__kc.board(30)); const out = {};
          const card = __kc.cards().find(c => c.querySelector('[data-more]'));
          const file = card.dataset.file;
          card.querySelector('[data-more]').click(); await __kc.frame();
          __kc.mark();
          for (let i = 0; i < 10; i++) { __kc.session(); __kc.paint(__kc.retitle(
            __kc.cards()[0].dataset.file === file ? __kc.cards()[1].dataset.file
                                                  : __kc.cards()[0].dataset.file, 'other ' + i)); }
          out.open = !!__kc.card(file).querySelector('.cardtools.open');
          out.keptOpen = !!__kc.card(file).__kcMark;
          // one handler, not one per paint: one click closes it
          __kc.card(file).querySelector('[data-more]').click(); await __kc.frame();
          out.closed = S.cardMenu === null && !__kc.card(file).querySelector('.cardtools.open');
          const done = __kc.cards().find(c => c.closest('.drop').dataset.stage === 'done'
                                           && c.querySelector('[data-arch]'));
          const dfile = done.dataset.file;
          done.querySelector('[data-arch]').click();
          __kc.session(); __kc.paint(__kc.clone(S.state));
          out.armed = __kc.card(dfile).querySelector('[data-arch]').classList.contains('armed');
          __kc.mark(); __kc.session();
          out.armedKept = !!__kc.card(dfile).__kcMark;
          delete S.acts[`${dfile}::archive`]; __kc.paint();
          out.disarmed = !__kc.card(dfile).querySelector('[data-arch]').classList.contains('armed');
          S.acts[`${dfile}::archive`] = { phase: 'busy', until: Date.now() + 60000 }; __kc.paint();
          const busy = __kc.card(dfile).querySelector('[data-arch]');
          out.busy = busy.disabled && busy.classList.contains('busy');
          delete S.acts[`${dfile}::archive`]; __kc.paint();
          out.free = !__kc.card(dfile).querySelector('[data-arch]').disabled;
          return out; })()""")
        self.assertTrue(r["open"], "the open tools closed under a paint")
        self.assertTrue(r["keptOpen"], "the card with open tools was recreated")
        self.assertTrue(r["closed"], "one click did not close the tools: duplicated handlers?")
        self.assertTrue(r["armed"], "an armed control lost its window")
        self.assertTrue(r["armedKept"], "an armed card was recreated by an unrelated event")
        self.assertTrue(r["disarmed"])
        self.assertTrue(r["busy"], "a busy control was not drawn busy")
        self.assertTrue(r["free"], "a settled control stayed disabled")

    def test_6_a_drag_in_flight_survives_a_paint(self):
        r = self.ev("""(() => { __kc.paint(__kc.board(30));
          const card = __kc.cards().find(c => c.closest('.drop').dataset.stage === 'backlog');
          const file = card.dataset.file; const other = __kc.cards().find(c => c !== card).dataset.file;
          const dt = new DataTransfer();
          card.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: dt }));
          const dragging = S.dragging && S.dragging.file;
          __kc.session(); __kc.paint(__kc.retitle(other, 'changed during a drag'));
          const out = { dragging, file, connected: card.isConnected,
                        same: __kc.card(file) === card,
                        cls: card.classList.contains('dragging'),
                        payload: dt.getData('application/json') };
          card.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt }));
          out.ended = !card.classList.contains('dragging') && S.dragging === null;
          return out; })()""")
        self.assertEqual(r["dragging"], r["file"])
        self.assertTrue(r["connected"] and r["same"], "the dragged card was replaced mid-drag")
        self.assertTrue(r["cls"])
        self.assertIn(r["file"], r["payload"])
        self.assertTrue(r["ended"])

    def test_7_a_burst_of_readings_lands_the_last_without_duplicates(self):
        r = self.ev("""(async () => { __kc.paint(__kc.board(50)); let st;
          for (let i = 0; i < 30; i++) {
            st = __kc.clone(S.state);
            const all = st.board.stages.flatMap(s => s.tasks);
            all[i % all.length].title = `burst ${i}`;
            if (i % 7 === 3) { const s0 = st.board.stages[i % 5]; s0.tasks.reverse(); }
            S.state = st; scheduleRender();
            if (i % 4 === 0) await __kc.frame();
          }
          await __kc.frame();
          const want = S.state.board.stages.map(s => s.tasks.filter(t => !t.phase).map(t => [t.file, t.title]));
          const got = [...document.querySelectorAll('#board .drop')].map(d =>
            [...d.querySelectorAll('article.card')].map(c => [c.dataset.file,
              c.querySelector('.title').textContent]));
          // clicking a card once opens it once
          const c = __kc.cards()[3]; c.click(); await __kc.frame();
          const opened = S.selected && S.selected.file === c.dataset.file;
          S.selected = null; render();
          return { want, got, dupes: __kc.dupes(), opened }; })()""")
        self.assertEqual(r["got"], r["want"], "the board does not show the last reading")
        self.assertEqual(r["dupes"], 0)
        self.assertTrue(r["opened"])

    def test_8_phase_members_in_board_and_phases(self):
        r = self.ev("""(async () => { __kc.paint(__kc.board(20, true)); const out = {};
          const phase = S.state.board.stages.find(s => s.slug === 'to-do').tasks[0];
          const members = phase.members.map(m => m.file);
          out.boardHides = members.map(f => !!__kc.card(f));
          out.backlog = __kc.columns().find(c => c.stage === 'backlog');
          out.phaseOnBoard = !!__kc.card(phase.file);
          setView('phases'); await __kc.frame();
          const lane = () => document.querySelector(`#lanes .lane[data-phase="${phase.file}"]`);
          const laneEl = lane();
          out.lane = !!laneEl;
          out.inLane = members.map(f => !!__kc.card(f, '#lanes'));
          __kc.mark('#lanes');
          __kc.session(); __kc.paint(__kc.clone(S.state));
          out.laneKept = lane() === laneEl;
          out.laneCardsKept = __kc.kept('#lanes');
          const st = __kc.clone(S.state);
          const back = st.board.stages.find(s => s.slug === 'backlog');
          const t = back.tasks.find(x => x.file === members[0]);
          back.tasks.splice(back.tasks.indexOf(t), 1); t.stage = 'review';
          st.board.stages.find(s => s.slug === 'review').tasks.unshift(t);
          __kc.paint(st);
          const col = (slug) => [...lane().querySelectorAll('.lcol')][
            ['backlog', 'to-do', 'in-progress', 'review', 'merged'].indexOf(slug)];
          out.movedTo = !!col('review').querySelector(`article.card[data-file="${members[0]}"]`);
          out.leftFrom = !!col('backlog').querySelector(`article.card[data-file="${members[0]}"]`);
          out.laneCounts = [...lane().querySelectorAll('.lcol')].map(c =>
            [+c.querySelector('h3 .count').textContent, c.querySelectorAll('article.card').length]);
          out.laneDupes = __kc.dupes('#lanes');
          setView('board'); await __kc.frame();
          out.boardStillHides = !__kc.card(members[0]);
          return out; })()""")
        self.assertEqual(r["boardHides"], [False, False], "phase members drawn on the Board")
        self.assertIn("+2 in phases", r["backlog"]["note"])
        self.assertEqual(r["backlog"]["count"], r["backlog"]["drawn"])
        self.assertTrue(r["phaseOnBoard"])
        self.assertTrue(r["lane"])
        self.assertEqual(r["inLane"], [True, True])
        self.assertTrue(r["laneKept"], "an unrelated paint rebuilt the lane")
        self.assertEqual(r["laneCardsKept"], 2, "an unrelated paint rebuilt a lane's cards")
        self.assertTrue(r["movedTo"])
        self.assertFalse(r["leftFrom"])
        for count, drawn in r["laneCounts"]:
            self.assertEqual(count, drawn)
        self.assertEqual(r["laneDupes"], 0)
        self.assertTrue(r["boardStillHides"])

    def test_9_theme_and_width_change_keep_the_cards(self):
        b = self.b
        r = self.ev("""(() => { __kc.paint(__kc.board(40)); __kc.mark();
          S.dark = !S.dark; applyTheme(); render();
          return { kept: __kc.kept(), total: __kc.cards().length,
                   theme: document.documentElement.getAttribute('data-theme') }; })()""")
        self.assertEqual(r["kept"], r["total"], "a theme switch rebuilt the cards")
        b.viewport(760, 900)
        r2 = self.ev("""(async () => { await __kc.frame(); __kc.session();
          return { kept: __kc.kept(), total: __kc.cards().length,
                   wide: document.documentElement.scrollWidth - innerWidth,
                   columns: __kc.columns() }; })()""")
        path = S["shots"] / "keyed-cards-760-after-theme.png"
        b.screenshot(path)
        S["required"].append(path)
        self.ev("(() => { S.dark = !S.dark; applyTheme(); render(); return true; })()")
        self.assertEqual(r2["kept"], r2["total"], "a narrower window rebuilt the cards")
        self.assertLessEqual(r2["wide"], 0, "the document overflows sideways at 760px")
        for col in r2["columns"]:
            self.assertEqual(col["count"], col["drawn"])

    def test_a_drafted_comment_survives_board_updates(self):
        b = self.b
        files = b.eval("__kc.orig.board.stages.flatMap(s => s.tasks).map(t => t.file)")
        target = next(f for f in files if f.startswith("01-"))
        other = next(f for f in files if f.startswith("02-"))
        b.eval(f"document.querySelector('#board article.card[data-file=\"{target}\"]').click()")
        self.assertTrue(b.wait("!!document.querySelector('#commentbox')", 10),
                        "the detail view never showed its comment box")
        r = self.ev(f"""(() => {{ const box = document.querySelector('#commentbox');
          box.focus(); box.value = 'half a thought'; box.dispatchEvent(new Event('input', {{ bubbles: true }}));
          __kc.mark();
          for (let i = 0; i < 5; i++) {{ __kc.session(); __kc.paint(__kc.retitle('{other}', 'other ' + i)); }}
          const now = document.querySelector('#commentbox');
          return {{ value: now && now.value, focused: document.activeElement === now,
                    kept: __kc.kept(), total: __kc.cards().length }}; }})()""")
        self.assertEqual(r["value"], "half a thought", "an unsent comment was lost")
        self.assertEqual(r["kept"], r["total"] - 1, "only the retitled card is a new node")

    def test_archive_and_undo_through_the_backend(self):
        b = self.b
        tasks = S["backend"].work / "tasks"
        name = "97-keyed-archive-probe.md"
        path = tasks / "done" / name
        path.write_text("# Keyed archive probe\n\n**Status:** Done\n**Priority:** Low\n\n"
                        "A disposable card.\n", encoding="utf-8")
        try:
            sel = f'#board article.card[data-file="{name}"]'
            self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                            "the probe card never reached the board")
            arch = f"document.querySelector('{sel} [data-arch]')"
            b.eval(f"{arch}.click()")
            b.eval(f"{arch}.click()")
            self.assertTrue(b.wait(f"!document.querySelector('{sel}')", 15), "archive did not remove it")
            # two board readings racing the confirmation: nothing comes back
            b.eval("(async () => { await Promise.all([loadState(), loadState()]); return true; })()")
            self.assertFalse(b.eval(f"!!document.querySelector('{sel}')"),
                             "an older reading put the archived card back")
            self.assertEqual(0, b.eval("__kc.dupes()"))
            b.eval("document.dispatchEvent(new KeyboardEvent('keydown', "
                   "{ key: 'z', ctrlKey: true, bubbles: true }))")
            self.assertTrue(b.wait(f"document.querySelectorAll('{sel}').length === 1", 15),
                            "⌘Z did not bring the card back exactly once")
            b.eval("(async () => { await loadState(); return true; })()")
            self.assertEqual(0, b.eval("__kc.dupes()"))
            cols = b.eval("__kc.columns()")
            for col in cols:
                self.assertEqual(col["count"], col["drawn"], col)
        finally:
            path.unlink(missing_ok=True)
            (tasks / "archive" / name).unlink(missing_ok=True)
