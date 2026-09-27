package com.musab.aragpt2;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The computer's brain: NewAl on the user's computer (Qwen3.6) answers instead of the phone's own model, over the
 * link its 📱 panel shows as a QR code (http://192.168.x.x:8767/?k=KEY: the same Wi-Fi, the phone's hotspot or USB).
 * The key goes as a bearer token. A tiny HTTP client over a socket is used so the app's policy against plain-text
 * HTTP stays on for everything else.
 *
 * <p>Answers keep the phone engine's shape: thinking arrives as {@code <think>…</think>} and tool calls the server
 * parsed come back as {@code <tool_call>} text, so ChatSession and AgentLoop work unchanged.
 */
final class RemoteBrain {
    /** The phone engine's flags (LlamaEngine.FLAG_CODE_MODE, FLAG_THINK). */
    static final int CODE_MODE = 1, THINK = 4;
    /** What the phone engine adds in code mode (llama_bridge_v2.cpp), so both brains get the same instructions. */
    static final String CODE_SYSTEM = "You are a precise offline programming assistant. "
            + "Prioritize correct, runnable Python and explain assumptions briefly. "
            + "Answer simple arithmetic exactly. Do not invent APIs. "
            + "When code is requested, return complete code.";
    static final int DEFAULT_PORT = 8767;
    private static final Pattern LINK = Pattern.compile(
            "(?:https?://)?([A-Za-z0-9.\\-]+|\\[[0-9A-Fa-f:.]+\\])(?::(\\d{1,5}))?/?[^?#\\s]*\\?(?:[^#\\s]*&)?k=([A-Za-z0-9_\\-]{8,})");

    final String host;
    final int port;
    private final String key;
    /** The brain's context in tokens, as NewAl on the computer is set (its Settings). */
    volatile int contextTokens = 32768;
    private volatile Socket live;
    private volatile boolean cancelled;

    RemoteBrain(String host, int port, String key) {
        this.host = host;
        this.port = port;
        this.key = key;
    }

    /** From the link or the QR code NewAl shows on the computer; null when the text holds none. */
    static RemoteBrain parse(String text) {
        if (text == null) return null;
        Matcher m = LINK.matcher(text.trim());
        if (!m.find()) return null;
        String host = m.group(1);
        if (host.startsWith("[")) host = host.substring(1, host.length() - 1);
        int port = m.group(2) == null ? DEFAULT_PORT : Integer.parseInt(m.group(2));
        if (port < 1 || port > 65535) return null;
        return new RemoteBrain(host, port, m.group(3));
    }

    /** The link again (to save it). */
    String link() {
        return "http://" + (host.contains(":") ? "[" + host + "]" : host) + ":" + port + "/?k=" + key;
    }

    String describe() { return host + ":" + port; }

    /**
     * Asks NewAl on the computer whether it can answer: null when it can, else what is wrong (in Arabic, for the user).
     * Also takes the brain's context size from its settings.
     */
    String check() {
        JSONObject state;
        try (Exchange x = open("GET", "/api/state", null, 10000)) {
            if (x.status == 403) return "المفتاح غلط أو تغيّر: امسح رمز QR من جديد من NewAl على الكمبيوتر (📱 الهاتف)";
            if (x.status != 200) return "NewAl على الكمبيوتر رد بـ HTTP " + x.status;
            state = new JSONObject(x.readAll(1 << 20));
        } catch (IOException e) {
            return "ما قدرت أوصل للكمبيوتر (" + describe() + "): تأكد إن NewAl شغّال عليه وإن «📱 الهاتف» مفعّل، "
                    + "وإن الهاتف والكمبيوتر على نفس الشبكة أو نقطة الاتصال أو كابل USB";
        } catch (Exception e) {
            return "رد غير مفهوم من الكمبيوتر: " + e.getMessage();
        }
        JSONObject connectors = state.optJSONObject("connectors");
        if (connectors != null && connectors.has("engine") && !connectors.optBoolean("engine")) {
            return "محرك llama.cpp مش منزّل على الكمبيوتر: افتح NewAl هناك ونزّل المحرك أولاً";
        }
        JSONArray models = state.optJSONArray("models");
        boolean ready = models == null;                       // an older NewAl without the list: just try
        for (int i = 0; models != null && i < models.length(); i++) {
            JSONObject m = models.optJSONObject(i);
            if (m != null && "coder".equals(m.optString("role")) && m.optBoolean("ready")) ready = true;
        }
        if (!ready) return "نموذج العقل Qwen3.6 مش منزّل على الكمبيوتر: نزّله من NewAl هناك (🧠 العقل والمبرمج)";
        JSONObject settings = state.optJSONObject("settings");
        int ctx = settings == null ? 0 : settings.optInt("brain_context", 0);
        if (ctx >= 2048) contextTokens = ctx;
        return null;
    }

    /**
     * Has NewAl on the computer load the brain now: the first load takes a minute or more on a laptop, better while
     * the user reads "connecting" than after the first question.
     */
    void warmUp() throws IOException {
        chat(java.util.Collections.singletonList(new ChatMessage(0, ChatMessage.ROLE_USER, "Hi", 0)), 1, 0f, 1, 0, null, null);
    }

    void cancel() {
        cancelled = true;
        Socket s = live;
        if (s != null) try { s.close(); } catch (IOException ignored) { }
    }

    /** The request body for one answer (the OpenAI chat format NewAl passes to llama.cpp). */
    static JSONObject request(List<ChatMessage> turns, int maxNewTokens, float temperature, int topK, int flags,
                              String grammar) throws Exception {
        // One system message first, as the phone engine builds it: Qwen's template refuses a system message later on.
        StringBuilder system = new StringBuilder((flags & CODE_MODE) != 0 ? CODE_SYSTEM : "");
        for (ChatMessage m : turns) {
            if (m.role != ChatMessage.ROLE_SYSTEM) continue;
            if (system.length() > 0) system.append("\n\n");
            system.append(m.text);
        }
        JSONArray messages = new JSONArray();
        if (system.length() > 0) messages.put(new JSONObject().put("role", "system").put("content", system.toString()));
        for (ChatMessage m : turns) {
            if (m.role == ChatMessage.ROLE_SYSTEM) continue;
            String role = m.role == ChatMessage.ROLE_USER ? "user" : m.role == ChatMessage.ROLE_TOOL ? "tool" : "assistant";
            messages.put(new JSONObject().put("role", role).put("content", m.text));
        }
        JSONObject body = new JSONObject()
                .put("model", "newal-coder").put("stream", true).put("messages", messages)
                .put("temperature", Math.max(0f, temperature))
                .put("chat_template_kwargs", new JSONObject().put("enable_thinking", (flags & THINK) != 0));
        if (maxNewTokens > 0) body.put("max_tokens", maxNewTokens);
        if (topK > 0) body.put("top_k", topK);
        if (grammar != null && !grammar.isEmpty()) body.put("grammar", grammar);
        return body;
    }

    /** One answer, streamed to {@code listener} as it is written; the same contract as LlamaEngine.generate. */
    GenerationResult chat(List<ChatMessage> turns, int maxNewTokens, float temperature, int topK, int flags,
                          String grammar, TextListener listener) throws IOException {
        byte[] body;
        try {
            body = request(turns, maxNewTokens, temperature, topK, flags, grammar).toString().getBytes(StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IOException("تعذّر تجهيز الطلب: " + e.getMessage(), e);
        }
        cancelled = false;
        Stream out = new Stream(listener);
        int promptTokens = 0, generated = 0, stop = GenerationResult.STOP_EOG;
        double tps = 0;
        Map<Integer, String[]> calls = new TreeMap<>();      // index → {name, arguments}
        // A long prompt on a laptop CPU can take minutes before the first word: wait up to 20 minutes between bytes.
        try (Exchange x = open("POST", "/v1/chat/completions", body, 20 * 60 * 1000)) {
            if (x.status != 200) {
                String why = x.readAll(4000);
                try { why = new JSONObject(why).getJSONObject("error").optString("message", why); } catch (Exception ignored) { }
                throw new IOException("NewAl على الكمبيوتر رد بـ HTTP " + x.status + ": " + why);
            }
            String line;
            while ((line = x.readLine()) != null) {
                if (!line.startsWith("data:")) continue;
                String data = line.substring(5).trim();
                if (data.equals("[DONE]")) break;
                JSONObject chunk;
                try { chunk = new JSONObject(data); } catch (Exception e) { continue; }
                JSONObject error = chunk.optJSONObject("error");
                if (error != null) throw new IOException("العقل على الكمبيوتر: " + error.optString("message", data));
                JSONArray choices = chunk.optJSONArray("choices");
                JSONObject c = choices != null && choices.length() > 0 ? choices.optJSONObject(0) : null;
                JSONObject delta = c == null ? null : c.optJSONObject("delta");
                if (delta != null) {
                    out.thinking(text(delta, "reasoning_content"));
                    out.answer(text(delta, "content"));
                    JSONArray tc = delta.optJSONArray("tool_calls");
                    for (int i = 0; tc != null && i < tc.length(); i++) {
                        JSONObject call = tc.optJSONObject(i);
                        if (call == null) continue;
                        String[] acc = calls.computeIfAbsent(call.optInt("index", i), k -> new String[]{"", ""});
                        JSONObject fn = call.optJSONObject("function");
                        if (fn == null) continue;
                        if (!text(fn, "name").isEmpty()) acc[0] = text(fn, "name");
                        acc[1] += text(fn, "arguments");
                    }
                }
                if (c != null && "length".equals(c.optString("finish_reason"))) stop = GenerationResult.STOP_MAX_TOKENS;
                JSONObject t = chunk.optJSONObject("timings");
                if (t != null) {
                    promptTokens = t.optInt("prompt_n", promptTokens);
                    generated = t.optInt("predicted_n", generated);
                    tps = t.optDouble("predicted_per_second", tps);
                }
            }
            // Tool calls the server took apart go back into the text, in the form the phone's parser reads.
            for (String[] call : calls.values()) {
                if (call[0].isEmpty()) continue;
                Object args;
                try { args = new JSONObject(call[1].isEmpty() ? "{}" : call[1]); } catch (Exception e) { args = call[1]; }
                out.answer("\n<tool_call>\n" + new JSONObject().put("name", call[0]).put("arguments", args) + "\n</tool_call>");
            }
        } catch (IOException e) {
            if (!cancelled) throw e;
        } catch (org.json.JSONException e) {
            throw new IOException(e.getMessage(), e);
        }
        return new GenerationResult(out.text.toString(), promptTokens, generated, out.firstMs, tps,
                System.currentTimeMillis() - out.start, cancelled ? GenerationResult.STOP_CANCELLED : stop, 0);
    }

    private static String text(JSONObject o, String name) {
        return o.isNull(name) ? "" : o.optString(name, "");
    }

    /** The answer as it grows: thinking wrapped in {@code <think>} tags, every piece passed on to the listener. */
    private static final class Stream {
        final StringBuilder text = new StringBuilder();
        final long start = System.currentTimeMillis();
        final TextListener listener;
        long firstMs = -1;
        boolean inThink;

        Stream(TextListener listener) { this.listener = listener; }

        void thinking(String s) {
            if (s.isEmpty()) return;
            if (!inThink) { emit("<think>\n"); inThink = true; }
            emit(s);
        }

        void answer(String s) {
            if (s.isEmpty()) return;
            if (inThink) { emit("\n</think>\n\n"); inThink = false; }
            emit(s);
        }

        private void emit(String s) {
            if (firstMs < 0) firstMs = System.currentTimeMillis() - start;
            text.append(s);
            if (listener == null) return;
            try {
                listener.onText(s);
            } catch (Exception e) {
                // The caller stopped the answer: the same as with the phone's own engine.
                throw new IllegalStateException(e.getMessage(), e);
            }
        }
    }

    // ------------------------------------------------------------------ a minimal HTTP/1.1 client

    private Exchange open(String method, String path, byte[] body, int readTimeoutMs) throws IOException {
        Socket s = new Socket();
        live = s;
        try {
            s.connect(new InetSocketAddress(host, port), 8000);
            s.setSoTimeout(readTimeoutMs);
            StringBuilder head = new StringBuilder();
            head.append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
                .append("Host: ").append(host.contains(":") ? "[" + host + "]" : host).append(':').append(port).append("\r\n")
                .append("Authorization: Bearer ").append(key).append("\r\n")
                .append("Accept: application/json, text/event-stream\r\n")
                .append("Connection: close\r\n");
            if (body != null) {
                head.append("Content-Type: application/json\r\nContent-Length: ").append(body.length).append("\r\n");
            }
            head.append("\r\n");
            OutputStream out = s.getOutputStream();
            out.write(head.toString().getBytes(StandardCharsets.UTF_8));
            if (body != null) out.write(body);
            out.flush();
            return new Exchange(s);
        } catch (IOException | RuntimeException e) {
            try { s.close(); } catch (IOException ignored) { }
            throw e;
        }
    }

    /** A response: the status, then the body line by line (with a length, chunked, or until the connection closes). */
    static final class Exchange implements AutoCloseable {
        private final Socket socket;
        private final InputStream in;
        final int status;
        private final boolean chunked, sized;
        private long left;                  // bytes left in the current chunk, or of a sized body
        private boolean ended;

        Exchange(Socket socket) throws IOException {
            this.socket = socket;
            this.in = new BufferedInputStream(socket.getInputStream());
            String statusLine = rawLine();
            if (statusLine == null) throw new IOException("NewAl على الكمبيوتر سكّر الاتصال");
            String[] parts = statusLine.split(" ", 3);
            int code;
            try { code = Integer.parseInt(parts.length > 1 ? parts[1].trim() : ""); }
            catch (NumberFormatException e) { throw new IOException("رد غير مفهوم: " + statusLine); }
            status = code;
            boolean isChunked = false;
            long length = -1;
            String h;
            while ((h = rawLine()) != null && !h.isEmpty()) {
                String lower = h.toLowerCase(Locale.ROOT);
                if (lower.startsWith("transfer-encoding:") && lower.contains("chunked")) isChunked = true;
                if (lower.startsWith("content-length:")) {
                    try { length = Long.parseLong(h.substring(15).trim()); } catch (NumberFormatException ignored) { }
                }
            }
            chunked = isChunked;
            sized = !chunked && length >= 0;
            if (sized) left = length;
        }

        /** One raw line (ISO-8859-1) without its line end; null at the end of the stream. */
        private String rawLine() throws IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            int c;
            while ((c = in.read()) != -1 && c != '\n') {
                if (c != '\r') b.write(c);
            }
            if (c == -1 && b.size() == 0) return null;
            return b.toString("ISO-8859-1");
        }

        /** The next byte of the body, or -1 at its end. */
        private int bodyByte() throws IOException {
            if (ended) return -1;
            if (chunked && left == 0) {
                String size = rawLine();
                while (size != null && size.trim().isEmpty()) size = rawLine();
                if (size == null) { ended = true; return -1; }
                int semi = size.indexOf(';');
                try { left = Long.parseLong((semi >= 0 ? size.substring(0, semi) : size).trim(), 16); }
                catch (NumberFormatException e) { throw new IOException("chunk غير مفهوم: " + size); }
                if (left == 0) { ended = true; return -1; }
            } else if (sized && left == 0) {
                ended = true;
                return -1;
            }
            int c = in.read();
            if (c == -1) { ended = true; return -1; }
            if (chunked || sized) left--;
            return c;
        }

        /** The next line of the body as UTF-8 (whole lines, so Arabic split across packets stays whole). */
        String readLine() throws IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            int c;
            while ((c = bodyByte()) != -1 && c != '\n') {
                if (c != '\r') b.write(c);
            }
            if (c == -1 && b.size() == 0) return null;
            return new String(b.toByteArray(), StandardCharsets.UTF_8);
        }

        /** The rest of the body (at most {@code limit} characters). */
        String readAll(int limit) throws IOException {
            StringBuilder sb = new StringBuilder();
            String l;
            while (sb.length() < limit && (l = readLine()) != null) sb.append(l).append('\n');
            return sb.toString().trim();
        }

        @Override public void close() {
            try { socket.close(); } catch (IOException ignored) { }
        }
    }
}
