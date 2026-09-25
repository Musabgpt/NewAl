package com.musab.aragpt2;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Persistent connection to newal_agent.py running inside Termux.
 *
 * The agent is started once through Termux's RUN_COMMAND service ({@link Launcher}); after
 * that every file write and command goes over one authenticated localhost socket, so no
 * Termux UI, intent round trip or temporary file is involved per execution.
 */
public final class TermuxBridge implements Closeable {
    static final int HELLO = 0x01, PING = 0x02, FOPEN = 0x10, FWRITE = 0x11, FCLOSE = 0x12,
            SYNC = 0x14, VALIDATE = 0x20, EXEC = 0x30, KILL = 0x31;
    static final int HELLO_OK = 0x81, PONG = 0x82, FCLOSED = 0x92, SYNC_RESULT = 0x94,
            VALIDATE_RESULT = 0xA0, STARTED = 0xB0, STDOUT = 0xB1, STDERR = 0xB2, EXIT = 0xB3, ERROR = 0xFF;

    /** Starts the agent process inside Termux. */
    public interface Launcher { void launch(String script) throws Exception; }

    public interface ExecListener {
        void onStarted(long pid, long startLatencyMs);
        void onOutput(boolean stderr, byte[] data);
        void onExit(ExecResult result);
    }

    public static final class ExecResult {
        public final int exitCode, signal;
        public final boolean timedOut, disconnected;
        public final long durationMs;
        public final String error;

        ExecResult(int exitCode, int signal, boolean timedOut, boolean disconnected, long durationMs, String error) {
            this.exitCode = exitCode; this.signal = signal; this.timedOut = timedOut;
            this.disconnected = disconnected; this.durationMs = durationMs; this.error = error;
        }
    }

    private final String host;
    private final int port;
    private final String token;
    private final String agentSource;
    private final Launcher launcher;
    private final AtomicInteger ids = new AtomicInteger(1);
    private final Map<Integer, CompletableFuture<byte[]>> replies = new ConcurrentHashMap<>();
    private final Map<Integer, ExecState> execs = new ConcurrentHashMap<>();
    private final Object stateLock = new Object();
    private volatile Socket socket;
    private volatile OutputStream out;
    private volatile JSONObject agentInfo;

    public TermuxBridge(String host, int port, String token, String agentSource, Launcher launcher) {
        this.host = host; this.port = port; this.token = token;
        this.agentSource = agentSource; this.launcher = launcher;
    }

    public boolean isConnected() { Socket s = socket; return s != null && !s.isClosed(); }
    public JSONObject agentInfo() { return agentInfo; }

    /** Connects to a running agent, starting it through Termux first when none answers. */
    public synchronized void ensureConnected(long timeoutMs) throws IOException {
        if (isConnected()) {
            // A socket can look open for a moment after Termux killed the agent; one
            // sub-millisecond round trip proves it is really alive.
            try { ping(1500); return; } catch (IOException e) { close(); }
        }
        if (tryConnect()) return;
        try {
            launcher.launch("NEWAL_TOKEN=" + pyString(token) + "\nNEWAL_PORT=" + port + "\n" + agentSource);
        } catch (Exception e) {
            throw new IOException("تعذر تشغيل وكيل Termux: " + e.getMessage(), e);
        }
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        long delay = 50;
        while (System.nanoTime() < deadline) {
            if (tryConnect()) return;
            try { Thread.sleep(delay); } catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
            delay = Math.min(250, delay * 2);
        }
        throw new IOException("وكيل Termux لم يستجب — تأكد من إعداد Termux مرة واحدة");
    }

    private boolean tryConnect() throws IOException {
        Socket s = new Socket();
        try {
            s.setTcpNoDelay(true);
            s.connect(new InetSocketAddress(host, port), 300);
        } catch (IOException e) {
            s.close();
            return false;
        }
        DataInputStream in = new DataInputStream(new BufferedInputStream(s.getInputStream(), 65536));
        OutputStream o = new BufferedOutputStream(s.getOutputStream(), 65536);
        byte[] t = token.getBytes(StandardCharsets.UTF_8);
        writeFrame(o, HELLO, 0, t, 0, t.length);
        s.setSoTimeout(3000);
        int op = in.readUnsignedByte();
        in.readInt();
        byte[] payload = new byte[in.readInt()];
        in.readFully(payload);
        if (op != HELLO_OK) {
            // A stale agent with another token owns the port; a fresh launch replaces it.
            s.close();
            return false;
        }
        s.setSoTimeout(0);
        try { agentInfo = new JSONObject(new String(payload, StandardCharsets.UTF_8)); }
        catch (JSONException e) { agentInfo = new JSONObject(); }
        synchronized (stateLock) {
            socket = s;
            out = o;
        }
        Thread reader = new Thread(() -> readLoop(s, in), "termux-bridge");
        reader.setDaemon(true);
        reader.start();
        return true;
    }

