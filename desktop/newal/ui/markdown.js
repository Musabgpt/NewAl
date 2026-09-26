// A small, safe Markdown renderer: everything is escaped first, then formatting is added.
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function inline(s) {
    const codes = [];
    s = esc(s).replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank">$2</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + codes[+i] + "</code>");
  }

  function table(lines) {
    const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
    let h = "<table><thead><tr>" + cells(lines[0]).map(c => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>";
    for (const l of lines.slice(2)) h += "<tr>" + cells(l).map(c => "<td>" + c + "</td>").join("") + "</tr>";
    return h + "</tbody></table>";
  }

  function render(src) {
    const lines = src.replace(/\r/g, "").split("\n");
    let out = "", i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+#.-]*)/);
      if (fence) {
        const lang = fence[1] || "";
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;  // closing fence (or end while streaming)
        out += '<div class="codeblock" data-lang="' + esc(lang) + '"><div class="bar"><span>' + (esc(lang) || "code") +
          '</span><span><button data-act="copy">نسخ</button><button data-act="save">حفظ كملف</button></span></div><pre><code>' +
          esc(body.join("\n")) + "</code></pre></div>";
        continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      const h = line.match(/^(#{1,6})\s+(.*)/);
      if (h) { const n = Math.min(h[1].length + 1, 4); out += "<h" + n + ' dir="auto">' + inline(h[2]) + "</h" + n + ">"; i++; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { out += "<hr>"; i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        const t = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) t.push(lines[i++]);
        out += table(t);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        out += '<blockquote dir="auto">' + render(q.join("\n")) + "</blockquote>";
        continue;
      }
      const li = line.match(/^\s*([-*+]|\d+[.)])\s+/);
      if (li) {
        const ordered = /\d/.test(li[1]);
        out += ordered ? '<ol dir="auto">' : '<ul dir="auto">';
        while (i < lines.length) {
          const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)/);
          if (!m) {
            if (/^\s{2,}\S/.test(lines[i])) { out = out.replace(/<\/li>$/, " " + inline(lines[i].trim()) + "</li>"); i++; continue; }
            break;
          }
          out += "<li>" + inline(m[2]) + "</li>";
          i++;
        }
        out += ordered ? "</ol>" : "</ul>";
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s|\|)/.test(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      out += '<p dir="auto">' + para.map(inline).join("<br>") + "</p>";
    }
    return out;
  }

  window.renderMarkdown = render;
  window.escapeHtml = esc;
})();
