'use strict';
/* ── boot ─────────────────────────────────────────────────────────────── */

document.querySelectorAll('#views button').forEach(b =>
  b.addEventListener('click', () => setView(b.dataset.view)));
$('#livechip').addEventListener('click', () => setView('flight'));
/* Keyboard as well as pointer: the inspector is the one header chip that
   opens something, so it is reachable without a mouse. */
/* Every labelled Copy control — a path in the configuration check, in a
   sheet or in Settings. The value is the control's own attribute, never
   read off the page around it. Clipboard access needs a secure context,
   which a loopback board is; a refusal is said rather than swallowed. */
document.addEventListener('click', async (e) => {
  const btn = e.target.closest && e.target.closest('[data-copy]');
  if (!btn) return;
  e.stopPropagation();
  const value = btn.dataset.copy;
  let copied = false;
  try {
    await navigator.clipboard.writeText(value);
    copied = true;
  } catch (err) {
    // an unfocused document or a refused permission: the older path
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { copied = document.execCommand('copy'); } catch (e2) { copied = false; }
    ta.remove();
    btn.focus();
  }
  if (copied) {
    btn.textContent = 'Copied';
    toast('copied ' + clip(value, 60));
  } else {
    toast('the browser refused the clipboard — select the path and copy it by hand', true);
  }
});

$('#doctorchip').addEventListener('click', () => doctorSheet(null));
$('#doctorchip').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doctorSheet(null); }
});
$('#setupchip').addEventListener('click', openSetupGuide);
$('#setupchip').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSetupGuide(); }
});
$('#themebtn').addEventListener('click', () => { S.dark = !S.dark; applyTheme(); });
$('#focussel').addEventListener('change', (e) => { S.focusSid = e.target.value; render(); });
$('#refresh').addEventListener('click', loadState);
window.addEventListener('focus', loadState);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('#sheetwrap').classList.contains('open')) { closeSheet(); }
  else if (S.fileView) { S.fileView = null; renderDrawer(); }
  else if (S.selected) {
    // as the Close button does: back to the card the detail view was
    // opened from, so a keyboard user is not dropped to the top of the page
    const file = S.selected.file;
    S.selected = null;
    renderCards();
    focusCard(file);
  }
});

/* the drawer: drag its left edge to widen */
{
  const grip = $('#drawergrip'), panel = $('#drawer');
  const saved = parseInt(localStorage.getItem('bench-drawer-w'), 10);
  if (saved) panel.style.width = Math.min(saved, Math.round(innerWidth * 0.92)) + 'px';
  let dragging = false, startX = 0, startW = 0;
  grip.addEventListener('mousedown', (e) => {
    dragging = true; startX = e.clientX; startW = panel.offsetWidth;
    grip.classList.add('dragging'); e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const w = Math.min(Math.max(startW + (startX - e.clientX), 380), Math.round(innerWidth * 0.92));
    panel.style.width = w + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('dragging');
    localStorage.setItem('bench-drawer-w', panel.offsetWidth);
  });
}

/* the activity log: drag its grip to resize, scroll to read back */
{
  const grip = $('#loggrip'), body = $('#logbody');
  const clamp = (px) => Math.min(Math.max(px, 80), Math.round(innerHeight * 0.6));
  let h = clamp(parseInt(localStorage.getItem('bench-log-h'), 10) ||
    Math.round(innerHeight * 0.30));
  body.style.height = h + 'px';
  let dragging = false, startY = 0, startH = 0;
  grip.addEventListener('mousedown', (e) => {
    dragging = true; startY = e.clientY; startH = body.offsetHeight;
    grip.classList.add('dragging'); e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    h = clamp(startH + (startY - e.clientY));
    body.style.height = h + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('dragging');
    localStorage.setItem('bench-log-h', h);
  });
  body.addEventListener('scroll', () => {
    S.logStick = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
  });
}

/* the activity bar: toggle, archive dock, ⌘Z */
$('#actbtn').addEventListener('click', () => {
  S.logOpen = !S.logOpen;
  localStorage.setItem('bench-log-open', S.logOpen ? '1' : '0');
  renderBar(); renderLog();
});
$('#tray').addEventListener('click', () => {
  const n = S.state && S.state.archivedCount || 0;
  toast(n ? `${n} archived — ⌘Z undoes the last one from this board` : 'Nothing archived yet. Drag a card here.');
});
{
  const bar = $('#statusbar');
  bar.addEventListener('dragover', (e) => {
    if (!S.dragging) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (!S.dockHot) { S.dockHot = true; renderBar(); }
  });
  bar.addEventListener('dragleave', (e) => {
    if (e.relatedTarget && bar.contains(e.relatedTarget)) return;
    if (S.dockHot) { S.dockHot = false; renderBar(); }
  });
  bar.addEventListener('drop', (e) => {
    e.preventDefault();
    const d = S.dragging;
    S.dragging = null; S.dockHot = false; renderBar();
    if (d) archiveCard(d.file, d.from);
  });
}
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' &&
      !/input|textarea|select/i.test(e.target.tagName)) {
    e.preventDefault();
    unarchiveLast();
  }
});
let tickCount = 0;
setInterval(() => {
  renderChip();
  const el = $('#celapsed');
  if (el && S.focusSid) {
    const m = sessionMeta(S.focusSid);
    if (m) el.textContent = `${sessionElapsed(m)} on this`;
  }
  // safety net: refetch every 30s so a missed broadcast can never
  // leave the page lying for long
  if (++tickCount % 6 === 0) loadState();
}, 5000);

applyTheme();
loadState().then(connectStream);
// the setup guide is read once on load for the header's invitation; the
// terminal handoff names `#setup`, which opens the guide itself
fetchSetup().then(() => { if (location.hash === '#setup') openSetupGuide(); });
