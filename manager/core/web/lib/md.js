/* The page's small Markdown renderer. Everything it is given is untrusted. */
import { esc } from './dom.js';

// Small markdown renderer — for task files, which are hard-wrapped prose
// (a logical line spans several source lines), and for the reports agents
// file into them (task 83). No dependencies. Everything it is given is
// untrusted: the source is escaped before a single tag is written, so raw
// HTML, scripts and handler attributes arrive as text, and the only tags in
// the output are the ones written here. Nothing it emits loads anything:
// there are no images, and a link is followed only when a person clicks it.
//
// `opts.base` is the level a document's own top heading is drawn at, so
// one drawn under the page's headings nests beneath them: a report whose
// headings start at `##` has those as h4s under the drawer's h3, and the
// order below them is kept — six stays the deepest. Without it a `#` is
// an h1, as a task file's title is.
function md(src, opts = {}) {
  let hn = (n) => n;
  // the private-use characters mark held spans; nothing written may forge one
  const source = esc(String(src ?? '').replace(/\r\n?/g, '\n').replace(/[-]/g, ''));

  // a link is a link only to somewhere a card may point: the web, mail,
  // an anchor or a relative path. Briefs and reports alike are untrusted,
  // so `javascript:`, `data:` and their kin stay plain text, and `//host`
  // or `\\host` — another site wearing a relative path — does too
  const SAFE_HREF = /^(?:https?:\/\/|mailto:|#|(?![\/\\]{2})[^:\\]*$)/i;
  const inline = (t) => {
    const held = [];
    const hold = (html) => `${held.push(html) - 1}`;
    const emph = (s) => s
      .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^\w])__([^_\n]+?)__(?=[^\w]|$)/g, '$1<strong>$2</strong>')
      .replace(/(^|[\s(])\*([^*\s][^*\n]*?)\*/g, '$1<em>$2</em>')
      .replace(/(^|[\s(])_([^_\s][^_\n]*?)_(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    // code first, and held: nothing inside backticks is emphasis or a link
    t = t.replace(/`([^`]+)`/g, (m, c) => hold(`<code>${c}</code>`));
    // an image is never fetched: `![alt](src)` is drawn as a link to it.
    // A target holding a held span is no target: restored inside the href,
    // its markup would be an attribute's text (the autolink below refuses
    // held spans for the same reason)
    t = t.replace(/!?\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) =>
      SAFE_HREF.test(href.replace(/&amp;/g, '&')) && !/&amp;#/.test(href)
        ? hold(`<a href="${href}" rel="noopener noreferrer">${emph(label)}</a>`) : label);
    // a bare web address is a link too, less its trailing punctuation
    t = t.replace(/https?:\/\/(?:(?!&(?:quot|lt|gt);)[^\s<])+/g, (url) => {
      const tail = url.match(/(?:[.,;:!?'\])]|&amp;)+$/);
      const keep = tail ? url.slice(0, -tail[0].length) : url;
      return hold(`<a href="${keep}" rel="noopener noreferrer">${keep}</a>`) + (tail ? tail[0] : '');
    });
    t = emph(t);
    while (/\d+/.test(t)) t = t.replace(/(\d+)/g, (m, i) => held[+i]);
    return t;
  };

  /* Lists: group physical lines into logical items before rendering. A new
     item begins only at a marker; a line without one is continuation text
     joined to the item above with a space — that is what stops a wrapped
     item from sprouting a second bullet. A marker indented past the level
     it sits in opens a nested list, and a shallower one closes back to the
     level that fits, so children indent under their parent instead of
     flattening beside it. */
  const MARKER = /^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+(.*)$/;
  const ORDERED = /^[ \t]*(\d+)[.)]/;
  const TICK = /^\[([ xX])\]\s*(.*)$/;   // - [ ] / - [x], read-only here
  const TABLE_ROW = /^\s*\|.*\|\s*$/;
  const TABLE_SEP = /^\s*\|[\s:|-]+\|\s*$/;
  const HEADING = /^#{1,6}\s/;

  const newList = (line) => {
    const m = line.match(ORDERED);
    return { ordered: !!m, start: m ? parseInt(m[1], 10) : 1, items: [] };
  };
  const listTree = (lines) => {
    const root = newList(lines[0]);
    const stack = [{ indent: null, list: root }];
    let last = null;
    for (const line of lines) {
      const m = line.match(MARKER);
      if (!m) {                                  // continuation of the item above
        if (last && line.trim()) last.text += ' ' + line.trim();
        continue;
      }
      const indent = m[1].replace(/\t/g, '    ').length;
      const top = () => stack[stack.length - 1];
      if (top().indent === null) top().indent = indent;
      while (stack.length > 1 && indent < top().indent) stack.pop();
      if (last && indent > top().indent) {
        last.sub = newList(line);
        stack.push({ indent, list: last.sub });
      }
      last = { text: m[2], sub: null };
      top().list.items.push(last);
    }
    return root;
  };

  const listHtml = (list) => {
    const tag = list.ordered ? 'ol' : 'ul';
    const start = list.ordered && list.start !== 1 ? ` start="${list.start}"` : '';
    return `<${tag}${start}>` + list.items.map((it) => {
      const t = it.text.match(TICK);
      const body = inline(t ? t[2] : it.text) + (it.sub ? listHtml(it.sub) : '');
      if (!t) return `<li>${body}</li>`;
      // A glyph, never an input: the task file is the source of truth and
      // the drawer is not an editor. Done reads as settled, open is neutral.
      const done = t[1] !== ' ';
      return `<li class="tick${done ? ' on' : ''}">` +
        `<span class="box" role="img" aria-label="${done ? 'done' : 'not done'}">` +
        `${done ? '✓' : ''}</span>${body}</li>`;
    }).join('') + `</${tag}>`;
  };

  /* Line by line first, so a block need not be set off by a blank line to
     be what it is: reports write "**Before merging:**" with its list on
     the very next line, and a fence or a heading straight after prose. A
     fenced block is lifted out whole, every line of it kept exactly, blank
     ones included; a heading stands alone; prose meeting a list marker, a
     table or a quote ends there. The rest splits on blank lines, as it
     always did. */
  const fences = [];
  const flow = [];
  const rows = source.split('\n');
  let mode = null;                       // what the line above belongs to; null after a blank
  for (let i = 0; i < rows.length; i++) {
    const line = rows[i];
    const f = line.match(/^([ \t]{0,3})(`{3,})([^`]*)$/);
    if (f) {
      const close = new RegExp('^[ \\t]{0,3}' + f[2] + '`*[ \\t]*$');
      const body = [];
      let j = i + 1;
      while (j < rows.length && !close.test(rows[j])) body.push(rows[j++]);
      fences.push('<pre><code>' +
        body.map(l => l.startsWith(f[1]) ? l.slice(f[1].length) : l).join('\n') + '</code></pre>');
      flow.push('', `${fences.length - 1}`, '');
      i = j; mode = null;
      continue;
    }
    const prose = mode !== null;
    if (!line.trim()) mode = null;
    else if (HEADING.test(line)) { flow.push('', line, ''); mode = null; continue; }
    else if (MARKER.test(line) && mode !== 'list') { if (prose) flow.push(''); mode = 'list'; }
    else if (TABLE_ROW.test(line) && TABLE_SEP.test(rows[i + 1] || '') && mode !== 'table') {
      if (prose) flow.push(''); mode = 'table';
    } else if (mode === 'table' && !TABLE_ROW.test(line)) { flow.push(''); mode = 'text'; }
    else if (/^&gt;/.test(line) && mode !== 'quote' && mode !== 'list') {
      if (prose) flow.push(''); mode = 'quote';
    } else if (mode === null) mode = 'text';
    flow.push(line);
  }
  if (opts.base) {
    const top = Math.min(7, ...flow.filter(l => HEADING.test(l)).map(l => l.match(/^#+/)[0].length));
    hn = (n) => Math.min(6, n - top + opts.base);
  }

  const out = [];
  for (const block of flow.join('\n').split(/\n(?:[ \t]*\n)+/)) {
    const text = block.replace(/^\n+/, '').replace(/\s+$/, '');
    if (!text) continue;
    const lift = text.match(/^(\d+)$/);
    const lines = text.split('\n');
    if (lift) {
      out.push(fences[+lift[1]]);
    } else if (lines.length >= 2 && TABLE_ROW.test(lines[0]) && TABLE_SEP.test(lines[1])) {
      const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
      const head = cells(lines[0]);
      const rows = lines.slice(2).filter(l => /\|/.test(l)).map(cells);
      out.push('<div class="tablewrap"><table><thead><tr>' +
        head.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>');
    } else if (/^#{1,6}\s/.test(text)) {
      out.push(lines.map(l => {
        const m = l.match(/^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/);
        return m ? `<h${hn(m[1].length)}>${inline(m[2])}</h${hn(m[1].length)}>` : `<p>${inline(l)}</p>`;
      }).join(''));
    } else if (MARKER.test(lines[0])) {
      out.push(listHtml(listTree(lines)));
    } else if (/^&gt;/.test(text)) {
      // reflow, don't preserve the author's wrap column
      out.push('<blockquote>' + inline(text.replace(/^&gt;\s?/gm, '')).replace(/\n/g, ' ') + '</blockquote>');
    } else if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(text.trim())) {
      out.push('<hr>');
    } else {
      out.push(`<p>${inline(text).replace(/\n/g, ' ')}</p>`);
    }
  }
  return out.join('');
}

export {
  md
};
