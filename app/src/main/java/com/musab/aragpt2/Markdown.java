package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A small Markdown parser for model answers (the subset chat models write): headings,
 * paragraphs, bullet / numbered lists, quotes, fenced code, tables, rules and $$ math, plus inline
 * **bold**, *italic*, `code`, ~~strike~~, [links](url) and [n] citations. It also copes with an
 * answer that is still streaming (an unclosed code fence is shown as code).
 */
final class Markdown {
    private Markdown() {}

    enum Type { HEADING, PARAGRAPH, BULLET, NUMBERED, QUOTE, CODE, TABLE, RULE }

    static final class Block {
        final Type type;
        /** Heading level, list depth (0-based) or list number. */
        final int level, number;
        final String text;
        /** Code language, or "" */
        final String lang;
        final List<List<String>> rows;
        /** A code block whose closing fence has not arrived yet. */
        final boolean open;

        Block(Type type, int level, int number, String text, String lang, List<List<String>> rows, boolean open) {
            this.type = type; this.level = level; this.number = number; this.text = text; this.lang = lang;
            this.rows = rows; this.open = open;
        }

        @Override public String toString() {
            return type + (level > 0 ? String.valueOf(level) : "") + (number > 0 ? "#" + number : "")
                    + (lang.isEmpty() ? "" : "(" + lang + ")") + (rows != null ? rows.toString() : ":" + text);
        }
    }

    private static final Pattern FENCE = Pattern.compile("^\\s*(`{3,}|~{3,})\\s*([\\w+#.-]*).*$");
    private static final Pattern HEADING = Pattern.compile("^(#{1,6})\\s+(.*?)\\s*#*\\s*$");
    private static final Pattern BULLET = Pattern.compile("^(\\s*)[-*+•]\\s+(.*)$");
    private static final Pattern NUMBERED = Pattern.compile("^(\\s*)(\\d{1,3})[.)]\\s+(.*)$");
    private static final Pattern RULE = Pattern.compile("^\\s*(?:-{3,}|\\*{3,}|_{3,})\\s*$");
    private static final Pattern TABLE_SEP = Pattern.compile("^\\s*\\|?\\s*:?-+:?\\s*(\\|\\s*:?-+:?\\s*)*\\|?\\s*$");

