/* ============================================================
   CSP 训练站 · Markdown 渲染（本地原型）
   用途：新建题目页的「预览」，让老师改完题面就立刻看到学生看到的样子。

   覆盖线上 core/markdown.py 实际会用到的那部分语法：
     标题 / 段落 / 无序列表（含嵌套）/ 有序列表 / 围栏代码块 /
     表格 / 引用 / 分隔线 / 行内 code、粗体、斜体、链接
   公式（$…$、$$…$$）原样留出，由 KaTeX 在渲染后处理。

   注意：表格表头用 <td> 而不是 <th>，与线上渲染结果保持一致
   （线上题面的子任务表就是这个样式），预览才和学生在页面上看到的一样。
   ============================================================ */

(function () {
  'use strict';

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /* ---------- 行内 ---------- */
  function inline(s) {
    var codes = [];
    // 先把行内代码抽出来，免得里面的 * _ 被当成强调
    s = s.replace(/`([^`]+)`/g, function (m, c) {
      codes.push(c);
      return '\u0000C' + (codes.length - 1) + '\u0000';
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*/g, '$1<i>$2</i>');
    s = s.replace(/\u0000C(\d+)\u0000/g, function (m, i) {
      return '<code>' + codes[+i] + '</code>';
    });
    return s;
  }

  /* ---------- 列表 ---------- */
  function collectList(lines, start, ordered) {
    var pat = ordered ? /^(\s*)\d+\.\s+(.*)$/ : /^(\s*)[-*+]\s+(.*)$/;
    var entries = [];
    var i = start;
    while (i < lines.length) {
      var m = lines[i].match(pat);
      if (m) {
        entries.push({ indent: m[1].replace(/\t/g, '  ').length, text: m[2] });
        i++;
        continue;
      }
      // 续行：缩进的普通行，接到上一项后面
      if (entries.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i])
          && !/^\s*([-*+]|\d+\.)\s/.test(lines[i])) {
        entries[entries.length - 1].text += ' ' + lines[i].trim();
        i++;
        continue;
      }
      break;
    }
    return { entries: entries, next: i };
  }

  function buildList(entries, ordered) {
    var html = ordered ? '<ol>' : '<ul>';
    var i = 0;
    while (i < entries.length) {
      var cur = entries[i];
      var sub = [];
      var j = i + 1;
      while (j < entries.length && entries[j].indent > cur.indent) {
        sub.push(entries[j]);
        j++;
      }
      html += '<li>' + inline(cur.text);
      if (sub.length) html += buildList(sub, ordered);
      html += '</li>';
      i = j;
    }
    return html + (ordered ? '</ol>' : '</ul>');
  }

  /* ---------- 表格 ---------- */
  function isDivider(line) {
    return /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && /\|/.test(line);
  }

  function splitRow(line) {
    var s = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    return s.split('|').map(function (c) { return c.trim(); });
  }

  function buildTable(header, rows) {
    // 与线上 markdown.py 一致：表头也用 <td>
    var html = '<table><tr>';
    for (var c = 0; c < header.length; c++) html += '<td>' + inline(header[c]) + '</td>';
    html += '</tr>';
    for (var r = 0; r < rows.length; r++) {
      html += '<tr>';
      for (var k = 0; k < header.length; k++) {
        html += '<td>' + inline(rows[r][k] === undefined ? '' : rows[r][k]) + '</td>';
      }
      html += '</tr>';
    }
    return html + '</table>';
  }

  /* ---------- 主渲染 ---------- */
  function render(src) {
    var lines = esc(String(src || '')).replace(/\r\n?/g, '\n').split('\n');
    var out = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      // 空行
      if (!line.trim()) { i++; continue; }

      // 围栏代码块
      if (/^\s*```/.test(line)) {
        var body = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i++; }
        i++;
        out.push('<pre class="stmt-code">' + body.join('\n') + '</pre>');
        continue;
      }

      // 缩进代码块（4 空格）：要求前面是空行或开头，且不像列表项
      if (/^ {4,}\S/.test(line)
          && !/^ {4,}[-*+]\s/.test(line)
          && !/^ {4,}\d+\.\s/.test(line)
          && (i === 0 || !lines[i - 1].trim())) {
        var ind = [];
        while (i < lines.length) {
          if (/^ {4,}/.test(lines[i])) { ind.push(lines[i].replace(/^ {4}/, '')); i++; continue; }
          if (!lines[i].trim()) {
            // 空行：后面还跟着缩进行才算块内空行
            var j = i;
            while (j < lines.length && !lines[j].trim()) j++;
            if (j < lines.length && /^ {4,}/.test(lines[j])) {
              for (var b = i; b < j; b++) ind.push('');
              i = j;
              continue;
            }
          }
          break;
        }
        out.push('<pre class="stmt-code">' + ind.join('\n') + '</pre>');
        continue;
      }

      // 分隔线
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue; }

      // 标题
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lv = h[1].length;
        out.push('<h' + lv + '>' + inline(h[2].replace(/\s*#+\s*$/, '')) + '</h' + lv + '>');
        i++;
        continue;
      }

      // 表格
      if (/\|/.test(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
        var header = splitRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].trim() && /\|/.test(lines[i])) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        out.push(buildTable(header, rows));
        continue;
      }

      // 引用
      if (/^\s*>\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          q.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        out.push('<blockquote>' + render(q.join('\n')) + '</blockquote>');
        continue;
      }

      // 列表
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = collectList(lines, i, false);
        out.push(buildList(ul.entries, false));
        i = ul.next;
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = collectList(lines, i, true);
        out.push(buildList(ol.entries, true));
        i = ol.next;
        continue;
      }

      // 段落：连续非空行合成一段
      var para = [];
      while (i < lines.length && lines[i].trim()
             && !/^\s*(#{1,6}\s|```|>|[-*+]\s|\d+\.\s)/.test(lines[i])
             && !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])) {
        para.push(lines[i].trim());
        i++;
      }
      if (para.length) out.push('<p>' + inline(para.join(' ')) + '</p>');
    }

    return out.join('\n');
  }

  window.renderMarkdown = render;

  /* ---------- 渲染 + 公式 ---------- */
  window.renderStatementInto = function (el, src) {
    if (!el) return;
    el.innerHTML = render(src);
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(el, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '\\[', right: '\\]', display: true },
            { left: '$', right: '$', display: false },
            { left: '\\(', right: '\\)', display: false }
          ],
          throwOnError: false,
          ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option']
        });
      } catch (e) { /* 公式渲染失败不影响阅读 */ }
    }
  };
})();