    private void readLoop(Socket s, DataInputStream in) {
        try {
            while (true) {
                int op = in.readUnsignedByte();
                int id = in.readInt();
                byte[] payload = new byte[in.readInt()];
                in.readFully(payload);
                dispatch(op, id, payload);
            }
        } catch (IOException ignored) {
        } finally {
            disconnect(s);
        }
    }

    private void dispatch(int op, int id, byte[] payload) {
        ExecState exec = execs.get(id);
        if (exec != null) {
            switch (op) {
                case STARTED:
                    JSONObject st = json(payload);
                    exec.listener.onStarted(st.optLong("pid"), (System.nanoTime() - exec.sentNanos) / 1_000_000);
                    return;
                case STDOUT: exec.listener.onOutput(false, payload); return;
                case STDERR: exec.listener.onOutput(true, payload); return;
                case EXIT:
                    execs.remove(id);
                    JSONObject r = json(payload);
                    exec.listener.onExit(new ExecResult(r.optInt("code", -1), r.optInt("signal"),
                            r.optBoolean("timed_out"), false, r.optLong("duration_ms"), null));
                    return;
                case ERROR:
                    execs.remove(id);
                    exec.listener.onExit(new ExecResult(-1, 0, false, false, 0, utf8(payload)));
                    return;
                default: return;
            }
        }
        CompletableFuture<byte[]> f = replies.remove(id);
        if (f == null) return;
        if (op == ERROR) f.completeExceptionally(new IOException(utf8(payload)));
        else f.complete(payload);
    }

    private void disconnect(Socket s) {
        synchronized (stateLock) {
            if (socket != s) return;
            socket = null;
            out = null;
        }
        try { s.close(); } catch (IOException ignored) {}
        IOException gone = new IOException("انقطع الاتصال بـTermux");
        for (Integer id : replies.keySet()) {
            CompletableFuture<byte[]> f = replies.remove(id);
            if (f != null) f.completeExceptionally(gone);
        }
        for (Integer id : execs.keySet()) {
            ExecState e = execs.remove(id);
            if (e != null) e.listener.onExit(new ExecResult(-1, 0, false, true, 0, gone.getMessage()));
        }
    }

    private void send(int op, int id, byte[] data, int off, int len) throws IOException {
        OutputStream o = out;
        if (o == null) throw new IOException("غير متصل بـTermux");
        synchronized (o) { writeFrame(o, op, id, data, off, len); }
    }

    private void send(int op, int id, JSONObject payload) throws IOException {
        byte[] b = payload.toString().getBytes(StandardCharsets.UTF_8);
        send(op, id, b, 0, b.length);
    }

    private static void writeFrame(OutputStream o, int op, int id, byte[] data, int off, int len) throws IOException {
        byte[] h = {(byte) op, (byte) (id >>> 24), (byte) (id >>> 16), (byte) (id >>> 8), (byte) id,
                (byte) (len >>> 24), (byte) (len >>> 16), (byte) (len >>> 8), (byte) len};
        o.write(h);
        if (len > 0) o.write(data, off, len);
        o.flush();
    }

    private JSONObject call(int op, JSONObject payload, long timeoutMs) throws IOException {
        int id = ids.getAndIncrement();
        CompletableFuture<byte[]> f = new CompletableFuture<>();
        replies.put(id, f);
        try {
            send(op, id, payload);
            return json(f.get(timeoutMs, TimeUnit.MILLISECONDS));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted", e);
        } catch (ExecutionException e) {
            throw e.getCause() instanceof IOException ? (IOException) e.getCause() : new IOException(e.getCause());
        } catch (TimeoutException e) {
            throw new IOException("انتهت مهلة Termux", e);
        } finally {
            replies.remove(id);
        }
    }

