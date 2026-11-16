/* Opening a plans/ or reference/ document. */
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $ } from '../lib/dom.js';
import { toast } from '../lib/toast.js';
import { renderDrawer } from './detail/drawer.js';

/* ── extras / markdown ────────────────────────────────────────────────── */

async function openExtra(dir, name) {
  const url = `/files/${encodeURIComponent(dir)}/${encodeURIComponent(name)}`;
  if (name.toLowerCase().endsWith('.md')) {
    try {
      const res = await fetch(url);
      if (!res.ok) { toast(`could not read ${name}`, true); return; }
      S.fileView = { dir, name, content: await res.text() };
      S.selected = null;
      renderDrawer();
      $('#drawerbody').scrollTop = 0;
    } catch { toast(`could not read ${name}`, true); }
  } else {
    window.open(url, '_blank');  // html renders, everything else the browser handles
  }
}

export {
  openExtra
};
