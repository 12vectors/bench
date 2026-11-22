"""Architecture governance (task 107, WP01 of the maintainability plan).

What this holds, all of it read mechanically from the source with the
standard library's `ast` and `re` — nothing here imports a core module:

1. Top-level imports between core modules respect the *current* layer
   table in `manager/core/ARCHITECTURE.md`. That block is the only source
   of the layers; this file never restates them.
2. Function-level (deferred) core imports are on a reviewed allowlist that
   may only shrink.
3. Cross-module reaches of a private name (`module._name`) are on a
   reviewed allowlist that may only shrink.
4. Every HTTP route and the door it passes through match a snapshot.
5. Standalone executables (the agent adapters and the two run shims)
   import no core module, and composition modules are imported only by
   composition.
6. GitHub vocabulary in core (outside `providers/`) is on a per-module count
   that may only shrink.
9. Every attribute a test assigns, `setattr`s or patches on a core module
   still exists in that module, so a patch cannot silently go inert.
10. Stdlib leaves (`gitproc`, task 109; `records`, task 110; `redact`,
   task 111) import no core module and nothing
   outside the standard library, anywhere in the file.

Checks 7 and 8 of the plan (frontend import direction, offline assets)
and the infrastructure/provider purity checks need modules and assets that
do not exist yet. They are deferred to the packages that build them,
rather than written now to iterate over nothing (ARCHITECTURE.md lists
them).

**Convention.** `A → B` means A imports B, so B is the lower layer.

**Identity.** An exception is identified by file, enclosing function and
what it reaches — never by line number — so inserting lines above it does
not change it. Each baseline entry also carries the line it was reviewed
at, for navigation only; failures print the current line.

**Baselines are reviewed, not regenerated.** The dictionaries below are
the WP01 inventory as reviewed. The test fails on anything *new* (fix the
code, or make the exception a reviewed edit of this file *and* of the
counts block in ARCHITECTURE.md) and on anything *gone* (delete the
entry and lower the count: the ratchet tightens). Nothing in this file
writes a baseline. `python3 tests/test_architecture.py` prints the actual
inventory to stdout for a reviewer to compare; it writes no file.
"""

from __future__ import annotations

import ast
import functools
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
TESTS = REPO / "tests"
DOC = "ARCHITECTURE.md"


# ── the document: the only source of the layers ────────────────────────

FENCE_OPEN = re.compile(r"^```architecture (current|proposed|counts)\s*$")
LAYER_LINE = re.compile(r"^L(\d+)\s+(.+?)\s*$")
ROLE_LINE = re.compile(r"^(composition|retired|new)\s*:\s*(.*?)\s*$")
COUNT_LINE = re.compile(r"^([a-z-]+)\s+(\d+)\s*$")


def read_blocks(text: str) -> dict:
    """The machine-readable blocks of ARCHITECTURE.md.

    `current` and `proposed` are `{"layers": [[module, ...], ...],
    "composition": set, "retired": set, "new": set}`; `counts` is
    `{name: int}`. Anything in a block that is not one of those line
    shapes is an error, so a typo cannot silently drop a module."""
    blocks: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        opened = FENCE_OPEN.match(lines[i])
        i += 1
        if not opened:
            continue
        kind = opened.group(1)
        if kind in blocks:
            raise ValueError(f"two `architecture {kind}` blocks")
        body = []
        while i < len(lines) and lines[i].strip() != "```":
            body.append(lines[i])
            i += 1
        if i == len(lines):
            raise ValueError(f"`architecture {kind}` block never closes")
        i += 1
        blocks[kind] = _parse_block(kind, body)
    for kind in ("current", "proposed", "counts"):
        if kind not in blocks:
            raise ValueError(f"no `architecture {kind}` block")
    return blocks


def _parse_block(kind: str, body: list[str]):
    if kind == "counts":
        counts = {}
        for raw in body:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = COUNT_LINE.match(line)
            if not match:
                raise ValueError(f"counts: cannot read {raw!r}")
            counts[match.group(1)] = int(match.group(2))
        return counts
    out = {"layers": [], "composition": set(), "retired": set(), "new": set()}
    seen: set[str] = set()
    for raw in body:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        layer = LAYER_LINE.match(line)
        role = ROLE_LINE.match(line)
        if layer:
            if int(layer.group(1)) != len(out["layers"]):
                raise ValueError(f"{kind}: layers must count up from L0 "
                                 f"without gaps ({raw!r})")
            names = layer.group(2).split()
            for name in names:
                if name in seen:
                    raise ValueError(f"{kind}: {name} is placed twice")
                seen.add(name)
            out["layers"].append(names)
        elif role:
            out[role.group(1)] |= set(role.group(2).split())
        else:
            raise ValueError(f"{kind}: cannot read {raw!r}")
    return out


def layer_of(block: dict) -> dict[str, int]:
    return {name: n for n, names in enumerate(block["layers"]) for name in names}


# ── scanning ───────────────────────────────────────────────────────────


def core_modules(core: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(core.glob("*.py"))}