    /** Opens (truncates) a project file inside Termux for streaming writes. */
    public RemoteFile openFile(String project, String path) throws IOException {
        int id = ids.getAndIncrement();
        CompletableFuture<byte[]> done = new CompletableFuture<>();
        replies.put(id, done);
        try {
            send(FOPEN, id, new JSONObject().put("project", project).put("path", path));
        } catch (JSONException e) {
            throw new IOException(e);
        } catch (IOException e) {
            replies.remove(id);
            throw e;
        }
        return new RemoteFile(id, done);
    }

    public final class RemoteFile {
        private final int id;
        private final CompletableFuture<byte[]> done;

        RemoteFile(int id, CompletableFuture<byte[]> done) { this.id = id; this.done = done; }

        public void write(byte[] data, int off, int len) throws IOException {
            if (done.isCompletedExceptionally()) {
                try { done.join(); } catch (RuntimeException e) {
                    throw new IOException(e.getCause() != null ? e.getCause().getMessage() : e.getMessage(), e);
                }
            }
            send(FWRITE, id, data, off, len);
        }

        /** Closes the file; true when Termux's copy matches the expected SHA-256. */
        public boolean close(String expectedSha) throws IOException {
            byte[] sha = expectedSha.getBytes(StandardCharsets.US_ASCII);
            send(FCLOSE, id, sha, 0, sha.length);
            try {
                return json(done.get(10, TimeUnit.SECONDS)).optBoolean("ok");
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException("interrupted", e);
            } catch (ExecutionException e) {
                throw new IOException(e.getCause().getMessage(), e.getCause());
            } catch (TimeoutException e) {
                throw new IOException("انتهت مهلة كتابة الملف في Termux", e);
            } finally {
                replies.remove(id);
            }
        }
    }

    /** Returns the paths whose Termux copy is missing or differs from the given SHA-256 map. */
    public java.util.List<String> staleFiles(String project, Map<String, String> shaByPath) throws IOException {
        try {
            JSONObject r = call(SYNC, new JSONObject().put("project", project).put("files", new JSONObject(shaByPath)), 15000);
            java.util.List<String> stale = new java.util.ArrayList<>();
            org.json.JSONArray a = r.optJSONArray("stale");
            for (int i = 0; a != null && i < a.length(); i++) stale.add(a.getString(i));
            return stale;
        } catch (JSONException e) {
            throw new IOException(e);
        }
    }

    /** In-process syntax checks (Python/JSON) and command availability, without spawning a process. */
    public JSONObject validate(String project, Iterable<String> files, Iterable<String> commands) throws IOException {
        try {
            org.json.JSONArray f = new org.json.JSONArray(), c = new org.json.JSONArray();
            for (String s : files) f.put(s);
            for (String s : commands) c.put(s);
            return call(VALIDATE, new JSONObject().put("project", project).put("files", f).put("commands", c), 15000);
        } catch (JSONException e) {
            throw new IOException(e);
        }
    }

    public long ping(long timeoutMs) throws IOException {
        long t = System.nanoTime();
        call(PING, new JSONObject(), timeoutMs);
        return (System.nanoTime() - t) / 1_000_000;
    }

    /** Runs {@code command} with bash inside the project directory; output streams to the listener. */
    public int exec(String project, String command, Map<String, String> env, long timeoutMs, ExecListener listener) throws IOException {
        int id = ids.getAndIncrement();
        ExecState state = new ExecState(listener);
        execs.put(id, state);
        try {
            JSONObject req = new JSONObject().put("project", project).put("command", command).put("timeout_ms", timeoutMs);
            if (env != null && !env.isEmpty()) req.put("env", new JSONObject(env));
            state.sentNanos = System.nanoTime();
            send(EXEC, id, req);
        } catch (JSONException e) {
            execs.remove(id);
            throw new IOException(e);
        } catch (IOException e) {
            execs.remove(id);
            throw e;
        }
        return id;
    }

    public void kill(int execId) {
        try { send(KILL, execId, new byte[0], 0, 0); } catch (IOException ignored) {}
    }

    @Override public void close() {
        Socket s = socket;
        if (s != null) disconnect(s);
    }

    private static final class ExecState {
        final ExecListener listener;
        volatile long sentNanos;
        ExecState(ExecListener listener) { this.listener = listener; }
    }

    private static String utf8(byte[] b) { return new String(b, StandardCharsets.UTF_8); }

    private static JSONObject json(byte[] b) {
        try { return new JSONObject(utf8(b)); } catch (JSONException e) { return new JSONObject(); }
    }

    private static String pyString(String s) {
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'";
    }
}
