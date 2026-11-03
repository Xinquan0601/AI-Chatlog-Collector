import { esc } from './core.js';

// A small, dependency-free Markdown renderer for archived chat messages.
// The input is untrusted transcript text, so every text run is HTML-escaped
// before any tag is emitted: raw HTML in a message can never become markup.

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])(\s+|$)(.*)$/;
const TASK = /^\[([ xX])\]\s+(.*)$/;
const DELIM_CELL = /^:?-+:?$/;

export function renderMarkdown(source) {
  const text = String(source === undefined || source === null ? '' : source)
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, '    ')
    .replace(/\u0001/g, '');
  if (!text.trim()) return '';
  return renderBlocks(text.split('\n'));
}

function renderBlocks(lines) {
  let html = '';
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    const fence = FENCE.exec(line);
    if (fence) { i = renderFence(lines, i, fence, function (chunk) { html += chunk; }); continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;
      html += '<h' + level + '>' + inline(heading[2]) + '</h' + level + '>';
      i += 1;
      continue;
    }

    if (HR.test(line)) {
      html += '<hr>';
      i += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const quoted = [];
      while (i < lines.length) {
        const match = QUOTE.exec(lines[i]);
        if (match) { quoted.push(match[1]); i += 1; continue; }
        if (!lines[i].trim()) { break; }
        const next = lines[i + 1];
        if (next !== undefined && !next.trim()) break;
        quoted.push(lines[i]);
        i += 1;
      }
      html += '<blockquote class="md-quote">' + renderBlocks(quoted) + '</blockquote>';
      continue;
    }

    if (LIST.test(line)) {
      const list = consumeList(lines, i);
      html += list.html;
      i = list.next;
      continue;
    }

    if (isTableStart(lines, i)) {
      const table = renderTable(lines, i);
      html += table.html;
      i = table.next;
      continue;
    }

    const paragraph = [];
    while (i < lines.length) {
      const current = lines[i];
      if (!current.trim()) break;
      if (paragraph.length && isBlockStart(lines, i)) break;
      paragraph.push(current);
      i += 1;
    }
    html += '<p>' + inline(paragraph.join('\n')).replace(/\n/g, '<br>') + '</p>';
  }
  return html;
}

function isBlockStart(lines, index) {
  const line = lines[index];
  return FENCE.test(line) || HEADING.test(line) || HR.test(line) || QUOTE.test(line) ||
    LIST.test(line) || isTableStart(lines, index);
}

function renderFence(lines, start, fence, push) {
  const marker = fence[1][0];
  const length = fence[1].length;
  const language = fence[2] || '';
  const code = [];
  let i = start + 1;
  while (i < lines.length) {
    const close = FENCE.exec(lines[i]);
    if (close && close[1][0] === marker && close[1].length >= length) { i += 1; break; }
    code.push(lines[i]);
    i += 1;
  }
  const body = '<pre class="md-code"><code>' + esc(code.join('\n')) + '</code></pre>';
  push(language
    ? '<div class="md-code-wrap"><div class="md-code-head"><span>' + esc(language) + '</span></div>' + body + '</div>'
    : body);
  return i;
}

function consumeList(lines, start) {
  const first = LIST.exec(lines[start]);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let current = null;
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      let next = i + 1;
      while (next < lines.length && !lines[next].trim()) next += 1;
      if (next >= lines.length) break;
      const after = LIST.exec(lines[next]);
      if (after && after[1].length > baseIndent && /\d/.test(after[2]) === ordered) { i = next; continue; }
      if (after && after[1].length <= baseIndent + 1 && /\d/.test(after[2]) === ordered) {
        i = next;
        continue;
      }
      break;
    }

    const match = LIST.exec(line);
    if (match) {
      const indent = match[1].length;
      if (indent < baseIndent) break;
      if (indent <= baseIndent + 1) {
        if (/\d/.test(match[2]) !== ordered) break;
        current = { head: match[4], extra: [] };
        items.push(current);
        i += 1;
        continue;
      }
      if (current) current.extra.push(line);
      i += 1;
      continue;
    }

    if (!current) break;
    current.extra.push(line.replace(/^ {0,2}/, ''));
    i += 1;
  }

  const tag = ordered ? 'ol' : 'ul';
  const startAt = ordered ? parseInt(first[2], 10) : 1;
  const body = items.map(function (item) {
    let head = item.head;
    let task = '';
    const marker = TASK.exec(head);
    if (marker) {
      task = '<span class="md-task' + (marker[1].toLowerCase() === 'x' ? ' is-done' : '') + '" aria-hidden="true"></span>';
      head = marker[2];
    }
    const inner = renderBlocks(item.extra);
    const text = inline(head);
    if (!text && !inner) return '';
    return '<li' + (task ? ' class="md-task-item"' : '') + '>' + task +
      (text ? '<span class="md-li-text">' + text + '</span>' : '') + inner + '</li>';
  }).join('');
  return {
    html: '<' + tag + (ordered && startAt !== 1 ? ' start="' + startAt + '"' : '') + '>' + body + '</' + tag + '>',
    next: i
  };
}

