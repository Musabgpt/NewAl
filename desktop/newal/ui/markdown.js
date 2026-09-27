// A small, safe Markdown renderer: everything is escaped first, then formatting is added.
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // Math: $$...$$ and \[...\] as a block, \(...\) and $...$ inline (only when it looks like math, so prices like
  // "$5 and $10" stay text). Rendered by KaTeX after the HTML is in the page (renderMath).
  function math(s) {
    return s.replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => mathSpan(t, false))
      .replace(/(^|[^\\$\w])\$(?!\s)([^$\n]*?[\\^_{}=][^$\n]*?)(?<!\s)\$(?![\w$])/g, (_, a, t) => a + mathSpan(t, false));
  }
  function mathSpan(tex, block) {
    return '<span class="math' + (block ? " block" : "") + '" data-tex="' + tex.replace(/"/g, "&quot;") + '">' + tex + "</span>";
  }

  function inline(s) {
    const codes = [];
    s = esc(s).replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = math(s);
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
      const fence = line.match(/^\s*```\s*([\w+#.-]*)[ \t]*(?:(?:path|file|title|filename)\s*[=:]\s*)?["']?([\w\-./\\]+\.[A-Za-z0-9]{1,6})?/);
      if (fence) {
        const lang = fence[1] || "", file = fence[2] || "";
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;  // closing fence (or end while streaming)
        const preview = PREVIEW.test(lang) || (!lang && /^\s*<(!doctype html|html|svg)\b/i.test(body.join("\n")));
        out += '<div class="codeblock" data-lang="' + esc(lang) + '" data-file="' + esc(file) + '"><div class="bar"><span dir="ltr">' +
          (file ? "📄 " + esc(file) : (esc(lang) || "code")) +
          '</span><span>' + (preview ? '<button data-act="preview">▶ معاينة</button>' : "") +
          '<button data-act="copy">نسخ</button><button data-act="save">حفظ كملف</button></span></div><pre><code>' +
          esc(body.join("\n")) + "</code></pre></div>";
        continue;
      }
      const block = line.match(/^\s*(\$\$|\\\[)\s*(.*)$/);
      if (block) {
        // $$ ... $$ or \[ ... \] over one or several lines: one displayed equation
        const close = block[1] === "$$" ? /^(.*?)\$\$\s*$/ : /^(.*?)\\\]\s*$/;
        const tex = [];
        let rest = block[2], m = rest.match(close);
        if (m) { tex.push(m[1]); i++; }
        else {
          tex.push(rest); i++;
          while (i < lines.length && !(m = lines[i].match(close))) tex.push(lines[i++]);
          if (m) { tex.push(m[1]); i++; }
        }
        out += '<div class="mathblock">' + mathSpan(esc(tex.join("\n")), true) + "</div>";
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
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s|\||\$\$|\\\[)/.test(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      out += '<p dir="auto">' + para.map(inline).join("<br>") + "</p>";
    }
    return out;
  }

  // Code the canvas can show live: web pages, SVG, diagrams, React components, Markdown documents.
  const PREVIEW = /^(html?|svg|xml|mermaid|jsx|tsx|react|markdown|md)$/i;

  window.renderMarkdown = render;
  window.escapeHtml = esc;
  window.previewable = lang => PREVIEW.test(lang || "");
})();