def _parse(path: Path) -> ast.Module:
    return _parse_text(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _parse_text(text: str) -> ast.Module:
    # keyed by content, so a scratch copy re-parses only what it changed;
    # nothing here mutates a tree
    return ast.parse(text)


class _Scope(ast.NodeVisitor):
    """Walks a module keeping the qualified name of the enclosing
    function or class, and whether the code runs at import time."""

    def __init__(self):
        self.stack: list[str] = []
        self.deferred = 0

    @property
    def qualname(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _enter(self, node, deferred: bool):
        self.stack.append(node.name)
        self.deferred += deferred
        self.generic_visit(node)
        self.deferred -= deferred
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._enter(node, True)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._enter(node, False)


def _private(attr: str) -> bool:
    return attr.startswith("_") and not (attr.startswith("__") and attr.endswith("__"))


def module_aliases(tree: ast.Module, modules) -> dict[str, str]:
    """Every name a file binds to a core module, at any scope."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


class _CoreScan(_Scope):
    def __init__(self, me: str, modules, aliases):
        super().__init__()
        self.me, self.modules, self.aliases = me, modules, aliases
        self.imports: list[tuple[str, str, int, bool]] = []
        self.reaches: list[tuple[str, str, int]] = []

    def _imported(self, target: str, node):
        if target in self.modules and target != self.me:
            self.imports.append((self.qualname, target, node.lineno,
                                 bool(self.deferred)))

    def visit_Import(self, node):
        for alias in node.names:
            self._imported(alias.name, node)

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module:
            self._imported(node.module, node)
            if node.module in self.modules:
                for alias in node.names:
                    if _private(alias.name):
                        self.reaches.append(
                            (self.qualname, f"{node.module}.{alias.name}",
                             node.lineno))

    def visit_Attribute(self, node):
        if (isinstance(node.value, ast.Name) and node.value.id in self.aliases
                and self.aliases[node.value.id] != self.me
                and _private(node.attr)):
            self.reaches.append(
                (self.qualname, f"{self.aliases[node.value.id]}.{node.attr}",
                 node.lineno))
        self.generic_visit(node)


def scan_core(core: Path) -> dict:
    """The import and private-reach facts of every core module."""
    modules = core_modules(core)
    facts = {}
    for name, path in modules.items():
        tree = _parse(path)
        scan = _CoreScan(name, modules, module_aliases(tree, modules))
        scan.visit(tree)
        facts[name] = scan
    return facts


def _tally(rows) -> dict[str, tuple[int, int]]:
    """identity → (occurrences, first line)."""
    counts: Counter = Counter()
    first: dict[str, int] = {}
    for identity, line in rows:
        counts[identity] += 1
        first[identity] = min(first.get(identity, line), line)
    return {identity: (counts[identity], first[identity]) for identity in counts}


def top_level_edges(facts) -> set[tuple[str, str]]:
    return {(name, target) for name, scan in facts.items()
            for _q, target, _l, deferred in scan.imports if not deferred}


def function_imports(facts) -> dict[str, tuple[int, int]]:
    return _tally((f"{name}.py::{q} -> {target}", line)
                  for name, scan in facts.items()
                  for q, target, line, deferred in scan.imports if deferred)


def private_reaches(facts) -> dict[str, tuple[int, int]]:
    return _tally((f"{name}.py::{q} -> {reached}", line)
                  for name, scan in facts.items()
                  for q, reached, line in scan.reaches)


def layer_violations(facts, block) -> dict[str, tuple[int, int]]:
    """Top-level imports whose target is not on a strictly lower layer."""
    layers = layer_of(block)
    rows = []
    for name, scan in facts.items():
        for _q, target, line, deferred in scan.imports:
            if deferred:
                continue
            if layers.get(target, -1) >= layers.get(name, -1):
                rows.append((f"{name}.py -> {target}", line))
    return _tally(rows)


def direction(facts, block, identity: str) -> str:
    """'down', 'same' or 'up' for a function-level import identity — for
    the printed inventory, so a reader sees which deferred imports hide an
    upward dependency."""
    layers = layer_of(block)
    source = identity.split(".py::")[0]
    target = identity.rsplit(" -> ", 1)[1]
    a, b = layers.get(source, -1), layers.get(target, -1)
    return "down" if b < a else ("same" if a == b else "up")


# ── routes ─────────────────────────────────────────────────────────────

def _const_str(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise ValueError(f"line {node.lineno}: expected a string literal")


def routes(table: Path) -> dict[str, tuple[str, int]]:
    """`METHOD path` → (request class, line) for every row of the `ROUTES`
    table in `routes.py` (WP04). Every row must be a literal
    `Route("METHOD", "path", "class", handler, …)` call: a row this cannot
    read is an error, never an omission."""
    found: dict[str, tuple[str, int]] = {}
    rows = None
    for node in _parse(table).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "ROUTES"):
            rows = node.value
    if not isinstance(rows, (ast.Tuple, ast.List)):
        raise ValueError(f"{table.name}: no literal ROUTES table found")
    for row in rows.elts:
        if not (isinstance(row, ast.Call) and isinstance(row.func, ast.Name)
                and row.func.id == "Route" and len(row.args) >= 4):
            raise ValueError(f"line {row.lineno}: a route row this inventory "
                             f"cannot read: {ast.unparse(row)}")
        method, path, klass = (_const_str(a) for a in row.args[:3])
        key = f"{method} {path}"
        if key in found:
            raise ValueError(f"{key} is declared twice")
        found[key] = (klass, row.lineno)
    return found


def handlers_calling_doors(table: Path) -> list[str]:
    """Handlers in `routes.py` that call a door themselves. The door is the
    dispatcher's, by class, so that no route can skip it by forgetting a
    call — a handler checking its own origin is a second, divergent door."""
    found = []
    for node in ast.walk(_parse(table)):
        if (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
                and (getattr(node.func, "id", None) or getattr(node.func, "attr", ""))
                in ("origin_problem", "card_write_problem", "door_problem")):
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
    return found


# ── standalone executables and composition ─────────────────────────────


def standalone_files(core: Path) -> list[Path]:
    """Python the board runs as a separate program: the agent adapters
    and the run shims. They must stay importable without core."""
    out = []
    candidates = [core / "bench-verify", core / "bench-progress"]
    candidates += sorted(p for p in (core / "adapters").rglob("*") if p.is_file())
    for path in candidates:
        if path.suffix == ".py":
            out.append(path)
        elif path.suffix == "" and path.read_bytes()[:40].startswith(b"#!") \
                and b"python" in path.read_bytes()[:40]:
            out.append(path)
    return out


def standalone_problems(core: Path) -> list[str]:
    modules = core_modules(core)
    problems = []
    for path in standalone_files(core):
        for node in ast.walk(_parse(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in modules:
                    problems.append(f"{path.relative_to(core)}:{node.lineno} "
                                    f"imports core module {name}")
    return problems


def composition_problems(facts, block) -> list[str]:
    composed = block["composition"]
    return [f"{name}.py:{line} imports composition module {target}"
            for name, scan in facts.items() if name not in composed
            for _q, target, line, _d in scan.imports if target in composed]


# ── provider vocabulary ────────────────────────────────────────────────

VOCABULARY = re.compile(r"\bgh\b|GH_|github|copilot", re.IGNORECASE)
# Since WP08 (task 116) the provider's code lives under `providers/`, which
# is not a core module and is not counted; no core module is exempt.
PROVIDER_MODULE = None


def _docstrings(tree) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(id(first.value))
    return out


def provider_vocabulary(core: Path) -> dict[str, int]:
    """Per core module (the provider module itself excepted): how often
    GitHub's vocabulary — `gh`, `GH_…` settings, GitHub, Copilot — appears
    in code and strings. Docstrings and comments are prose about the
    design and are not counted."""
    out = {}
    for name, path in core_modules(core).items():
        if name == PROVIDER_MODULE:
            continue
        tree = _parse(path)
        skip = _docstrings(tree)
        hits = 0
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in skip:
                text = node.value
            elif isinstance(node, ast.Name):
                text = node.id
            elif isinstance(node, ast.Attribute):
                text = node.attr
            elif isinstance(node, ast.alias):
                text = node.asname or node.name
            if text:
                hits += len(VOCABULARY.findall(text))
        if hits:
            out[name] = hits
    return out


# ── test patch targets ─────────────────────────────────────────────────


def module_names(path: Path) -> set[str]:
    """Every name a module binds at its top level, including names its
    functions declare `global` and assign."""
    tree = _parse(path)
    names: set[str] = set()

    def bind(target):
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                bind(elt)
        elif isinstance(target, ast.Starred):
            bind(target.value)

    def walk(body):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(stmt.name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    bind(target)
            elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                bind(stmt.target)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                bind(stmt.target)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars)
            for field in ("body", "orelse", "finalbody"):
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef)):
                    walk(getattr(stmt, field, []) or [])
            for handler in getattr(stmt, "handlers", []) or []:
                walk(handler.body)

    walk(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
    return names


def _is_patch_object(func) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr == "object"
            and ((isinstance(func.value, ast.Attribute) and func.value.attr == "patch")
                 or (isinstance(func.value, ast.Name) and func.value.id == "patch")))


def _is_patch(func) -> bool:
    return ((isinstance(func, ast.Attribute) and func.attr == "patch"
             and not (isinstance(func.value, ast.Name) and func.value.id == "self"))
            or (isinstance(func, ast.Name) and func.id == "patch"))


def _literal_rows(node) -> list[list] | None:
    """The rows a `for` loop iterates when they are written out: a tuple
    or list (of tuples), or a dict literal's `.items()`."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return [list(e.elts) if isinstance(e, (ast.Tuple, ast.List)) else [e]
                for e in node.elts]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "items" and isinstance(node.func.value, ast.Dict)):
        return [[k, v] for k, v in zip(node.func.value.keys, node.func.value.values)]
    return None