function splitRow(line) {
  let text = line.trim();
  if (text.charAt(0) === '|') text = text.slice(1);
  if (text.charAt(text.length - 1) === '|' && text.charAt(text.length - 2) !== '\\') text = text.slice(0, -1);
  const cells = [];
  let buffer = '';
  for (let i = 0; i < text.length; i += 1) {
    const char = text.charAt(i);
    if (char === '\\' && text.charAt(i + 1) === '|') {
      buffer += '|';
      i += 1;
      continue;
    }
    if (char === '|') {
      cells.push(buffer.trim());
      buffer = '';
      continue;
    }
    buffer += char;
  }
  cells.push(buffer.trim());
  return cells;
}

function isTableStart(lines, index) {
  if (index + 1 >= lines.length) return false;
  if (String(lines[index]).indexOf('|') < 0) return false;
  if (String(lines[index + 1]).indexOf('|') < 0) return false;
  const head = splitRow(lines[index]);
  const delimiter = splitRow(lines[index + 1]);
  if (!head.length || head.length !== delimiter.length) return false;
  return delimiter.every(function (cell) { return DELIM_CELL.test(cell); });
}

function renderTable(lines, start) {
  const head = splitRow(lines[start]);
  const alignments = splitRow(lines[start + 1]).map(function (cell) {
    const left = cell.charAt(0) === ':';
    const right = cell.charAt(cell.length - 1) === ':';
    if (left && right) return 'md-align-center';
    if (right) return 'md-align-right';
    return '';
  });
  let i = start + 2;
  const rows = [];
  while (i < lines.length && lines[i].trim() && String(lines[i]).indexOf('|') >= 0) {
    rows.push(splitRow(lines[i]));
    i += 1;
  }
  const cell = function (tag, text, align) {
    return '<' + tag + (align ? ' class="' + align + '"' : '') + '>' + inline(text) + '</' + tag + '>';
  };
  const headHtml = head.map(function (text, index) { return cell('th', text, alignments[index]); }).join('');
  const bodyHtml = rows.map(function (row) {
    return '<tr>' + head.map(function (text, index) {
      return cell('td', row[index] === undefined ? '' : row[index], alignments[index]);
    }).join('') + '</tr>';
  }).join('');
  return {
    html: '<div class="md-table-wrap"><table class="md-table"><thead><tr>' + headHtml + '</tr></thead>' +
      (bodyHtml ? '<tbody>' + bodyHtml + '</tbody>' : '') + '</table></div>',
    next: i
  };
}

function inline(text) {
  return renderInline(esc(text), true, null);
}

function renderInline(value, allowLinks, shared) {
  const stash = shared ? shared.stash : [];
  const keep = shared ? shared.keep : function (html) {
    return '\u0001' + (stash.push(html) - 1) + '\u0001';
  };

  value = value.replace(/`([^`\n]+)`/g, function (match, code) {
    return keep('<code>' + code + '</code>');
  });

  // `value` is already escaped, so captured URLs are safe to place in attributes as-is.
  if (allowLinks) {
    value = value.replace(/(!?)\[([^\]]*)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+|#[^\s)]*)\)/gi, function (match, bang, label, url) {
      const href = url.trim();
      const target = /^https?:/i.test(href) ? ' target="_blank" rel="noreferrer noopener"' : '';
      // Labels are already escaped and must not build nested <a> tags. Sharing the
      // stash keeps code spans captured before link parsing resolvable.
      const text = renderInline(label, false, { stash: stash, keep: keep });
      return keep('<a class="md-link" href="' + href + '"' + target + '>' + (text || href) + '</a>');
    });

    value = value.replace(/(^|[\s(（])((?:https?:\/\/)[^\s<)）]+)/g, function (match, lead, url) {
      const trimmed = url.replace(/[.,;:!?]+$/, '');
      const tail = url.slice(trimmed.length);
      return lead + keep('<a class="md-link" href="' + trimmed + '" target="_blank" rel="noreferrer noopener">' + trimmed + '</a>') + tail;
    });
  }

  value = emphasize(value);
  return value.replace(/\u0001(\d+)\u0001/g, function (match, index) {
    return stash[index] || '';
  });
}

function emphasize(value) {
  // Emphasis markers must hug their text (`* a *` stays literal), as in CommonMark.
  let text = value;
  text = text.replace(/\*\*\*(\S(?:[^*\n]*\S)?)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*(\S(?:[^*\n]*\S)?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^\w])__(\S(?:[^_\n]*\S)?)__(?!\w)/g, '$1<strong>$2</strong>');
  text = text.replace(/~~(\S(?:[^~\n]*\S)?)~~/g, '<del>$1</del>');
  text = text.replace(/(^|[^\w*])\*(\S(?:[^*\n]*\S)?)\*(?!\*)/g, '$1<em>$2</em>');
  text = text.replace(/(^|[^\w])_(\S(?:[^_\n]*\S)?)_(?!\w)/g, '$1<em>$2</em>');
  return text;
}
