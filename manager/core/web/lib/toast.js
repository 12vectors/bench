/* The toast pill: one message at a time, up as long as it takes to read. */
import { $ } from './dom.js';

let toastTimer = null;
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
}

export {
  toast, toastTimer
};