def _loop_bindings(loop) -> dict[str, list[str] | None]:
    """Name → the string literals it takes in this loop, or None when
    the loop does not write them out."""
    names = ([loop.target.id] if isinstance(loop.target, ast.Name)
             else [e.id if isinstance(e, ast.Name) else None
                   for e in getattr(loop.target, "elts", [])])
    rows = _literal_rows(loop.iter)
    out = {}
    for position, name in enumerate(names):
        if name is None:
            continue
        values = None
        if rows is not None and all(len(r) > position for r in rows):
            cells = [r[position] for r in rows]
            if all(isinstance(c, ast.Constant) and isinstance(c.value, str)
                   for c in cells):
                values = [c.value for c in cells]
        out[name] = values
    return out


class _PatchScan(_Scope):
    def __init__(self, aliases, helpers):
        super().__init__()
        self.aliases, self.helpers = aliases, helpers
        self.targets: list[tuple[str, str, int, str]] = []   # module, attr, line, how
        self.dynamic: list[tuple[str, str, int]] = []        # qualname, module, line
        self.loops: list[dict] = []

    def _bound(self, name: str):
        """(is a loop variable, its literal values or None)."""
        for frame in reversed(self.loops):
            if name in frame:
                return True, frame[name]
        return False, None

    def visit_For(self, node):
        self.loops.append(_loop_bindings(node))
        self.generic_visit(node)
        self.loops.pop()

    visit_AsyncFor = visit_For

    def _target(self, node, attr, how):
        self.targets.append((self.aliases[node.id], attr, node.lineno, how))

    def _named_target(self, call, module_node, attr_node, how):
        """The (module, attribute) pair of a setattr or patch.object."""
        if not isinstance(module_node, ast.Name):
            return
        if module_node.id not in self.aliases:
            # a loop variable standing for a module: not readable here
            if self._bound(module_node.id)[0]:
                self.dynamic.append((self.qualname, "<module>", call.lineno))
            return
        if isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str):
            self._target(module_node, attr_node.value, how)
            return
        if isinstance(attr_node, ast.Name):
            _loop, values = self._bound(attr_node.id)
            if values is not None:
                for value in values:
                    self.targets.append((self.aliases[module_node.id], value,
                                         call.lineno, f"{how} (loop)"))
                return
        self.dynamic.append((self.qualname, self.aliases[module_node.id],
                             call.lineno))

    def _assigned(self, target):
        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and target.value.id in self.aliases):
            self._target(target.value, target.attr, "assignment")
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._assigned(elt)

    def visit_Assign(self, node):
        for target in node.targets:
            self._assigned(target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._assigned(node.target)
        self.generic_visit(node)

    visit_AnnAssign = visit_AugAssign

    def visit_FunctionDef(self, node):
        if node.name in self.helpers and node.args.kwarg is not None:
            # the helper's own setattr is checked at each call, by keyword
            return
        super().visit_FunctionDef(node)

    def visit_Call(self, node):
        func, args = node.func, node.args
        created = any(k.arg == "create" for k in node.keywords)
        if isinstance(func, ast.Name) and func.id == "setattr" and len(args) >= 2:
            self._named_target(node, args[0], args[1], "setattr")
        elif _is_patch_object(func) and len(args) >= 2 and not created:
            self._named_target(node, args[0], args[1], "patch.object")
        elif (_is_patch(func) and args and isinstance(args[0], ast.Constant)
              and isinstance(args[0].value, str) and not created):
            parts = args[0].value.split(".")
            if len(parts) >= 2 and parts[0] in set(self.aliases.values()):
                self.targets.append((parts[0], parts[1], node.lineno, "patch"))
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None)
        if name in self.helpers:
            for keyword in node.keywords:
                if keyword.arg:
                    self.targets.append((self.helpers[name], keyword.arg,
                                         node.lineno, f"{name}(**)"))
        self.generic_visit(node)


