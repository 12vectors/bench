/* The toast pill: one message at a time, up as long as it takes to read. */
import { $ } from './dom.js';

let toastTimer = null;
let announceTimer = null;

/* Said, not only shown (task 125). The pill is drawn for a sighted
   reader and hidden from assistive technology; the same words go to one
   of two persistent live regions beside it — `#announce` (status,
   polite) for news, `#announcealert` (alert, assertive) for an error or
   a refusal. Both regions are in the page from the start and outside
   every root a redraw demolishes, so a repaint never re-announces them.
   The region is emptied first and filled a moment later, so the same
   refusal twice is announced twice rather than read as no change. */
function announce(message, isError = false) {
  const region = $(isError ? '#announcealert' : '#announce');
  const other = $(isError ? '#announce' : '#announcealert');
  if (!region) return;
  if (other) other.textContent = '';
  region.textContent = '';
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => { region.textContent = String(message); }, 30);
}

/* A refusal that names what to do instead is longer than "07 → done/", and
   a message you cannot finish reading is a wall however politely it is
   worded. So the pill takes the shape its text needs and stays up for as
   long as that text takes to read, up to a ceiling. */
function toast(message, isError = false) {
  const el = $('#toast');
  $('#toastmsg').textContent = message;
  el.classList.toggle('err', isError);
  el.classList.toggle('wrapped', message.length > 90);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'),
                          Math.min(9000, 3200 + message.length * 28));
  announce(message, isError);
}

export {
  announce, toast, toastTimer
};
