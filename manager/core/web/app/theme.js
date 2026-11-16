/* Night and Daylight. */
import { S } from './state.js';
import { $ } from '../lib/dom.js';

function applyTheme() {
  document.documentElement.setAttribute('data-theme', S.dark ? 'dark' : 'light');
  $('#themebtn').textContent = S.dark ? 'Daylight' : 'Night';
  localStorage.setItem('bench-theme', S.dark ? 'dark' : 'light');
}

export {
  applyTheme
};
