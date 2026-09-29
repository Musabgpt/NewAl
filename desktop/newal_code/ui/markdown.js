// A small, safe Markdown renderer: text is escaped first, then formatting is added.
(function () {
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function inline(s) {
    const codes = [];
    s = esc(s).replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
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
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    let out = "", i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+#.\-]*)/);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;
        out += '<pre><code data-lang="' + esc(fence[1] || "") + '">' + esc(body.join("\n")) + "</code></pre>";
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        out += table(rows);
        continue;
      }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { out += "<h" + Math.min(h[1].length + 1, 4) + ">" + inline(h[2]) + "</h" + Math.min(h[1].length + 1, 4) + ">"; i++; continue; }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]/.test(line);
        let items = "";
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          let t = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
          const box = t.match(/^\[( |x|X)\]\s+/);
          if (box) t = (box[1] === " " ? "☐ " : "☑ ") + t.slice(box[0].length);
          items += "<li>" + inline(t) + "</li>";
          i++;
        }
        out += ordered ? "<ol>" + items + "</ol>" : "<ul>" + items + "</ul>";
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        const q = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        out += "<blockquote>" + inline(q.join(" ")) + "</blockquote>";
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|[-*+]\s|\d+[.)]\s|>)/.test(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      out += "<p>" + para.map(inline).join("<br>") + "</p>";
    }
    return out;
  }

  window.renderMarkdown = render;
  window.escapeHtml = esc;
})();