    static List<Block> parse(String md) {
        List<Block> out = new ArrayList<>();
        String[] lines = md.replace("\r", "").split("\n", -1);
        StringBuilder para = new StringBuilder();
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            Matcher m;
            if ((m = FENCE.matcher(line)).matches()) {
                flush(out, para);
                String fence = m.group(1);
                StringBuilder code = new StringBuilder();
                int j = i + 1;
                boolean closed = false;
                for (; j < lines.length; j++) {
                    if (lines[j].trim().startsWith(fence) && lines[j].trim().replace(fence.substring(0, 1), "").isEmpty()) { closed = true; break; }
                    code.append(code.length() == 0 && j == i + 1 ? "" : "\n").append(lines[j]);
                }
                out.add(new Block(Type.CODE, 0, 0, code.toString(), m.group(2), null, !closed));
                i = j;
                continue;
            }
            if (line.trim().startsWith("$$")) {
                flush(out, para);
                StringBuilder math = new StringBuilder(line.trim().substring(2));
                int j = i;
                if (!math.toString().trim().endsWith("$$")) {
                    for (j = i + 1; j < lines.length && !lines[j].trim().endsWith("$$"); j++) math.append('\n').append(lines[j]);
                    if (j < lines.length) math.append('\n').append(lines[j]);
                }
                out.add(new Block(Type.CODE, 0, 0, math.toString().replace("$$", "").trim(), "math", null, false));
                i = j;
                continue;
            }
            if (line.trim().isEmpty()) { flush(out, para); continue; }
            if ((m = HEADING.matcher(line)).matches()) {
                flush(out, para);
                out.add(new Block(Type.HEADING, m.group(1).length(), 0, m.group(2), "", null, false));
                continue;
            }
            if (RULE.matcher(line).matches()) { flush(out, para); out.add(new Block(Type.RULE, 0, 0, "", "", null, false)); continue; }
            if (line.contains("|") && i + 1 < lines.length && TABLE_SEP.matcher(lines[i + 1]).matches()) {
                flush(out, para);
                List<List<String>> rows = new ArrayList<>();
                rows.add(cells(line));
                int j = i + 2;
                for (; j < lines.length && lines[j].contains("|") && !lines[j].trim().isEmpty(); j++) rows.add(cells(lines[j]));
                out.add(new Block(Type.TABLE, 0, 0, "", "", rows, false));
                i = j - 1;
                continue;
            }
            if ((m = BULLET.matcher(line)).matches() && !RULE.matcher(line).matches()) {
                flush(out, para);
                out.add(new Block(Type.BULLET, m.group(1).replace("\t", "    ").length() / 2, 0, m.group(2), "", null, false));
                continue;
            }
            if ((m = NUMBERED.matcher(line)).matches()) {
                flush(out, para);
                out.add(new Block(Type.NUMBERED, m.group(1).length() / 2, Integer.parseInt(m.group(2)), m.group(3), "", null, false));
                continue;
            }
            if (line.startsWith(">")) {
                flush(out, para);
                out.add(new Block(Type.QUOTE, 0, 0, line.replaceFirst("^>\\s?", ""), "", null, false));
                continue;
            }
            // A continuation line of a list item stays with it.
            if (para.length() == 0 && !out.isEmpty() && line.startsWith("  ")) {
                Block last = out.get(out.size() - 1);
                if (last.type == Type.BULLET || last.type == Type.NUMBERED) {
                    out.set(out.size() - 1, new Block(last.type, last.level, last.number, last.text + " " + line.trim(), "", null, false));
                    continue;
                }
            }
            para.append(para.length() == 0 ? "" : "\n").append(line);
        }
        flush(out, para);
        return out;
    }

    private static void flush(List<Block> out, StringBuilder para) {
        if (para.length() == 0) return;
        out.add(new Block(Type.PARAGRAPH, 0, 0, para.toString(), "", null, false));
        para.setLength(0);
    }

    static List<String> cells(String row) {
        String r = row.trim();
        if (r.startsWith("|")) r = r.substring(1);
        if (r.endsWith("|")) r = r.substring(0, r.length() - 1);
        List<String> out = new ArrayList<>();
        for (String c : r.split("\\|", -1)) out.add(c.trim());
        return out;
    }

    // ------------------------------------------------------------------ inline

    enum Style { BOLD, ITALIC, CODE, STRIKE, LINK, CITE }

    static final class Span {
        final Style style;
        final int start, end;
        final String url;
        Span(Style style, int start, int end, String url) { this.style = style; this.start = start; this.end = end; this.url = url; }
        @Override public String toString() { return style + "[" + start + "," + end + ")" + (url == null ? "" : url); }
    }

    /** Plain text of an inline Markdown string, with its styled ranges. */
    static final class Inline {
        final String text;
        final List<Span> spans;
        Inline(String text, List<Span> spans) { this.text = text; this.spans = spans; }
    }

    private static final Pattern INLINE = Pattern.compile(
            "`([^`\\n]+)`|\\*\\*(.+?)\\*\\*|__(.+?)__|~~(.+?)~~|\\[([^\\]\\n]+)]\\((https?://[^)\\s]+)\\)|(?<![\\w*])\\*(?!\\s)([^*\\n]+?)\\*(?!\\w)|\\[(\\d{1,2})]|(https?://[^\\s)<>\\]]+)");

    static Inline inline(String s) {
        StringBuilder b = new StringBuilder();
        List<Span> spans = new ArrayList<>();
        Matcher m = INLINE.matcher(s);
        int last = 0;
        while (m.find()) {
            b.append(s, last, m.start());
            int start = b.length();
            if (m.group(1) != null) { b.append(m.group(1)); spans.add(new Span(Style.CODE, start, b.length(), null)); }
            else if (m.group(2) != null || m.group(3) != null) {
                Inline inner = inline(m.group(2) != null ? m.group(2) : m.group(3));
                b.append(inner.text);
                for (Span sp : inner.spans) spans.add(new Span(sp.style, sp.start + start, sp.end + start, sp.url));
                spans.add(new Span(Style.BOLD, start, b.length(), null));
            }
            else if (m.group(4) != null) { b.append(m.group(4)); spans.add(new Span(Style.STRIKE, start, b.length(), null)); }
            else if (m.group(5) != null) { b.append(m.group(5)); spans.add(new Span(Style.LINK, start, b.length(), m.group(6))); }
            else if (m.group(7) != null) { b.append(m.group(7)); spans.add(new Span(Style.ITALIC, start, b.length(), null)); }
            else if (m.group(8) != null) { b.append('[').append(m.group(8)).append(']'); spans.add(new Span(Style.CITE, start, b.length(), m.group(8))); }
            else { b.append(m.group(9)); spans.add(new Span(Style.LINK, start, b.length(), m.group(9))); }
            last = m.end();
        }
        b.append(s.substring(last));
        return new Inline(b.toString(), spans);
    }
}
