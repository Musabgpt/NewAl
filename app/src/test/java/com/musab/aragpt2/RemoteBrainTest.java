package com.musab.aragpt2;

import static org.junit.Assert.*;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Test;

/** The phone using NewAl on the computer as its brain, against a fake NewAl that answers like the real one. */
public class RemoteBrainTest {
    private static final String KEY = "Zx9_kQ-2mN7pL4vB8cR1tY6w";
    private static final String READY = "{\"models\":[{\"role\":\"coder\",\"ready\":true},{\"role\":\"agent\",\"ready\":false}],"
            + "\"connectors\":{\"engine\":true},\"settings\":{\"brain_context\":65536}}";
    private Fake server;

    @After public void stop() {
        if (server != null) server.close();
    }

    private RemoteBrain brain() { return new RemoteBrain("127.0.0.1", server.port(), KEY); }

    private static List<ChatMessage> turns(ChatMessage... m) { return new ArrayList<>(Arrays.asList(m)); }

    private static ChatMessage msg(int role, String text) { return new ChatMessage(0, role, text, 0); }

    private static String event(JSONObject delta, String finish) throws Exception {
        JSONObject choice = new JSONObject().put("index", 0).put("delta", delta);
        if (finish != null) choice.put("finish_reason", finish);
        return "data: " + new JSONObject().put("choices", new JSONArray().put(choice)) + "\n\n";
    }

    private static String content(String s) throws Exception { return event(new JSONObject().put("content", s), null); }

    /**
     * A fake NewAl on a socket: /api/state with a fixed length and the phone key checked, the answer as server-sent
     * events in HTTP chunks (one per part, flushed apart).
     */
    private static final class Fake implements AutoCloseable {
        final ServerSocket socket;
        final String state;
        final int status;
        final String[] parts;
        final AtomicReference<String> body = new AtomicReference<>(), auth = new AtomicReference<>();
        /** When set, the answer waits for it after its first part (a computer still thinking). */
        volatile CountDownLatch hold;

        Fake(String state, int status, String... parts) throws IOException {
            this.socket = new ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"));
            this.state = state;
            this.status = status;
            this.parts = parts;
            Thread t = new Thread(() -> {
                while (!socket.isClosed()) {
                    try {
                        Socket s = socket.accept();
                        Thread h = new Thread(() -> handle(s));
                        h.setDaemon(true);
                        h.start();
                    } catch (IOException e) {
                        return;
                    }
                }
            });
            t.setDaemon(true);
            t.start();
        }

        int port() { return socket.getLocalPort(); }

        @Override public void close() {
            try { socket.close(); } catch (IOException ignored) { }
        }

        private static String line(InputStream in) throws IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            int c;
            while ((c = in.read()) != -1 && c != '\n') if (c != '\r') b.write(c);
            return c == -1 && b.size() == 0 ? null : new String(b.toByteArray(), StandardCharsets.ISO_8859_1);
        }

        private static void sized(OutputStream out, int code, String text) throws IOException {
            byte[] b = text.getBytes(StandardCharsets.UTF_8);
            out.write(("HTTP/1.1 " + code + " X\r\nContent-Type: application/json\r\nContent-Length: " + b.length + "\r\n\r\n")
                    .getBytes(StandardCharsets.ISO_8859_1));
            out.write(b);
            out.flush();
        }

