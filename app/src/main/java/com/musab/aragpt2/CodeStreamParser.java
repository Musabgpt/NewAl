package com.musab.aragpt2;

import java.io.IOException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Incremental parser for model output. File content is forwarded to the sink as soon as it
 * arrives; the only text held back is a partial line that could still turn out to be the
 * closing fence (at most a few backticks), so nothing is lost, duplicated or added.
 *
 * Recognised output:
 * <pre>
 * FILE: path/name.ext        EDIT: path/name.ext        RUN: command
 * ```lang                    ```
 * whole file                 &lt;&lt;&lt;&lt;&lt;&lt;&lt; SEARCH
 * ```                        old lines
 *                            =======
 *                            new lines
 *                            &gt;&gt;&gt;&gt;&gt;&gt;&gt; REPLACE
 *                            ```
 * </pre>
 * Prose outside blocks is ignored; it never reaches a file.
 */
public final class CodeStreamParser {
    public interface Sink {
        void onFileStart(String path) throws IOException;
        void onFileData(String text) throws IOException;
        /** complete=false when the stream ended before the closing fence. */
        void onFileEnd(boolean complete) throws IOException;
        void onEdit(String path, String editBody, boolean complete) throws IOException;
        void onRunCommand(String command);
    }

    /** Chooses a file for a fenced block that has no name, or null to ignore the block. */
    public interface UnnamedBlockResolver { String pathFor(String language); }

    static final Pattern HEADER = Pattern.compile(
            "^[\\s#>*_`-]*(file|edit|path)\\s*[:：]\\s*[`*_\"']*\\s*([^`*\\s\"']+)\\s*[`*_\"']*\\s*$",
            Pattern.CASE_INSENSITIVE);
    static final Pattern RUN = Pattern.compile("^[\\s#>*_-]*run\\s*[:：]\\s*`*\\s*(.+?)\\s*`*\\s*$",
            Pattern.CASE_INSENSITIVE);
    static final Pattern FENCE_OPEN = Pattern.compile("^ {0,3}(`{3,})\\s*([^`]*)$");
    static final Pattern PATH_TOKEN = Pattern.compile("^[\\w./-]*\\w\\.[A-Za-z0-9]+$");
    static final String SEARCH_MARK = "<<<<<<<";

    private enum Mode { OUTSIDE, FILE, EDIT, RAW_EDIT, SKIP }

    private final Sink sink;
    private final UnnamedBlockResolver resolver;
    private final StringBuilder line = new StringBuilder();
    private final StringBuilder editBody = new StringBuilder();
    private Mode mode = Mode.OUTSIDE;
    private String pendingPath;
    private boolean pendingIsEdit;
    private String blockPath;
    private int fenceLen;
    private boolean firstLine;
    private boolean lineFlushed;
    private int filesWritten, editsSeen, incompleteBlocks;

    public CodeStreamParser(Sink sink, UnnamedBlockResolver resolver) {
        this.sink = sink;
        this.resolver = resolver;
    }

    public int filesWritten() { return filesWritten; }
    public int editsSeen() { return editsSeen; }
    public int incompleteBlocks() { return incompleteBlocks; }

    public void feed(CharSequence chunk) throws IOException {
        for (int i = 0, n = chunk.length(); i < n; ) {
            if (mode == Mode.FILE && !firstLine) {
                // Fast path: stream everything up to the next newline unless the current line
                // could still be a closing fence.
                int nl = indexOf(chunk, '\n', i);
                int end = nl < 0 ? n : nl;
                if (lineFlushed) {
                    if (end > i) sink.onFileData(chunk.subSequence(i, end).toString());
                } else {
                    line.append(chunk, i, end);
                    if (!couldBeFence(line)) {
                        sink.onFileData(line.toString());
                        line.setLength(0);
                        lineFlushed = true;
                    }
                }
                if (nl < 0) return;
                i = nl + 1;
                if (lineFlushed) {
                    sink.onFileData("\n");
                } else if (isClosingFence(line)) {
                    line.setLength(0);
                    endFile(true);
                    continue;
                } else {
                    line.append('\n');
                    sink.onFileData(line.toString());
                }
                line.setLength(0);
                lineFlushed = false;
                continue;
            }
            char c = chunk.charAt(i++);
            if (c == '\n') {
                String l = line.toString();
                line.setLength(0);
                onLine(l);
            } else {
                line.append(c);
            }
        }
    }

    /** Ends the stream; an unterminated block is reported as incomplete, never silently dropped. */
    public void finish() throws IOException {
        if (mode == Mode.FILE) {
            if (firstLine) {
                // Cut off before the first line completed: the block is incomplete. Opening the
                // target now would truncate an existing file for nothing.
                incompleteBlocks++;
                if (line.length() == 0 || line.toString().trim().startsWith(SEARCH_MARK)) {
                    line.setLength(0);
                    mode = Mode.OUTSIDE;
                    firstLine = false;
                    return;
                }
                startFile();
                lineFlushed = false;
            }
            if (line.length() > 0 && !lineFlushed) sink.onFileData(line.toString());
            line.setLength(0);
            endFile(false);
            return;
        }
        if (line.length() > 0) {
            String l = line.toString();
            line.setLength(0);
            onLine(l);
        }
        if (mode == Mode.EDIT || mode == Mode.RAW_EDIT) {
            boolean complete = mode == Mode.RAW_EDIT && editBody.toString().trim().endsWith("REPLACE");
            emitEdit(complete);
        }
        mode = Mode.OUTSIDE;
    }

