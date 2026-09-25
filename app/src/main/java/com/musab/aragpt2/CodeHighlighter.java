package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Lightweight syntax highlighting for code bubbles: finds comment, string, keyword, number and
 * block-header ranges. Pure Java (no Android types) so it is unit-testable; the UI turns the
 * ranges into colour spans.
 */
final class CodeHighlighter {
    static final int COMMENT = 0, STRING = 1, KEYWORD = 2, NUMBER = 3, HEADER = 4, FUNCTION = 5;

    static final class Span {
        final int start, end, type;
        Span(int start, int end, int type) { this.start = start; this.end = end; this.type = type; }
    }

    private static final Pattern HEADER_LINE = Pattern.compile(
            "(?m)^(?:FILE|EDIT|RUN|STDIN|TOOL|SAY):.*$|^```.*$|^(?:<<<<<<< SEARCH|=======|>>>>>>> REPLACE)$");
    private static final Pattern COMMENT_RX = Pattern.compile("(?m)(?:#|//)[^\\n]*$");
    private static final Pattern STRING_RX = Pattern.compile(
            "\"\"\"[\\s\\S]*?\"\"\"|'''[\\s\\S]*?'''|\"(?:\\\\.|[^\"\\\\\\n])*\"|'(?:\\\\.|[^'\\\\\\n])*'|`(?:\\\\.|[^`\\\\])*`");
    private static final Pattern KEYWORD_RX = Pattern.compile("\\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|"
            + "import|from|as|try|except|finally|raise|with|lambda|yield|pass|break|continue|None|True|False|self|"
            + "function|const|let|var|new|async|await|export|require|null|undefined|true|false|this|int|void|"
            + "char|float|double|include|struct|public|private|static|echo|then|fi|do|done|local)\\b");
    private static final Pattern NUMBER_RX = Pattern.compile("\\b\\d+(?:\\.\\d+)?\\b");
    private static final Pattern FUNCTION_RX = Pattern.compile("\\b([A-Za-z_]\\w*)(?=\\()");

    private CodeHighlighter() {}

    /** Non-overlapping ranges; earlier kinds (headers, comments, strings) win over later ones. */
    static List<Span> highlight(String code) {
        boolean[] taken = new boolean[code.length()];
        List<Span> spans = new ArrayList<>();
        mark(code, HEADER_LINE, HEADER, taken, spans, 0);
        // Strings and comments are resolved in one left-to-right pass so '#' inside a string
        // and quotes inside a comment are handled correctly.
        Matcher s = STRING_RX.matcher(code), c = COMMENT_RX.matcher(code);
        int pos = 0;
        while (pos < code.length()) {
            boolean hs = s.find(pos), hc = c.find(pos);
            if (!hs && !hc) break;
            Matcher m = !hc || (hs && s.start() < c.start()) ? s : c;
            int type = m == s ? STRING : COMMENT;
            if (!overlaps(taken, m.start(), m.end())) add(spans, taken, m.start(), m.end(), type);
            pos = Math.max(m.end(), m.start() + 1);
        }
        mark(code, KEYWORD_RX, KEYWORD, taken, spans, 0);
        mark(code, FUNCTION_RX, FUNCTION, taken, spans, 1);
        mark(code, NUMBER_RX, NUMBER, taken, spans, 0);
        return spans;
    }

    private static void mark(String code, Pattern p, int type, boolean[] taken, List<Span> spans, int group) {
        Matcher m = p.matcher(code);
        while (m.find()) {
            int st = m.start(group), en = m.end(group);
            if (!overlaps(taken, st, en)) add(spans, taken, st, en, type);
        }
    }

    private static boolean overlaps(boolean[] taken, int start, int end) {
        for (int i = start; i < end; i++) if (taken[i]) return true;
        return false;
    }

    private static void add(List<Span> spans, boolean[] taken, int start, int end, int type) {
        for (int i = start; i < end; i++) taken[i] = true;
        spans.add(new Span(start, end, type));
    }
}
