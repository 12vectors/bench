/* Keyed updates: keep a node whose drawing did not change (task 130).

   The page used to redraw by demolition: every paint threw every card
   away and built it again, so a session event that changed nothing on the
   board still cost a thousand cards' worth of DOM, style and layout, and
   took focus, hover and a half-finished drag with it.

   A view that draws a list now asks this module instead. Each item has a
   key (a card's file) and a signature (everything its node shows, as one
   string). A node whose signature is unchanged is kept as it is — the
   same node, its listeners, its focus — and only moved if its place
   changed; a node whose signature changed is replaced by a fresh one; a
   node no item asks for is removed. There is no virtual DOM and no
   framework: the signature is the markup the builder was going to write
   anyway, so "unchanged" means "would draw the same", by construction.

   Focus is the one thing a replaced or moved node cannot keep by itself,
   so `holdFocus` puts it back: on the same node if it survived, else on
   the matching control of the node that replaced it (found by its data
   attributes, then by its place among the focusable controls), else on
   the replacement itself. */

const FOCUSABLE = 'button, a[href], input, select, textarea, [tabindex]';

/* Make `want` the element children of `parent`, in order: nodes already
   in place are not touched, out-of-place ones are moved, and every other
   child is removed. */
function placeChildren(parent, want) {
  const keep = new Set(want);
  for (const child of [...parent.children]) if (!keep.has(child)) child.remove();
  let at = parent.firstElementChild;
  for (const node of want) {
    if (node === at) { at = at.nextElementSibling; continue; }
    parent.insertBefore(node, at);
  }
}

/* How to find the focused control again inside a fresh copy of `root`. */
function focusTrail(root, el) {
  if (el === root) return { self: true };
  const data = [...el.attributes].filter(a => a.name.startsWith('data-'))
    .map(a => `[${a.name}="${CSS.escape(a.value)}"]`).join('');
  return { tag: el.tagName.toLowerCase(), data,
           index: [...root.querySelectorAll(FOCUSABLE)].indexOf(el) };
}

function followTrail(root, trail) {
  if (trail.self) return root;
  if (trail.data) {
    const hit = root.querySelector(trail.tag + trail.data);
    if (hit) return hit;
  }
  return root.querySelectorAll(FOCUSABLE)[trail.index] || root;
}

/* Run `update`, which returns a Map of replaced node → its replacement,
   and leave focus where a person would expect it afterwards. */
function holdFocus(scope, update) {
  const active = document.activeElement;
  const inside = active && active !== document.body && scope.contains(active) ? active : null;
  let owner = null, trail = null;
  if (inside) {
    owner = inside.closest('[data-key]');
    if (owner && scope.contains(owner)) trail = focusTrail(owner, inside);
  }
  const replaced = update() || new Map();
  if (!inside || document.activeElement === inside) return;
  let target = null;
  if (inside.isConnected) target = inside;
  else if (owner && replaced.has(owner)) target = followTrail(replaced.get(owner), trail);
  if (target && target.isConnected && typeof target.focus === 'function') {
    target.focus({ preventScroll: true });
  }
}

/* The nodes under `scope` that carry a key, by key: what a paint may keep. */
function keyedPool(scope, selector) {
  const pool = new Map();
  for (const node of scope.querySelectorAll(selector)) {
    if (node.dataset.key && !pool.has(node.dataset.key)) pool.set(node.dataset.key, node);
  }
  return pool;
}

/* One item's node: the pooled one when its signature is unchanged
   (`keep` refreshes what its listeners read), else a fresh one, recorded
   in `replaced` so focus can follow it. */
function keyedNode(pool, key, sig, build, keep, replaced) {
  const old = pool.get(key);
  pool.delete(key);
  if (old && old.__sig === sig) { if (keep) keep(old); return old; }
  const fresh = build();
  fresh.dataset.key = key;
  fresh.__sig = sig;
  if (old) replaced.set(old, fresh);
  return fresh;
}

/* Set an element's markup only when it differs from what it last set. */
function setHtml(el, html) {
  if (el.__html === html) return false;
  el.innerHTML = html;
  el.__html = html;
  return true;
}

/* A list's one line of words when it draws nothing, kept while it reads
   the same. */
function emptyNote(container, text) {
  const old = [...container.children].find(c => c.classList.contains('empty'));
  if (old && old.textContent === text) return old;
  const div = document.createElement('div');
  div.className = 'empty';
  div.textContent = text;
  return div;
}

export {
  emptyNote, FOCUSABLE, focusTrail, followTrail, holdFocus, keyedNode,
  keyedPool, placeChildren, setHtml
};