    private void onLine(String l) throws IOException {
        switch (mode) {
            case FILE: // only reached for the first line of a block; the file is not opened yet
                firstLine = false;
                if (l.trim().startsWith(SEARCH_MARK)) {
                    mode = Mode.EDIT;
                    editBody.setLength(0);
                    editBody.append(l).append('\n');
                    return;
                }
                startFile();
                if (isClosingFence(l)) endFile(true);
                else sink.onFileData(l + "\n");
                return;
            case EDIT:
                if (isClosingFence(l)) { emitEdit(true); mode = Mode.OUTSIDE; }
                else editBody.append(l).append('\n');
                return;
            case RAW_EDIT:
                String t = l.trim();
                boolean afterReplace = editBody.length() > 0 && lastLine(editBody).startsWith(">>>>>>>");
                if (afterReplace && !t.startsWith(SEARCH_MARK) && !t.isEmpty()) {
                    emitEdit(true);
                    mode = Mode.OUTSIDE;
                    onLine(l);
                } else if (!(afterReplace && t.isEmpty())) {
                    editBody.append(l).append('\n');
                }
                return;
            case SKIP:
                if (isClosingFence(l)) mode = Mode.OUTSIDE;
                return;
            default:
                onOutsideLine(l);
        }
    }

    private void onOutsideLine(String l) throws IOException {
        Matcher fence = FENCE_OPEN.matcher(l);
        if (fence.matches()) {
            fenceLen = fence.group(1).length();
            String info = fence.group(2).trim();
            String path = pendingPath != null ? pendingPath : pathFromInfo(info);
            boolean edit = pendingPath != null && pendingIsEdit;
            pendingPath = null;
            if (path == null && resolver != null) path = resolver.pathFor(language(info));
            if (path == null) { mode = Mode.SKIP; return; }
            blockPath = path;
            if (edit) {
                mode = Mode.EDIT;
                editBody.setLength(0);
            } else {
                mode = Mode.FILE;
                firstLine = true;
                lineFlushed = false;
            }
            return;
        }
        String t = l.trim();
        if (pendingPath != null && pendingIsEdit && t.startsWith(SEARCH_MARK)) {
            blockPath = pendingPath;
            pendingPath = null;
            mode = Mode.RAW_EDIT;
            editBody.setLength(0);
            editBody.append(l).append('\n');
            return;
        }
        Matcher h = HEADER.matcher(l);
        if (h.matches()) {
            pendingPath = h.group(2);
            pendingIsEdit = h.group(1).equalsIgnoreCase("edit");
            return;
        }
        Matcher run = RUN.matcher(l);
        if (run.matches()) sink.onRunCommand(run.group(1));
    }

    private void startFile() throws IOException {
        filesWritten++;
        sink.onFileStart(blockPath);
    }

    private void endFile(boolean complete) throws IOException {
        if (!complete) incompleteBlocks++;
        mode = Mode.OUTSIDE;
        firstLine = false;
        lineFlushed = false;
        sink.onFileEnd(complete);
    }

    private void emitEdit(boolean complete) throws IOException {
        editsSeen++;
        if (!complete) incompleteBlocks++;
        sink.onEdit(blockPath, editBody.toString(), complete);
        editBody.setLength(0);
    }

    /** True while the partial line could still become a closing fence (``` with optional indentation). */
    private boolean couldBeFence(CharSequence s) {
        int i = 0, n = s.length();
        while (i < n && i < 3 && s.charAt(i) == ' ') i++;
        while (i < n && s.charAt(i) == '`') i++;
        while (i < n && (s.charAt(i) == ' ' || s.charAt(i) == '\t' || s.charAt(i) == '\r')) i++;
        return i == n;
    }

    private boolean isClosingFence(CharSequence s) {
        String t = s.toString();
        int lead = 0;
        while (lead < t.length() && lead < 3 && t.charAt(lead) == ' ') lead++;
        t = t.substring(lead).trim();
        if (t.length() < fenceLen) return false;
        for (int i = 0; i < t.length(); i++) if (t.charAt(i) != '`') return false;
        return true;
    }

    private static String pathFromInfo(String info) {
        for (String token : info.split("[\\s:]+")) {
            if (PATH_TOKEN.matcher(token).matches()) return token;
        }
        return null;
    }

    private static String language(String info) {
        String[] parts = info.split("[\\s:{]+");
        return parts.length == 0 ? "" : parts[0].toLowerCase(java.util.Locale.ROOT);
    }

    private static String lastLine(StringBuilder b) {
        int end = b.length();
        if (end > 0 && b.charAt(end - 1) == '\n') end--;
        int start = b.lastIndexOf("\n", end - 1) + 1;
        return b.substring(start, end).trim();
    }

    private static int indexOf(CharSequence s, char c, int from) {
        for (int i = from, n = s.length(); i < n; i++) if (s.charAt(i) == c) return i;
        return -1;
    }
}
