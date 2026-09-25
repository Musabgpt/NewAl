package com.musab.aragpt2;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One generated project. Files are written through to two places as they stream:
 * the app's private mirror (source of truth for prompts and crash recovery) and the Termux
 * copy that is executed. Each file's SHA-256 is verified on both sides when it is closed.
 */
public final class ProjectWorkspace {
    public enum FileStatus { WRITING, COMPLETE, INCOMPLETE }

    public static final class Entry {
        public final String path;
        public String sha = "";
        public long size;
        public FileStatus status = FileStatus.WRITING;
        public int version;
        public int changedInAttempt;

        Entry(String path) { this.path = path; }
    }

    static final String META = ".newal";

    public final String id;
    public final File dir;
    private final Map<String, Entry> files = new LinkedHashMap<>();
    private volatile TermuxBridge bridge;
    private boolean remoteDirty = true;

    // Journal: enough to resume the loop after a crash without regenerating finished work.
    public String request = "";
    public String state = "IDLE";
    public int attempt;
    public String runCommandOverride;
    public String installedRequirementsSha = "";
    /** Sample keyboard input the model supplied for an interactive program, or null. */
    public String stdinInput;
    public String agentId = "code", skillId = "";
    public final List<String> attemptNotes = new ArrayList<>();

    private ProjectWorkspace(String id, File dir) {
        this.id = id;
        this.dir = dir;
    }

    public static ProjectWorkspace create(File root) throws IOException {
        long stamp = System.currentTimeMillis();
        File dir = new File(root, "p" + stamp);
        while (dir.exists()) dir = new File(root, "p" + (++stamp)); // unique even within one millisecond
        String id = dir.getName();
        if (!new File(dir, META).mkdirs()) throw new IOException("تعذر إنشاء مجلد المشروع");
        ProjectWorkspace w = new ProjectWorkspace(id, dir);
        w.saveJournal();
        return w;
    }

    /** Most recently created project under root, or null. */
    public static ProjectWorkspace openLatest(File root) {
        File[] dirs = root.listFiles(f -> f.isDirectory() && f.getName().startsWith("p"));
        if (dirs == null || dirs.length == 0) return null;
        File latest = dirs[0];
        for (File d : dirs) if (d.getName().compareTo(latest.getName()) > 0) latest = d;
        ProjectWorkspace w = new ProjectWorkspace(latest.getName(), latest);
        try {
            w.loadJournal();
        } catch (IOException | JSONException e) {
            return null;
        }
        return w;
    }

    public void attach(TermuxBridge b) {
        bridge = b;
        remoteDirty = true;
    }

    public synchronized List<Entry> entries() { return new ArrayList<>(files.values()); }
    public synchronized Entry entry(String path) { return files.get(path); }
    public synchronized boolean isEmpty() { return files.isEmpty(); }

    public synchronized Map<String, String> completeShas() {
        Map<String, String> m = new LinkedHashMap<>();
        for (Entry e : files.values()) if (e.status == FileStatus.COMPLETE) m.put(e.path, e.sha);
        return m;
    }

    public String read(String path) throws IOException {
        return new String(readBytes(new File(dir, checkPath(path))), StandardCharsets.UTF_8);
    }