def _helpers(tree, aliases) -> dict[str, str]:
    """Methods taking `**values` that `setattr` each one on a core module
    (the `self.patch(REPO=…)` idiom): name → module."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.args.kwarg is not None:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id == "setattr" and len(inner.args) >= 2
                        and isinstance(inner.args[0], ast.Name)
                        and inner.args[0].id in aliases):
                    out[node.name] = aliases[inner.args[0].id]
    return out


def patch_targets(core: Path, tests: Path) -> tuple[list[str], dict[str, tuple[int, int]], int]:
    """(inert targets, dynamic targets, how many targets were checked)."""
    modules = core_modules(core)
    defined = {name: module_names(path) for name, path in modules.items()}
    inert, dynamic, checked = [], [], 0
    for path in sorted(tests.glob("*.py")):
        tree = _parse(path)
        aliases = module_aliases(tree, modules)
        if not aliases:
            continue
        scan = _PatchScan(aliases, _helpers(tree, aliases))
        scan.visit(tree)
        for module, attr, line, how in scan.targets:
            checked += 1
            if attr not in defined[module] and not (attr.startswith("__")
                                                    and attr.endswith("__")):
                inert.append(f"{path.name}:{line} {how} {module}.{attr}, "
                             f"which {module}.py does not define")
        dynamic += [(f"{path.name}::{q} -> {module}.<computed>", line)
                    for q, module, line in scan.dynamic]
    return inert, _tally(dynamic), checked


# ── the reviewed baselines (WP01, at df72007) ──────────────────────────
# identity: (occurrences, line at review). Lines are for navigation only.

# Top-level imports against the current layer table: none at review.
TOP_LEVEL_EXCEPTIONS: dict[str, tuple[int, int]] = {}

# Deferred core imports: none since WP13 (task 122). The twenty at WP12's
# end — thirteen hiding an upward dependency, seven deferred downward
# imports — went by public ownership, not by aliases or by moving a
# module to look compliant:
#   - setupguide's six upward imports: its shared helpers moved down into
#     `setupbase`, the file inspection into `appinspect`, and setupguide
#     now sits above readiness, previews and setuptasks, which it composes;
#   - setuptasks → previews: previews reads the application through
#     `appinspect` instead of `setuptasks`, so setuptasks sits above it;
#   - drive → previews ×4: `drive.scrub` owns a drive log's scrubbing; the
#     drive's end hook is wired by `board.wire` (composition);
#   - settings → agents: the run registry is `state.AGENTS`;
#   - board's two and setuptasks' four were downward and are top-level.
# An empty baseline is the rule, not a count: any function-level core
# import anywhere is "new" and fails.
FUNCTION_IMPORTS: dict[str, tuple[int, int]] = {}

# Cross-module private reaches: none since WP13 (task 122). Each of the
# forty-nine became a public name on its owner (`taskfiles.is_fence`,
# `one_line`, `refuse_escape`, `depends_on`, `first_match`,
# `commit_paths`; `agents.launch`, `pick_name`, `finish`, `died_on`,
# `validate_launch`, `preflight_profile`; `gates.branch_tips`,
# `contained`, `epoch()`; `settings.checks_of`; `drive.alive`;
# `config.PURPOSE_SUFFIX`) or moved with its helpers into `setupbase`.
PRIVATE_REACHES: dict[str, tuple[int, int]] = {}

# Patch calls whose module the scan cannot read (a loop over computed
# pairs). `mock.patch.object` refuses a missing attribute itself unless
# told `create=True`, so these cannot go inert silently; they are still
# counted, and the count may only fall.
DYNAMIC_PATCH_TARGETS: dict[str, tuple[int, int]] = {
    "test_verify_on_review.py::NoRetryNoFollowOn.guarded -> <module>.<computed>":
        (1, 443),
    # WP12 (task 121): the reuse race's two seams now live in two owners
    # (`evidence.store`, `staleness.outcome_now`), so the loop names the
    # module too. `mock.patch.object` refuses a missing attribute, which is
    # how this test caught the old `verify._store` target loudly.
    "test_check_reuse.py::NewestRelevantAnswerDecides."
    "test_a_rerun_reserved_after_the_read_but_before_the_reuse_wins -> "
    "<module>.<computed>": (1, 465),
    # and the proof that every name WP12 moved fails loudly on `verify`:
    # it patches each one, expecting `mock.patch.object` to refuse
    "test_runner_identity.py::RunnerRevisionHasOneOwner."
    "test_the_old_names_fail_loudly -> verify.<computed>": (1, 158),
}

# GitHub vocabulary per core module outside github.py (WP08 moves it).
# `redact`'s four are GitHub's credential *shapes* (task 111 moved them
# out of `settings` and `agentsettings`, two each): what a token looks
# like, not provider behaviour, so WP08 leaves them where they are.
# `routes`' fourteen are `httpd`'s thirteen, moved with the handlers by
# WP04, plus the route table's one reference to the Copilot handler.
# `completion`'s are merge & clean up's, moved out of the provider module
# by WP07 (task 115): its `github.…` accessor calls, `gh`'s availability
# and its refusals' sentences. WP08 moves the provider half behind
# `githost`. The same move took four from `routes`, six from `delivery`
# and one from `gates`, whose references now name `completion`.
#
# WP08 (task 116) moved every `gh` argument list, GitHub url and payload
# shape into `providers/github/` (not a core module, not counted) and
# renamed `github` to `changes`, which is no longer exempt: its 24 are the
# persisted and page-facing `copilot` keys of the PR registry, the
# `request_copilot` route handler and its sentence, and the `gh_available`
# accessor completion asks — vocabulary, no host shape. `githost`'s two
# are the default provider's name and `BOARD_GH_BIN`, the setting the
# GitHub provider's CLI is configured by. `board`, `phases` and `watch`
# named the module and now name `changes`; `reviews` fell from 18 to 8
# and `completion` from 38 to 13 with the rename and the provider's own
# reason in its refusals; `templates`' sample
# pull-request url became a neutral sample. `gates`, `profiles` and
# `settings` keep the `github` / `separate-reviewer` policy words, which
# are persisted and are not renamed (plan §1.3).
#
# WP11 (task 120) moved the merge-rule policy out of `profiles` verbatim:
# thirteen of its seventeen went with it to `gatepolicy` (the review
# policy words and their sentences), and the total is unchanged.
#
# WP16 (task 125) named the external reviewer's field once in `changes`
# (`EXTERNAL_REVIEW`), so the combined verdict the page used to compute
# reads it without spelling it again: 24 → 23.
PROVIDER_VOCABULARY: dict[str, int] = {
    # Task 166: completion's merged-elsewhere event names the remote's
    # target rather than "GitHub": 13 → 12.
    "agents": 2, "changes": 23, "completion": 12, "config": 10,
    "delivery": 10, "doctor": 3, "gatepolicy": 13, "gates": 23,
    "githost": 2, "plans": 1, "profiles": 4, "readiness": 9, "redact": 4,
    "reviews": 8, "routes": 5, "settings": 36, "setupguide": 13,
    # WP13 (task 122): two of setupguide's fifteen are the
    # `.github/copilot-instructions.md` instruction file's name, which
    # moved with the inspection to `appinspect`; the total is unchanged
    "appinspect": 2,
    # WP12 (task 121): verify's one — the `gh[pousr]_` token shape in its
    # own credential table — went when the evidence log began asking
    # `redact`, whose four already count it
}

# Every route and its request class, as routes.py declares it (WP04).
# The dispatcher in httpd.py applies the class's door before a handler
# runs; a route changing class is a reviewed edit of this snapshot.
ROUTES: dict[str, str] = {
    "GET /": "read",
    # the page's own addresses (task 134): the shell for these shapes only
    "GET /board": "read",
    # the Brain (task 157): its list and one entry, `/brain/e/<id>`
    "GET /brain": "read",
    "GET /brain/*": "read",
    "GET /api/brain": "read",
    "GET /api/brain/entry": "read",
    # writing it (task 158): one entry file per write, revision-bound
    "GET /api/brain/revisions": "read",
    "GET /api/brain/operation": "read",
    "POST /api/brain/create": "browser-mutation",
    "POST /api/brain/edit": "browser-mutation",
    "POST /api/brain/classify": "browser-mutation",
    "POST /api/brain/override": "browser-mutation",
    "POST /api/brain/retire": "browser-mutation",
    "POST /api/brain/retry": "browser-mutation",
    # reviews filed on cards, read in the Brain (task 168), and a person's
    # classification of one — a record under brain/reviews/, never the card
    "GET /api/brain/reviews": "read",
    "GET /api/brain/review": "read",
    "POST /api/brain/review/classify": "browser-mutation",
    "POST /api/brain/review/override": "browser-mutation",
    # recovering a report that was not filed, and the explicit rebuild of
    # the Brain's review index (task 169)
    "GET /api/reports/filing": "read",
    "POST /api/reports/filing/retry": "browser-mutation",
    "POST /api/brain/reviews/rebuild": "browser-mutation",
    # drafting it with the Brain Agent (task 175): two reads, and a
    # person's steps — Generate and Continue launch one bounded run each
    "GET /api/brain/draft/preflight": "read",
    "GET /api/brain/drafts": "read",
    "POST /api/brain/draft/start": "browser-mutation",
    "POST /api/brain/draft/continue": "browser-mutation",
    "POST /api/brain/draft/cancel": "browser-mutation",
    "POST /api/brain/draft/discard": "browser-mutation",
    "POST /api/brain/draft/retarget": "browser-mutation",
    "POST /api/brain/draft/apply": "browser-mutation",
    "GET /cards/*": "read",
    # the Orchestrator view (task 145), a page like the others
    "GET /orchestrator": "read",
    "GET /orchestrator/*": "read",
    "GET /phases": "read",
    "GET /phases/*": "read",
    "GET /sessions": "read",
    "GET /sessions/*": "read",
    "GET /focus": "read",
    "GET /focus/*": "read",
    "GET /settings": "read",
    "GET /settings/*": "read",
    "GET /api/card": "read",
    "GET /api/card/locate": "read",
    "GET /api/archive/all": "read",
    "GET /api/card/acceptance": "read",
    "GET /api/card/decisions": "read",
    "GET /api/card/delivery": "read",
    "GET /api/card/timing": "read",
    "GET /api/card/discussion": "read",
    "GET /api/card/inputs": "read",
    "GET /api/card/result": "read",
    "GET /api/artifact": "read",
    "GET /api/diff": "read",
    "GET /api/doctor": "read",
    "GET /api/evidence": "read",
    "GET /api/evidence/log": "read",
    "GET /api/gates": "read",
    "GET /api/manifest": "read",
    "GET /api/plan": "read",
    "GET /api/preview": "read",
    "GET /api/preview/log": "read",
    "GET /api/profile": "read",
    "GET /api/progress": "read",
    "GET /api/review": "read",
    "GET /api/session": "read",
    "GET /api/settings": "read",
    "GET /api/settings/history": "read",
    # the orchestrator's effective state (task 137): a read that stays one
    "GET /api/orchestration": "read",
    # reasoning jobs and their validated responses (task 139): a read
    # that launches no reasoning run
    "GET /api/orchestration/reasoning": "read",
    # the decision trace (task 184): every pass, paged; reads only
    "GET /api/orchestration/trace": "read",
    "GET /api/orchestration/trace/entry": "read",
    "GET /api/setup": "read",
    "GET /api/setup/verification": "read",
    "GET /api/state": "read",
    "GET /api/stream": "read",
    "GET /api/task/completion": "read",
    "GET /api/tasks": "read",
    "GET /api/verify/checks": "read",
    "GET /api/verify/required": "read",
    "GET /board.html": "read",
    "GET /files/*": "read",
    "GET /index.html": "read",
    "GET /web/*": "read",
    "POST /api/agent/act-pr": "browser-mutation",
    "POST /api/agent/review": "browser-mutation",
    "POST /api/agent/review-pr": "browser-mutation",
    "POST /api/agent/start": "browser-mutation",
    "POST /api/agent/stop": "browser-mutation",
    "POST /api/archive": "browser-mutation",
    "POST /api/card/acceptance/decide": "browser-mutation",
    "POST /api/card/decisions/answer": "browser-mutation",
    "POST /api/card/decisions/withdraw": "browser-mutation",
    # asking the orchestrator (task 181): a person's question launches one
    # read-only reasoning run; reading conversations launches nothing
    "GET /api/ask": "read",
    "GET /api/ask/thread": "read",
    "GET /api/ask/facts": "read",
    "POST /api/ask": "browser-mutation",
    "POST /api/ask/retry": "browser-mutation",
    "POST /api/ask/cancel": "browser-mutation",
    "POST /api/card/acceptance/track": "browser-mutation",
    "POST /api/card/comment": "browser-mutation",
    "POST /api/card/delivery/check-remote": "browser-mutation",
    "POST /api/card/delivery/launch-check": "browser-mutation",
    "POST /api/card/finding": "browser-mutation",
    "POST /api/card/inputs/check": "browser-mutation",
    "POST /api/card/inputs/classify": "browser-mutation",
    "POST /api/card/result/setting": "browser-mutation",
    "POST /api/card/result/dismiss": "browser-mutation",
    "POST /api/card/result/preview": "browser-mutation",
    "POST /api/artifact/withhold": "browser-mutation",
    "POST /api/card/stack/action": "browser-mutation",
    "POST /api/card/stack/preview": "browser-mutation",
    "POST /api/card/refine": "browser-mutation",
    "POST /api/card/refine/apply": "browser-mutation",
    "POST /api/card/refine/cancel": "browser-mutation",
    "POST /api/card/refine/discard": "browser-mutation",
    "POST /api/command/run": "browser-mutation",
    "POST /api/doctor/run": "browser-mutation",
    "POST /api/drive/start": "browser-mutation",
    "POST /api/drive/stop": "browser-mutation",
    "POST /api/events": "event-ingest",
    "POST /api/gates/override": "browser-mutation",
    "POST /api/move": "browser-mutation",
    "POST /api/phase/add": "browser-mutation",
    "POST /api/phase/run": "browser-mutation",
    "POST /api/phase/stop": "browser-mutation",
    "POST /api/plan/replan": "browser-mutation",
    "POST /api/pr/copilot": "browser-mutation",
    "POST /api/pr/open": "browser-mutation",
    "POST /api/preview/cancel": "policy-mutation",
    "POST /api/preview/check": "policy-mutation",
    "POST /api/preview/configure": "policy-mutation",
    "POST /api/preview/prepare": "policy-mutation",
    "POST /api/preview/start": "policy-mutation",
    "POST /api/preview/stop": "policy-mutation",
    "POST /api/preview/validate": "policy-mutation",
    "POST /api/progress": "run-authenticated",
    "POST /api/settings/agents/apply": "policy-mutation",
    "POST /api/settings/agents/preview": "policy-mutation",
    "POST /api/settings/agents/render": "policy-mutation",
    "POST /api/settings/agents/validate": "policy-mutation",
    "POST /api/settings/apply": "policy-mutation",
    "POST /api/settings/preview": "policy-mutation",
    "POST /api/settings/validate": "policy-mutation",
    "POST /api/setup/cards/create": "policy-mutation",
    "POST /api/setup/cards/decide": "policy-mutation",
    "POST /api/setup/cards/preview": "policy-mutation",
    "POST /api/setup/cards/start": "policy-mutation",
    # operating agreements and the execution installation (task 137)
    "POST /api/orchestration/agreement/validate": "policy-mutation",
    "POST /api/orchestration/agreement/draft": "policy-mutation",
    "POST /api/orchestration/agreement/preview": "policy-mutation",
    "POST /api/orchestration/agreement/discard": "policy-mutation",
    "POST /api/orchestration/agreement/apply": "policy-mutation",
    # reserved phase allocations to shared capacity (task 182)
    "POST /api/orchestration/agreement/migrate": "policy-mutation",
    "POST /api/orchestration/scope": "policy-mutation",
    "POST /api/orchestration/installation": "policy-mutation",
    # a person settles an operation the scheduler held (task 141)
    "POST /api/orchestration/operation": "policy-mutation",
    "POST /api/orchestration/phase": "policy-mutation",
    # a person resolves a held recovery incident or resets the breaker
    # (task 144)
    "POST /api/orchestration/recovery": "policy-mutation",
    "POST /api/setup/decide": "policy-mutation",
    "POST /api/setup/task": "policy-mutation",
    "POST /api/setup/verification/activate": "policy-mutation",
    "POST /api/setup/verification/register": "policy-mutation",
    "POST /api/setup/verification/review": "policy-mutation",
    "POST /api/task/complete": "browser-mutation",
    "POST /api/task/complete/forget": "browser-mutation",
    "POST /api/unarchive": "browser-mutation",
    "POST /api/archive/all": "browser-mutation",
    "POST /api/verify": "browser-mutation",
    "POST /api/verify/cancel": "browser-mutation",
    "POST /api/verify/details": "run-authenticated",
    "POST /api/verify/required": "policy-mutation",
    "POST /api/verify/required/cancel": "policy-mutation",
}


# ── comparison ─────────────────────────────────────────────────────────


def ratchet(name: str, actual: dict, baseline: dict) -> list[str]:
    """Problems comparing an inventory with its reviewed baseline:
    anything new, more of anything, and anything the baseline still lists
    that has gone (delete it, so the list shrinks)."""
    problems = []
    for identity in sorted(set(actual) | set(baseline)):
        have = actual.get(identity)
        want = baseline.get(identity)
        have_n = have[0] if isinstance(have, tuple) else have
        want_n = want[0] if isinstance(want, tuple) else want
        where = f"  (now at line {have[1]})" if isinstance(have, tuple) else ""
        if want is None:
            problems.append(f"{name}: new, not in the reviewed baseline: "
                            f"{identity!r}: {have!r},{where}")
        elif have is None:
            problems.append(f"{name}: gone — delete it from the baseline and "
                            f"lower the count: {identity!r}")
        elif have_n > want_n:
            problems.append(f"{name}: {identity!r} grew from {want_n} to "
                            f"{have_n}{where}")
        elif have_n < want_n:
            problems.append(f"{name}: {identity!r} shrank from {want_n} to "
                            f"{have_n} — lower the baseline{where}")
    return problems


def total(inventory: dict) -> int:
    return sum(v[0] if isinstance(v, tuple) else (v if isinstance(v, int) else 1)
               for v in inventory.values())


def inventory(core: Path = CORE, tests: Path = TESTS) -> dict:
    """Every inventory, computed from the tree at `core` and `tests`."""
    blocks = read_blocks((core / DOC).read_text(encoding="utf-8"))
    facts = scan_core(core)
    inert, dynamic, checked = patch_targets(core, tests)
    route_lines = routes(core / "routes.py")
    return {
        "inert": inert,
        "checked": checked,
        "blocks": blocks,
        "facts": facts,
        "edges": top_level_edges(facts),
        "top-level-exceptions": layer_violations(facts, blocks["current"]),
        "function-imports": function_imports(facts),
        "private-reaches": private_reaches(facts),
        "dynamic-patch-targets": dynamic,
        "provider-vocabulary": provider_vocabulary(core),
        "routes": {k: v[0] for k, v in route_lines.items()},
        "route-lines": route_lines,
    }


def render(found: dict) -> str:
    """The inventory as text, for a reviewer. Never written to a file."""
    out = []
    current = found["blocks"]["current"]
    out.append(f"# top-level edges ({len(found['edges'])}), A -> B: A imports B")
    for a, b in sorted(found["edges"]):
        out.append(f"{a} -> {b}")
    for key in ("top-level-exceptions", "function-imports", "private-reaches",
                "dynamic-patch-targets"):
        out.append(f"\n# {key} ({total(found[key])})")
        for identity, (count, line) in sorted(found[key].items()):
            extra = (f"  [{direction(found['facts'], current, identity)}]"
                     if key == "function-imports" else "")
            out.append(f"{identity!r}: ({count}, {line}),{extra}")
    out.append(f"\n# provider-vocabulary ({total(found['provider-vocabulary'])})")
    for name, count in sorted(found["provider-vocabulary"].items()):
        out.append(f"{name!r}: {count},")
    out.append(f"\n# routes ({len(found['routes'])})")
    for key, (door, line) in sorted(found["route-lines"].items()):
        out.append(f"{key!r}: {door!r},  # httpd.py:{line}")
    return "\n".join(out)


BASELINES = {
    "top-level-exceptions": TOP_LEVEL_EXCEPTIONS,
    "function-imports": FUNCTION_IMPORTS,
    "private-reaches": PRIVATE_REACHES,
    "dynamic-patch-targets": DYNAMIC_PATCH_TARGETS,
    "provider-vocabulary": PROVIDER_VOCABULARY,
}


def governance_problems(core: Path = CORE, tests: Path = TESTS) -> list[str]:
    """Every governance problem in a tree. The test runs this on the real
    tree; the scratch-copy tests run it on a copy with a violation in it."""
    found = inventory(core, tests)
    blocks = found["blocks"]
    problems = []
    for key, baseline in BASELINES.items():
        problems += ratchet(key, found[key], baseline)
    problems += [f"route {p}" for p in _route_problems(found["routes"])]
    problems += [f"route handler checks its own door: {p}"
                 for p in handlers_calling_doors(core / "routes.py")]
    problems += composition_problems(found["facts"], blocks["current"])
    problems += eager_problems(found["facts"])
    problems += standalone_problems(core)
    problems += leaf_problems(core)
    problems += [f"inert patch target: {p}" for p in found["inert"]]
    return problems


# Eager startup (check 11, WP13 / task 122): `board.py` imports every core
# module at its top level, so a running board executes the code that was
# on disk when it started, and a module added later without being wired
# in is a failure rather than a lazy load on some first request.
EAGER = "board"


def eager_problems(facts) -> list[str]:
    if EAGER not in facts:
        return [f"{EAGER}.py is missing"]
    loaded = {t for _q, t, _l, deferred in facts[EAGER].imports if not deferred}
    return [f"{EAGER}.py does not import core module {name} at its top level"
            for name in sorted(set(facts) - loaded - {EAGER})]


# Stdlib leaves (check 10): modules that import nothing from core and
# nothing outside the standard library, anywhere in the file. Each joins
# the list in the package that builds it (WP03a: gitproc).
STDLIB_LEAVES = ("gitproc", "records", "redact")


def leaf_problems(core: Path) -> list[str]:
    modules = set(core_modules(core))
    problems = []
    for leaf in STDLIB_LEAVES:
        path = core / f"{leaf}.py"
        if not path.exists():
            problems.append(f"stdlib leaf {leaf}.py is missing")
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    problems.append(f"{leaf}.py:{node.lineno}: relative import")
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root in modules:
                    problems.append(f"{leaf}.py:{node.lineno}: stdlib leaf "
                                    f"imports core module {root}")
                elif root != "__future__" and root not in sys.stdlib_module_names:
                    problems.append(f"{leaf}.py:{node.lineno}: stdlib leaf "
                                    f"imports non-stdlib {root}")
    return problems


def _route_problems(actual: dict[str, str]) -> list[str]:
    problems = []
    for key in sorted(set(actual) | set(ROUTES)):
        if key not in ROUTES:
            problems.append(f"new, not in the snapshot: {key!r}: {actual[key]!r},")
        elif key not in actual:
            problems.append(f"gone: {key!r}")
        elif actual[key] != ROUTES[key]:
            problems.append(f"{key!r} passes {actual[key]!r}, the snapshot says "
                            f"{ROUTES[key]!r}")
    return problems


# ── the tests ──────────────────────────────────────────────────────────


class DocumentIsTheSource(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.blocks = read_blocks((CORE / DOC).read_text(encoding="utf-8"))
        cls.modules = set(core_modules(CORE))

    def test_current_layers_place_every_existing_module_once(self):
        placed = set(layer_of(self.blocks["current"]))
        self.assertEqual(placed, self.modules,
                         "the current layer table must be facts: every core "
                         "module, and nothing that does not exist")

    def test_proposed_layers_are_modelled_separately(self):
        proposed = self.blocks["proposed"]
        placed = set(layer_of(proposed))
        # every current module has a place, or is named as retired …
        self.assertEqual(self.modules - placed - proposed["retired"], set())
        # … retired modules are current ones, not inventions …
        self.assertLessEqual(proposed["retired"], self.modules)
        self.assertFalse(proposed["retired"] & placed)
        # … and a proposed module that does not exist yet is declared new,
        # which is all the test asks of it: it is not required to exist
        self.assertEqual(placed - self.modules, proposed["new"])
        self.assertFalse(proposed["new"] & self.modules,
                         "a module that now exists is no longer 'new'")

    def test_composition_is_named(self):
        self.assertLessEqual({"board", "httpd"}, self.blocks["current"]["composition"])

    def test_the_agents_md_map_says_what_the_block_says(self):
        """AGENTS.md's one-line map is a summary for people; it may not
        drift from the block that is the source."""
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.startswith("gitproc / records / redact < config < "))
        tiers = [set(t.replace("/", " ").split()) for t in line.split(" < ")]
        self.assertEqual(tiers, [set(l) for l in self.blocks["current"]["layers"]])

    def test_counts_block_matches_the_baselines(self):
        counts = self.blocks["counts"]
        expected = {key: total(base) for key, base in BASELINES.items()}
        expected["routes"] = len(ROUTES)
        self.assertEqual(counts, expected,
                         "ARCHITECTURE.md's counts block and this file's "
                         "baselines are reviewed together")


class TreeIsGoverned(unittest.TestCase):
    """The unchanged tree passes, with nothing skipped."""

    @classmethod
    def setUpClass(cls):
        cls.found = inventory()

    def test_top_level_imports_respect_the_current_layers(self):
        self.assertEqual(ratchet("top-level-exceptions",
                                 self.found["top-level-exceptions"],
                                 TOP_LEVEL_EXCEPTIONS), [])
        self.assertTrue(self.found["edges"], "no imports read: the check is vacuous")

    def test_function_level_imports_only_shrink(self):
        self.assertEqual(ratchet("function-imports", self.found["function-imports"],
                                 FUNCTION_IMPORTS), [])

    def test_private_reaches_only_shrink(self):
        self.assertEqual(ratchet("private-reaches", self.found["private-reaches"],
                                 PRIVATE_REACHES), [])

    def test_routes_match_the_snapshot(self):
        self.assertEqual(_route_problems(self.found["routes"]), [])

    def test_standalone_programs_import_no_core_module(self):
        self.assertGreaterEqual(len(standalone_files(CORE)), 8,
                                "too few standalone programs found: vacuous")
        self.assertEqual(standalone_problems(CORE), [])

    def test_composition_is_imported_only_by_composition(self):
        self.assertEqual(composition_problems(self.found["facts"],
                                              self.found["blocks"]["current"]), [])

    def test_provider_vocabulary_only_shrinks(self):
        self.assertEqual(ratchet("provider-vocabulary",
                                 self.found["provider-vocabulary"],
                                 PROVIDER_VOCABULARY), [])

    def test_test_patch_targets_exist(self):
        self.assertGreater(self.found["checked"], 100,
                           "too few patch targets read: vacuous")
        self.assertEqual(self.found["inert"], [])
        self.assertEqual(ratchet("dynamic-patch-targets",
                                 self.found["dynamic-patch-targets"],
                                 DYNAMIC_PATCH_TARGETS), [])

    def test_stdlib_leaves_are_pure(self):
        self.assertEqual(leaf_problems(CORE), [])

    def test_board_imports_every_core_module_at_startup(self):
        self.assertGreater(len(self.found["facts"]), 40, "too few modules: vacuous")
        self.assertEqual(eager_problems(self.found["facts"]), [])

    def test_the_whole_tree_has_no_problem(self):
        self.assertEqual(governance_problems(), [])

    def test_the_inventory_renders(self):
        text = render(self.found)
        self.assertIn("# function-imports", text)
        self.assertIn("'POST /api/move'", text)


class ScratchCopyCatchesViolations(unittest.TestCase):
    """A deliberately wrong import and private reach in a disposable copy
    fail; inserting lines moves nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-arch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.core = self.tmp / "core"
        shutil.copytree(CORE, self.core,
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.tests = self.tmp / "tests"
        shutil.copytree(TESTS, self.tests,
                        ignore=shutil.ignore_patterns("__pycache__", "fixtures"))

    def edit(self, rel: str, prefix: str = "", suffix: str = "", root=None) -> None:
        path = (root or self.core) / rel
        path.write_text(prefix + path.read_text(encoding="utf-8") + suffix,
                        encoding="utf-8")

    def test_the_copy_starts_clean(self):
        self.assertEqual(governance_problems(self.core, self.tests), [])

    def test_an_upward_top_level_import_fails(self):
        self.edit("taskfiles.py", suffix="\nimport agents  # wp01 probe\n")
        problems = governance_problems(self.core, self.tests)
        self.assertIn("top-level-exceptions: new, not in the reviewed baseline: "
                      "'taskfiles.py -> agents'", "\n".join(problems))

    def test_importing_composition_fails(self):
        self.edit("taskfiles.py", suffix="\ndef _probe():\n    import board\n")
        self.assertIn("taskfiles.py:", "\n".join(
            p for p in governance_problems(self.core, self.tests)
            if "composition module board" in p))

    def test_a_new_function_level_import_and_private_reach_fail(self):
        self.edit("templates.py", suffix=(
            "\n\ndef _wp01_probe():\n    import agents\n"
            "    return agents._launch\n"))
        text = "\n".join(governance_problems(self.core, self.tests))
        self.assertIn("function-imports: new, not in the reviewed baseline: "
                      "'templates.py::_wp01_probe -> agents'", text)
        self.assertIn("private-reaches: new, not in the reviewed baseline: "
                      "'templates.py::_wp01_probe -> agents._launch'", text)

    def test_a_reach_through_an_alias_or_a_from_import_fails(self):
        self.edit("templates.py", suffix=(
            "\nimport config as cfg\nfrom config import _never_public\n"
            "X = cfg._also_private\n"))
        text = "\n".join(governance_problems(self.core, self.tests))
        self.assertIn("'templates.py::<module> -> config._never_public'", text)
        self.assertIn("'templates.py::<module> -> config._also_private'", text)

    def test_a_downward_function_level_import_fails_too(self):
        """WP13 (task 122): the baseline is empty, so a deferred import
        fails whichever way it points — a lazy import of a *lower* module
        is still code a running board did not load at startup."""
        self.assertEqual(FUNCTION_IMPORTS, {})
        self.edit("setuptasks.py", suffix=(
            "\n\ndef _wp13_probe():\n    import state\n    return state\n"))
        self.assertIn("function-imports: new, not in the reviewed baseline: "
                      "'setuptasks.py::_wp13_probe -> state'", "\n".join(
                          governance_problems(self.core, self.tests)))

    def test_a_lazy_import_inside_a_method_or_a_nested_function_fails(self):
        self.edit("drive.py", suffix=(
            "\n\nclass _WP13:\n    def probe(self):\n        def inner():\n"
            "            from previews import record_last\n"
            "            return record_last\n        return inner\n"))
        self.assertIn("'drive.py::_WP13.probe.inner -> previews'", "\n".join(
            governance_problems(self.core, self.tests)))

    def test_a_second_occurrence_in_the_same_place_fails(self):
        """A reviewed exception that occurs twice is not the reviewed one:
        the count is part of the identity's baseline."""
        baseline = {"templates.py::_probe -> agents": (1, 1)}
        found = {"templates.py::_probe -> agents": (2, 1)}
        self.assertIn("function-imports: 'templates.py::_probe -> agents' "
                      "grew from 1 to 2",
                      "\n".join(ratchet("function-imports", found, baseline)))

    def test_inserting_lines_changes_no_identity(self):
        probe = ("\n\ndef _wp01_probe():\n    import agents\n"
                 "    return agents._launch_probe\n")
        self.edit("templates.py", suffix=probe)
        before = inventory(self.core, self.tests)
        shifted = "# wp01: shifted\n" * 25
        self.edit("templates.py", prefix=shifted)
        self.edit("httpd.py", prefix=shifted)
        moved = inventory(self.core, self.tests)
        for key in ("function-imports", "private-reaches"):
            self.assertTrue(before[key], f"the probe left no {key} to move")
            self.assertEqual(set(moved[key]), set(before[key]))
            for identity, (count, line) in moved[key].items():
                self.assertEqual(count, before[key][identity][0])
                self.assertEqual(line, before[key][identity][1] + 25)

    def test_a_removed_exception_must_leave_the_baseline(self):
        baseline = {"refine.py::_launch -> agents._launch": (1, 510)}
        self.assertIn("private-reaches: gone — delete it from the baseline "
                      "and lower the count: "
                      "'refine.py::_launch -> agents._launch'",
                      "\n".join(ratchet("private-reaches", {}, baseline)))

    ANCHOR = '    Route("POST", "/api/agent/stop",'

    def test_a_new_route_fails_the_snapshot(self):
        path = self.core / "routes.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(self.ANCHOR, text)
        path.write_text(text.replace(self.ANCHOR, (
            '    Route("POST", "/api/wp01-probe", "open", post_agent_stop),\n'
            + self.ANCHOR)), encoding="utf-8")
        self.assertIn("route new, not in the snapshot: 'POST /api/wp01-probe'",
                      "\n".join(governance_problems(self.core, self.tests)))

    def test_a_route_changing_class_fails_the_snapshot(self):
        path = self.core / "routes.py"
        text = path.read_text(encoding="utf-8")
        row = '    Route("POST", "/api/settings/apply", "policy-mutation",'
        self.assertIn(row, text)
        path.write_text(text.replace(row, row.replace(
            '"policy-mutation"', '"open"')), encoding="utf-8")
        self.assertIn("route 'POST /api/settings/apply' passes 'open'",
                      "\n".join(governance_problems(self.core, self.tests)))

    def test_a_handler_checking_its_own_door_fails(self):
        path = self.core / "routes.py"
        text = path.read_text(encoding="utf-8")
        anchor = "def post_agent_stop(req) -> None:\n"
        self.assertIn(anchor, text)
        path.write_text(text.replace(anchor, anchor + (
            "    httpd_door = origin_problem(req.headers, 1)\n")),
            encoding="utf-8")
        self.assertIn("route handler checks its own door",
                      "\n".join(governance_problems(self.core, self.tests)))

    def test_an_unreadable_route_row_is_an_error_not_an_omission(self):
        path = self.core / "routes.py"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(self.ANCHOR, (
            '    *[Route(m, "/api/wp01", "open", post_agent_stop) for m in "X"],\n'
            + self.ANCHOR)), encoding="utf-8")
        with self.assertRaises(ValueError):
            routes(path)

    def test_a_standalone_program_importing_core_fails(self):
        self.edit("adapters/claude/emit.py", suffix="\nimport config\n")
        self.assertIn("adapters/claude/emit.py", "\n".join(
            standalone_problems(self.core)))

    def test_a_core_module_board_does_not_load_fails(self):
        (self.core / "wp13probe.py").write_text('"""probe"""\n', encoding="utf-8")
        self.assertIn("board.py does not import core module wp13probe",
                      "\n".join(governance_problems(self.core, self.tests)))
        self.edit("board.py", prefix="import wp13probe  # noqa: F401\n")
        self.assertNotIn("wp13probe at its top level", "\n".join(
            governance_problems(self.core, self.tests)))

    def test_a_stdlib_leaf_importing_core_or_a_third_party_fails(self):
        self.edit("gitproc.py", suffix=(
            "\n\ndef _wp03_probe():\n    import config\n    import yaml\n"))
        text = "\n".join(governance_problems(self.core, self.tests))
        self.assertIn("stdlib leaf imports core module config", text)
        self.assertIn("stdlib leaf imports non-stdlib yaml", text)

    def test_new_provider_vocabulary_fails(self):
        self.edit("templates.py", suffix='\nWP01 = "gh pr view"\n')
        self.assertIn("provider-vocabulary", "\n".join(
            governance_problems(self.core, self.tests)))

    def test_inert_patch_targets_fail_in_every_form(self):
        (self.tests / "test_wp01_probe.py").write_text(
            "from unittest import mock\n"
            "import config\nimport verify as v\nimport changes\nimport agents\n"
            "config.NOT_A_SETTING = 1\n"
            "config.PORT = 1\n"                       # exists: fine
            "setattr(changes, '_gone', None)\n"
            "mock.patch.object(v, '_nope')\n"
            "mock.patch('agents._missing')\n"
            "mock.patch.object(v, '_made', create=True)\n"   # creates: fine
            "class C:\n"
            "    def patch(self, **values):\n"
            "        for attr, value in values.items():\n"
            "            setattr(config, attr, value)\n"
            "    def test(self):\n"
            "        self.patch(REPO=1, NO_SUCH_ROOT=2)\n"
            "        name = 'x'\n"
            "        mock.patch.object(v, name)\n",
            encoding="utf-8")
        inert, dynamic, _ = patch_targets(self.core, self.tests)
        text = "\n".join(p for p in inert if p.startswith("test_wp01_probe.py"))
        for expected in ("assignment config.NOT_A_SETTING",
                         "setattr changes._gone", "patch.object verify._nope",
                         "patch agents._missing", "patch(**) config.NO_SUCH_ROOT"):
            self.assertIn(expected, text)
        for fine in ("config.PORT", "verify._made", "config.REPO"):
            self.assertNotIn(fine, text)
        self.assertIn("test_wp01_probe.py::C.test -> verify.<computed>", dynamic)


if __name__ == "__main__":
    if sys.argv[1:] in ([], ["inventory"]):
        print(render(inventory()))
    else:
        unittest.main()