        private void handle(Socket s) {
            try (Socket sock = s) {
                InputStream in = new BufferedInputStream(sock.getInputStream());
                String request = line(in);
                Map<String, String> headers = new HashMap<>();
                String h;
                while ((h = line(in)) != null && !h.isEmpty()) {
                    int colon = h.indexOf(':');
                    headers.put(h.substring(0, colon).trim().toLowerCase(Locale.ROOT), h.substring(colon + 1).trim());
                }
                byte[] b = new byte[Integer.parseInt(headers.containsKey("content-length") ? headers.get("content-length") : "0")];
                for (int off = 0, n; off < b.length && (n = in.read(b, off, b.length - off)) > 0; ) off += n;
                OutputStream out = sock.getOutputStream();
                String a = headers.get("authorization");
                if (request.startsWith("GET /api/state ")) {
                    sized(out, ("Bearer " + KEY).equals(a) ? 200 : 403, ("Bearer " + KEY).equals(a) ? state : "<p>امسح رمز QR</p>");
                    return;
                }
                auth.set(a);
                body.set(new String(b, StandardCharsets.UTF_8));
                if (status != 200) {
                    sized(out, status, parts[0]);
                    return;
                }
                out.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                        .getBytes(StandardCharsets.ISO_8859_1));
                boolean first = true;
                for (String p : parts) {
                    byte[] d = p.getBytes(StandardCharsets.UTF_8);
                    if (d.length == 0) continue;
                    out.write((Integer.toHexString(d.length) + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                    out.write(d);
                    out.write("\r\n".getBytes(StandardCharsets.ISO_8859_1));
                    out.flush();
                    CountDownLatch wait = hold;
                    if (first && wait != null) wait.await(10, TimeUnit.SECONDS);
                    first = false;
                }
                out.write("0\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1));
                out.flush();
            } catch (Exception ignored) {
                // the phone hung up (cancel)
            }
        }
    }

    private void serve(String state, int status, String... parts) throws IOException {
        if (server != null) server.close();
        server = new Fake(state, status, parts);
    }

    // ------------------------------------------------------------------ the link

    @Test public void readsTheLinkFromTheQrCodeOrPastedText() {
        RemoteBrain b = RemoteBrain.parse("http://192.168.1.5:8767/?k=" + KEY);
        assertEquals("192.168.1.5", b.host);
        assertEquals(8767, b.port);
        assertEquals("http://192.168.1.5:8767/?k=" + KEY, b.link());

        b = RemoteBrain.parse("افتح هاد الرابط: http://10.42.0.1:9000/?k=abcdefgh1234 وشكراً");
        assertEquals("10.42.0.1", b.host);
        assertEquals(9000, b.port);
        assertTrue(b.link().endsWith("?k=abcdefgh1234"));

        b = RemoteBrain.parse("192.168.43.1/?k=abcdefgh12345");       // typed without http:// and the port
        assertEquals("192.168.43.1", b.host);
        assertEquals(8767, b.port);

        b = RemoteBrain.parse("http://my-pc.local:8767/?lang=ar&k=" + KEY);
        assertEquals("my-pc.local", b.host);
        assertEquals(RemoteBrain.parse(b.link()).link(), b.link());

        assertNull(RemoteBrain.parse(null));
        assertNull(RemoteBrain.parse("مرحبا"));
        assertNull(RemoteBrain.parse("http://192.168.1.5:8767/"));        // no key
        assertNull(RemoteBrain.parse("http://192.168.1.5:8767/?k=abc"));  // too short to be a key
        assertNull(RemoteBrain.parse("http://192.168.1.5:99999/?k=" + KEY));
    }

    @Test public void buildsTheRequestAsThePhoneEngineWould() throws Exception {
        JSONObject body = RemoteBrain.request(turns(msg(ChatMessage.ROLE_USER, "مرحبا"), msg(ChatMessage.ROLE_SYSTEM, "أنت مساعد"),
                msg(ChatMessage.ROLE_ASSISTANT, "أهلاً"), msg(ChatMessage.ROLE_TOOL, "{\"ok\":1}"), msg(ChatMessage.ROLE_USER, "كم الساعة؟")),
                0, 0.6f, 20, RemoteBrain.THINK | RemoteBrain.CODE_MODE, "root ::= \"x\"");
        JSONArray m = body.getJSONArray("messages");
        assertEquals(5, m.length());
        assertEquals("system", m.getJSONObject(0).getString("role"));            // one system message, first
        assertEquals(RemoteBrain.CODE_SYSTEM + "\n\nأنت مساعد", m.getJSONObject(0).getString("content"));
        assertEquals("user", m.getJSONObject(1).getString("role"));
        assertEquals("assistant", m.getJSONObject(2).getString("role"));
        assertEquals("tool", m.getJSONObject(3).getString("role"));
        assertEquals("كم الساعة؟", m.getJSONObject(4).getString("content"));
        assertTrue(body.getBoolean("stream"));
        assertEquals("newal-coder", body.getString("model"));
        assertTrue(body.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"));
        assertFalse(body.has("max_tokens"));                                       // 0: until the answer ends
        assertEquals(20, body.getInt("top_k"));
        assertEquals("root ::= \"x\"", body.getString("grammar"));

        body = RemoteBrain.request(turns(msg(ChatMessage.ROLE_USER, "hi")), 96, 0f, 1, 0, null);
        assertEquals(1, body.getJSONArray("messages").length());                 // no system message at all
        assertFalse(body.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"));
        assertEquals(96, body.getInt("max_tokens"));
        assertFalse(body.has("grammar"));
    }

    // ------------------------------------------------------------------ connecting

    @Test public void checksTheComputerBeforeUsingIt() throws Exception {
        serve(READY, 200);
        RemoteBrain b = brain();
        assertNull(b.check());
        assertEquals(65536, b.contextTokens);                                       // the computer's setting

        String wrong = new RemoteBrain("127.0.0.1", server.port(), "wrong-key-123456").check();
        assertTrue(wrong, wrong.contains("المفتاح"));

        serve("{\"models\":[{\"role\":\"coder\",\"ready\":false}],\"connectors\":{\"engine\":true}}", 200);
        String noBrain = brain().check();
        assertTrue(noBrain, noBrain.contains("Qwen3.6"));

        serve("{\"models\":[{\"role\":\"coder\",\"ready\":true}],\"connectors\":{\"engine\":false}}", 200);
        String noEngine = brain().check();
        assertTrue(noEngine, noEngine.contains("llama.cpp"));

        int port = server.port();
        server.close();
        server = null;
        String off = new RemoteBrain("127.0.0.1", port, KEY).check();             // NewAl closed
        assertTrue(off, off.contains("ما قدرت أوصل"));
    }

    // ------------------------------------------------------------------ answers

    @Test public void streamsArabicThinkingAndTimings() throws Exception {
        String timings = "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],"
                + "\"timings\":{\"prompt_n\":42,\"predicted_n\":7,\"predicted_per_second\":11.5}}\n\n";
        serve(READY, 200,
                event(new JSONObject().put("reasoning_content", "المستخدم يسلّم"), null),
                event(new JSONObject().put("reasoning_content", "، أرد بلطف"), null),
                content("مرح"), content("با بك"), timings, "data: [DONE]\n\n");
        List<String> pieces = new ArrayList<>();
        GenerationResult r = brain().chat(turns(msg(ChatMessage.ROLE_USER, "مرحبا")), 0, 0.6f, 20, RemoteBrain.THINK, null, pieces::add);
        assertEquals("<think>\nالمستخدم يسلّم، أرد بلطف\n</think>\n\nمرحبا بك", r.text);
        assertEquals(r.text, String.join("", pieces));
        assertEquals(42, r.promptTokens);
        assertEquals(7, r.generatedTokens);
        assertEquals(11.5, r.tokensPerSecond, 1e-9);
        assertEquals(GenerationResult.STOP_EOG, r.stopReason);
        assertTrue(r.firstTokenMs >= 0);
        assertEquals("Bearer " + KEY, server.auth.get());
        JSONObject sent = new JSONObject(server.body.get());
        assertEquals("مرحبا", sent.getJSONArray("messages").getJSONObject(0).getString("content"));
        assertTrue(sent.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"));
        // What the chat screen shows from it.
        String[] split = ChatSession.split(r.text);
        assertEquals("المستخدم يسلّم، أرد بلطف", split[0]);
        assertEquals("مرحبا بك", split[1]);
    }

    @Test public void keepsWholeCharactersWhenPacketsSplitThem() throws Exception {
        // NewAl's own server sends events without a length or chunks (the body ends when the connection closes),
        // and here the bytes of one Arabic letter arrive in two packets.
        String events = content("السلام عليكم") + "data: [DONE]\n\n";
        byte[] body = events.getBytes(StandardCharsets.UTF_8);
        int cut = events.substring(0, events.indexOf('ا')).getBytes(StandardCharsets.UTF_8).length + 1;   // inside "ا"
        try (ServerSocket ss = new ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))) {
            Thread t = new Thread(() -> {
                try (Socket s = ss.accept()) {
                    s.getInputStream().read(new byte[65536]);           // the request (small)
                    OutputStream out = s.getOutputStream();
                    out.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
                            + "Cache-Control: no-cache\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
                    out.write(Arrays.copyOfRange(body, 0, cut));
                    out.flush();
                    Thread.sleep(80);
                    out.write(Arrays.copyOfRange(body, cut, body.length));
                    out.flush();
                } catch (Exception ignored) { }
            });
            t.start();
            GenerationResult r = new RemoteBrain("127.0.0.1", ss.getLocalPort(), KEY)
                    .chat(turns(msg(ChatMessage.ROLE_USER, "سلام")), 0, 0f, 1, 0, null, null);
            assertEquals("السلام عليكم", r.text);
            t.join(2000);
        }
    }

    @Test public void toolCallsTheServerTookApartComeBackAsText() throws Exception {
        JSONObject call1 = new JSONObject().put("index", 0).put("id", "c1").put("type", "function")
                .put("function", new JSONObject().put("name", "web_search").put("arguments", ""));
        JSONObject call2 = new JSONObject().put("index", 0).put("function", new JSONObject().put("arguments", "{\"query\": \"سعر"));
        JSONObject call3 = new JSONObject().put("index", 0).put("function", new JSONObject().put("arguments", " الذهب\"}"));
        serve(READY, 200,
                content("بدور لك"),
                event(new JSONObject().put("tool_calls", new JSONArray().put(call1)), null),
                event(new JSONObject().put("tool_calls", new JSONArray().put(call2)), null),
                event(new JSONObject().put("tool_calls", new JSONArray().put(call3)), "tool_calls"),
                "data: [DONE]\n\n");
        GenerationResult r = brain().chat(turns(msg(ChatMessage.ROLE_USER, "كم سعر الذهب؟")), 0, 0f, 1, 0, null, null);
        assertTrue(r.text, r.text.startsWith("بدور لك\n<tool_call>\n"));
        List<ChatTools.Call> calls = ChatTools.parseCalls(r.text);
        assertEquals(1, calls.size());
        assertEquals("web_search", calls.get(0).name);
        assertEquals("سعر الذهب", calls.get(0).args.optString("query"));
        assertEquals("بدور لك", ChatSession.split(r.text)[1].trim());       // the call itself is not shown
    }

    @Test public void xmlToolCallsLeftInTheTextStayAsTheyAre() throws Exception {
        // What NewAl's llama.cpp sends when the phone lists its tools in the prompt (seen live).
        String xml = "<tool_call>\n<function=currency>\n<parameter=amount>\n250\n</parameter>\n</function>\n</tool_call>";
        serve(READY, 200, content(xml.substring(0, 30)), content(xml.substring(30)), "data: [DONE]\n\n");
        GenerationResult r = brain().chat(turns(msg(ChatMessage.ROLE_USER, "250 دولار")), 0, 0f, 1, 0, null, null);
        assertEquals(xml, r.text);
        assertEquals("currency", ChatTools.parseCalls(r.text).get(0).name);
    }

    @Test public void aCutOffAnswerSaysSo() throws Exception {
        serve(READY, 200, content("واحد، اثنان"), event(new JSONObject(), "length"), "data: [DONE]\n\n");
        GenerationResult r = brain().chat(turns(msg(ChatMessage.ROLE_USER, "عد")), 5, 0f, 1, 0, null, null);
        assertEquals(GenerationResult.STOP_MAX_TOKENS, r.stopReason);
        assertEquals(5, new JSONObject(server.body.get()).getInt("max_tokens"));
    }

    @Test public void errorsFromTheComputerAreShown() throws Exception {
        serve(READY, 400, "{\"error\":{\"message\":\"the request exceeds the available context size\"}}");
        try {
            brain().chat(turns(msg(ChatMessage.ROLE_USER, "x")), 0, 0f, 1, 0, null, null);
            fail();
        } catch (IOException e) {
            assertTrue(e.getMessage(), e.getMessage().contains("400") && e.getMessage().contains("context size"));
        }
        serve(READY, 200, content("بدأ"), "data: {\"error\":{\"message\":\"model crashed\"}}\n\n");
        try {
            brain().chat(turns(msg(ChatMessage.ROLE_USER, "x")), 0, 0f, 1, 0, null, null);
            fail();
        } catch (IOException e) {
            assertTrue(e.getMessage(), e.getMessage().contains("model crashed"));
        }
    }

    @Test public void theListenerCanStopTheAnswer() throws Exception {
        serve(READY, 200, content("أ"), content("ب"), content("ج"), "data: [DONE]\n\n");
        List<String> got = new ArrayList<>();
        try {
            brain().chat(turns(msg(ChatMessage.ROLE_USER, "x")), 0, 0f, 1, 0, null, s -> {
                got.add(s);
                if (got.size() == 2) throw new IllegalStateException("أوقف");
            });
            fail();
        } catch (IllegalStateException e) {
            assertEquals(Arrays.asList("أ", "ب"), got);                   // the same as the phone's own engine
        }
    }

    @Test public void cancelStopsAWaitingAnswer() throws Exception {
        serve(READY, 200, content("أول كلمة"), content(" وبعدين"), "data: [DONE]\n\n");
        CountDownLatch release = new CountDownLatch(1);
        server.hold = release;                                         // the computer is still thinking
        RemoteBrain b = brain();
        CountDownLatch firstWord = new CountDownLatch(1);
        new Thread(() -> {
            try { firstWord.await(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
            b.cancel();
        }).start();
        long t0 = System.currentTimeMillis();
        GenerationResult r = b.chat(turns(msg(ChatMessage.ROLE_USER, "x")), 0, 0f, 1, 0, null, s -> firstWord.countDown());
        release.countDown();
        assertEquals(GenerationResult.STOP_CANCELLED, r.stopReason);
        assertEquals("أول كلمة", r.text);
        assertTrue(System.currentTimeMillis() - t0 < 5000);
    }

    @Test public void warmUpAsksForOneToken() throws Exception {
        serve(READY, 200, content("Hi"), "data: [DONE]\n\n");
        brain().warmUp();
        JSONObject sent = new JSONObject(server.body.get());
        assertEquals(1, sent.getInt("max_tokens"));
        assertFalse(sent.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"));
    }
}