    /** Starts streaming a whole-file write. The previous version is kept until the write completes. */
    public FileWriter beginFile(String path, int attemptNo) throws IOException {
        String p = checkPath(path);
        File target = new File(dir, p);
        File parent = target.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) throw new IOException("تعذر إنشاء " + parent);
        File backup = null;
        if (target.isFile()) {
            backup = new File(dir, META + "/history/a" + attemptNo + "/" + p);
            if (!backup.isFile()) {
                File bp = backup.getParentFile();
                if (bp != null) bp.mkdirs();
                writeBytes(backup, readBytes(target), false);
            }
        }
        Entry e;
        synchronized (this) {
            e = files.get(p);
            if (e == null) { e = new Entry(p); files.put(p, e); }
            e.status = FileStatus.WRITING;
            e.changedInAttempt = attemptNo;
        }
        TermuxBridge.RemoteFile remote = null;
        TermuxBridge b = bridge;
        if (b != null && b.isConnected()) {
            try { remote = b.openFile(id, p); } catch (IOException ex) { remoteDirty = true; }
        } else {
            remoteDirty = true;
        }
        return new FileWriter(e, target, backup, new FileOutputStream(target, false), remote);
    }

    public final class FileWriter {
        public final Entry entry;
        private final File target, backup;
        private final FileOutputStream out;
        private final MessageDigest digest = sha256();
        private TermuxBridge.RemoteFile remote;
        private long bytes;
        public long writeNanos;

        FileWriter(Entry entry, File target, File backup, FileOutputStream out, TermuxBridge.RemoteFile remote) {
            this.entry = entry; this.target = target; this.backup = backup; this.out = out; this.remote = remote;
        }

        public void write(String text) throws IOException {
            if (text.isEmpty()) return;
            long t = System.nanoTime();
            byte[] b = text.getBytes(StandardCharsets.UTF_8);
            out.write(b);
            digest.update(b);
            bytes += b.length;
            if (remote != null) {
                try { remote.write(b, 0, b.length); }
                catch (IOException ex) { remote = null; remoteDirty = true; }
            }
            writeNanos += System.nanoTime() - t;
        }

        public long bytes() { return bytes; }

        /**
         * Finishes the file. An incomplete rewrite of an existing file is rolled back to the
         * previous version, so a cut-off generation never replaces working code.
         */
        public void close(boolean complete) throws IOException {
            long t = System.nanoTime();
            out.getFD().sync();
            out.close();
            String sha = hex(digest.digest());
            boolean remoteOk = false;
            if (remote != null) {
                try { remoteOk = remote.close(sha); } catch (IOException ex) { remoteOk = false; }
            }
            if (!remoteOk) remoteDirty = true;
            if (!complete && backup != null && backup.isFile()) {
                byte[] previous = readBytes(backup);
                writeBytes(target, previous, true);
                sha = hex(sha256().digest(previous));
                remoteDirty = true;
                complete = true;
                synchronized (ProjectWorkspace.this) { entry.size = previous.length; }
            } else {
                synchronized (ProjectWorkspace.this) { entry.size = bytes; }
            }
            synchronized (ProjectWorkspace.this) {
                entry.sha = sha;
                entry.status = complete ? FileStatus.COMPLETE : FileStatus.INCOMPLETE;
                entry.version++;
            }
            writeNanos += System.nanoTime() - t;
            saveJournal();
        }
    }

    /** Applies a SEARCH/REPLACE edit and writes the result. Returns null on success or the reason it failed. */
    public String applyEdit(String path, String body, int attemptNo) throws IOException {
        String p = checkPath(path);
        List<EditApplier.Hunk> hunks = EditApplier.parse(body);
        File target = new File(dir, p);
        String current = target.isFile() ? read(p) : "";
        if (!target.isFile()) {
            for (EditApplier.Hunk h : hunks) {
                if (!h.search.trim().isEmpty()) return "EDIT for " + p + " but the file does not exist";
            }
        }
        EditApplier.Result r = EditApplier.apply(current, hunks);
        if (!r.ok()) return p + ": " + r.error;
        if (r.content.equals(current) && target.isFile()) return null;
        FileWriter w = beginFile(p, attemptNo);
        w.write(r.content);
        w.close(true);
        return null;
    }

    /** Makes the Termux copy identical to the mirror (after reconnects or failed remote writes). */
    public void syncRemote() throws IOException {
        TermuxBridge b = bridge;
        if (b == null || !b.isConnected()) throw new IOException("غير متصل بـTermux");
        if (!remoteDirty) return;
        for (String p : b.staleFiles(id, completeShas())) {
            byte[] data = readBytes(new File(dir, p));
            TermuxBridge.RemoteFile rf = b.openFile(id, p);
            rf.write(data, 0, data.length);
            if (!rf.close(hex(sha256().digest(data)))) throw new IOException("فشل التحقق من " + p + " في Termux");
        }
        remoteDirty = false;
    }

    /**
     * Reverts the last attempt: files it changed get their previous content back, files it
     * created are deleted (here and in Termux). Returns the paths that were reverted.
     */
    public List<String> undoLastAttempt() throws IOException {
        List<String> reverted = new ArrayList<>();
        int a = attempt;
        if (a <= 0) return reverted;
        File history = new File(dir, META + "/history/a" + a);
        for (Entry e : entries()) {
            if (e.changedInAttempt != a) continue;
            File backup = new File(history, e.path);
            File target = new File(dir, e.path);
            if (backup.isFile()) {
                byte[] previous = readBytes(backup);
                writeBytes(target, previous, true);
                synchronized (this) {
                    e.sha = hex(sha256().digest(previous));
                    e.size = previous.length;
                    e.status = FileStatus.COMPLETE;
                    e.changedInAttempt = a - 1;
                }
            } else {
                target.delete();
                synchronized (this) { files.remove(e.path); }
                TermuxBridge b = bridge;
                if (b != null && b.isConnected()) {
                    try { b.deleteFile(id, e.path); } catch (IOException ignored) {}
                }
            }
            reverted.add(e.path);
        }
        attempt = a - 1;
        remoteDirty = true;
        saveJournal();
        return reverted;
    }

    public File runLog(int attemptNo, String stream) {
        File runs = new File(dir, META + "/runs");
        runs.mkdirs();
        return new File(runs, "a" + attemptNo + "." + stream);
    }

    /** Appends one structured log record (JSON Lines). */
    public void log(JSONObject record) {
        try {
            record.put("t", System.currentTimeMillis()).put("project", id);
            writeBytes(new File(dir, META + "/log.jsonl"),
                    (record.toString() + "\n").getBytes(StandardCharsets.UTF_8), false, true);
        } catch (JSONException | IOException ignored) {
        }
    }

    public synchronized void saveJournal() throws IOException {
        try {
            JSONObject j = new JSONObject()
                    .put("request", request).put("state", state).put("attempt", attempt)
                    .put("run", runCommandOverride == null ? JSONObject.NULL : runCommandOverride)
                    .put("installed_requirements", installedRequirementsSha)
                    .put("stdin", stdinInput == null ? JSONObject.NULL : stdinInput)
                    .put("agent", agentId).put("skill", skillId)
                    .put("notes", new JSONArray(attemptNotes));
            JSONArray fa = new JSONArray();
            for (Entry e : files.values()) {
                fa.put(new JSONObject().put("path", e.path).put("sha", e.sha).put("size", e.size)
                        .put("status", e.status.name()).put("version", e.version).put("attempt", e.changedInAttempt));
            }
            j.put("files", fa);
            writeBytes(new File(dir, META + "/state.json"), j.toString().getBytes(StandardCharsets.UTF_8), true);
        } catch (JSONException e) {
            throw new IOException(e);
        }
    }

    private synchronized void loadJournal() throws IOException, JSONException {
        JSONObject j = new JSONObject(new String(readBytes(new File(dir, META + "/state.json")), StandardCharsets.UTF_8));
        request = j.optString("request");
        state = j.optString("state", "IDLE");
        attempt = j.optInt("attempt");
        runCommandOverride = j.isNull("run") ? null : j.optString("run", null);
        installedRequirementsSha = j.optString("installed_requirements");
        stdinInput = j.isNull("stdin") ? null : j.optString("stdin", null);
        agentId = j.optString("agent", "code");
        skillId = j.optString("skill", "");
        JSONArray notes = j.optJSONArray("notes");
        for (int i = 0; notes != null && i < notes.length(); i++) attemptNotes.add(notes.getString(i));
        JSONArray fa = j.optJSONArray("files");
        for (int i = 0; fa != null && i < fa.length(); i++) {
            JSONObject o = fa.getJSONObject(i);
            Entry e = new Entry(o.getString("path"));
            e.sha = o.optString("sha");
            e.size = o.optLong("size");
            e.status = FileStatus.valueOf(o.optString("status", "INCOMPLETE"));
            e.version = o.optInt("version");
            e.changedInAttempt = o.optInt("attempt");
            // A file that was still being written when the app died is not trusted.
            if (e.status == FileStatus.WRITING) e.status = FileStatus.INCOMPLETE;
            files.put(e.path, e);
        }
    }

    static String checkPath(String path) throws IOException {
        String p = path == null ? "" : path.trim().replace('\\', '/');
        while (p.startsWith("./")) p = p.substring(2);
        if (p.isEmpty() || p.startsWith("/") || p.startsWith(META) || p.contains("\0")) throw new IOException("مسار غير مسموح: " + path);
        for (String part : p.split("/")) {
            if (part.isEmpty() || part.equals("..") || part.equals(".")) throw new IOException("مسار غير مسموح: " + path);
        }
        return p;
    }

    static byte[] readBytes(File f) throws IOException {
        try (FileInputStream in = new FileInputStream(f)) {
            long len = f.length();
            if (len > Integer.MAX_VALUE) throw new IOException("file too large");
            byte[] b = new byte[(int) len];
            int off = 0;
            while (off < b.length) {
                int n = in.read(b, off, b.length - off);
                if (n < 0) break;
                off += n;
            }
            return off == b.length ? b : java.util.Arrays.copyOf(b, off);
        }
    }

    private static void writeBytes(File f, byte[] data, boolean atomic) throws IOException {
        writeBytes(f, data, atomic, false);
    }

    private static void writeBytes(File f, byte[] data, boolean atomic, boolean append) throws IOException {
        File target = atomic ? new File(f.getPath() + ".tmp") : f;
        try (FileOutputStream out = new FileOutputStream(target, append)) {
            out.write(data);
            if (atomic) out.getFD().sync();
        }
        if (atomic && !target.renameTo(f)) throw new IOException("تعذر حفظ " + f.getName());
    }

    static MessageDigest sha256() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    static String hex(byte[] b) {
        char[] c = new char[b.length * 2];
        final char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < b.length; i++) {
            c[i * 2] = digits[(b[i] >> 4) & 0xF];
            c[i * 2 + 1] = digits[b[i] & 0xF];
        }
        return new String(c);
    }

    /** Removes files the model never finished and that have no earlier version. */
    public synchronized void dropIncomplete() {
        Iterator<Entry> it = files.values().iterator();
        while (it.hasNext()) {
            Entry e = it.next();
            if (e.status == FileStatus.INCOMPLETE) {
                new File(dir, e.path).delete();
                it.remove();
            }
        }
    }
}
