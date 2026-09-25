package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;

/**
 * Applies SEARCH/REPLACE edit blocks. A block is applied only when its SEARCH text is found;
 * otherwise the edit fails with a precise message for the model instead of guessing.
 */
public final class EditApplier {
    public static final class Hunk {
        public final String search, replace;
        Hunk(String search, String replace) { this.search = search; this.replace = replace; }
    }

    public static final class Result {
        public final String content;
        public final String error;
        Result(String content, String error) { this.content = content; this.error = error; }
        public boolean ok() { return error == null; }
    }

    private EditApplier() {}

    public static List<Hunk> parse(String body) {
        List<Hunk> hunks = new ArrayList<>();
        StringBuilder search = null, replace = null;
        for (String line : body.split("\n", -1)) {
            String t = line.trim();
            if (t.startsWith("<<<<<<<")) {
                search = new StringBuilder();
                replace = null;
            } else if (search != null && replace == null && t.matches("={5,}")) {
                replace = new StringBuilder();
            } else if (replace != null && t.startsWith(">>>>>>>")) {
                hunks.add(new Hunk(search.toString(), replace.toString()));
                search = null;
                replace = null;
            } else if (replace != null) {
                replace.append(line).append('\n');
            } else if (search != null) {
                search.append(line).append('\n');
            }
        }
        return hunks;
    }

    public static Result apply(String content, List<Hunk> hunks) {
        if (hunks.isEmpty()) return new Result(content, "EDIT block has no SEARCH/REPLACE section");
        String current = content;
        for (int i = 0; i < hunks.size(); i++) {
            Hunk h = hunks.get(i);
            if (h.search.trim().isEmpty()) {
                // Empty SEARCH appends (also how a new file is created through an edit).
                String sep = current.isEmpty() || current.endsWith("\n") ? "" : "\n";
                current = current + sep + h.replace;
                continue;
            }
            int at = current.indexOf(h.search);
            if (at >= 0) {
                current = current.substring(0, at) + h.replace + current.substring(at + h.search.length());
                continue;
            }
            String relaxed = replaceIgnoringTrailingSpace(current, h);
            if (relaxed == null) {
                return new Result(content, "SEARCH block " + (i + 1) + " does not match the file exactly:\n" + h.search);
            }
            current = relaxed;
        }
        return new Result(current, null);
    }

    /** Line-by-line match that ignores trailing whitespace and a missing final newline. */
    private static String replaceIgnoringTrailingSpace(String content, Hunk h) {
        String[] lines = content.split("\n", -1);
        String[] want = stripTrailingEmpty(h.search.split("\n", -1));
        if (want.length == 0) return null;
        outer:
        for (int start = 0; start + want.length <= lines.length; start++) {
            for (int j = 0; j < want.length; j++) {
                if (!rtrim(lines[start + j]).equals(rtrim(want[j]))) continue outer;
            }
            StringBuilder b = new StringBuilder();
            for (int k = 0; k < start; k++) b.append(lines[k]).append('\n');
            b.append(h.replace);
            if (!h.replace.isEmpty() && !h.replace.endsWith("\n")) b.append('\n');
            for (int k = start + want.length; k < lines.length; k++) {
                b.append(lines[k]);
                if (k < lines.length - 1) b.append('\n');
            }
            return b.toString();
        }
        return null;
    }

    private static String[] stripTrailingEmpty(String[] a) {
        int n = a.length;
        while (n > 0 && a[n - 1].trim().isEmpty()) n--;
        String[] r = new String[n];
        System.arraycopy(a, 0, r, 0, n);
        return r;
    }

    private static String rtrim(String s) {
        int end = s.length();
        while (end > 0 && Character.isWhitespace(s.charAt(end - 1))) end--;
        return s.substring(0, end);
    }
}
