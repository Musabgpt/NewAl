package dev.newal.code.lite;

import android.content.Context;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * The phone's side of the agent's `phone` tool: a small HTTP server on 127.0.0.1 that NewAl Code (in the app, or in
 * Termux) posts actions to, as JSON, with the app's key. Other apps on the phone can reach 127.0.0.1 as well, so a
 * request without the key is refused.
 */
final class PhoneServer implements Runnable {
    static final int PORT = 8793;
    private static PhoneServer running;

    private final Context ctx;
    private final byte[] key;

    private PhoneServer(Context c, String key) {
        ctx = c.getApplicationContext();
        this.key = key.getBytes(StandardCharsets.UTF_8);
    }

    static synchronized void start(Context c, String key) {
        if (running != null) {
            return;
        }
        running = new PhoneServer(c, key);
        Thread t = new Thread(running, "newal-phone");
        t.setDaemon(true);
        t.start();
    }

    @Override
    public void run() {
        try (ServerSocket s = new ServerSocket(PORT, 16, InetAddress.getByName("127.0.0.1"))) {
            while (true) {
                Socket c = s.accept();
                Thread t = new Thread(() -> serve(c), "newal-phone-call");
                t.setDaemon(true);
                t.start();
            }
        } catch (IOException e) {
            Log.e("NewAlCode", "phone server stopped", e);
            synchronized (PhoneServer.class) {
                running = null;
            }
        }
    }

    private void serve(Socket sock) {
        try (Socket c = sock) {
            c.setSoTimeout(120000);
            InputStream in = new BufferedInputStream(c.getInputStream());
            String first = line(in);
            Map<String, String> headers = new HashMap<>();
            for (String h; !(h = line(in)).isEmpty(); ) {
                int colon = h.indexOf(':');
                if (colon > 0) {
                    headers.put(h.substring(0, colon).trim().toLowerCase(Locale.ROOT), h.substring(colon + 1).trim());
                }
            }
            int n = 0;
            try {
                n = Math.min(Integer.parseInt(headers.getOrDefault("content-length", "0")), 1 << 20);
            } catch (NumberFormatException ignored) {
            }
            byte[] body = new byte[n];
            for (int got = 0; got < n; ) {
                int r = in.read(body, got, n - got);
                if (r < 0) {
                    break;
                }
                got += r;
            }
            int status = 200;
            JSONObject out;
            byte[] given = headers.getOrDefault("x-newal-key", "").getBytes(StandardCharsets.UTF_8);
            if (!first.startsWith("POST /phone ")) {
                status = 404;
                out = error("not found");
            } else if (!MessageDigest.isEqual(given, key)) {
                status = 401;
                out = error("wrong key");
            } else {
                try {
                    out = Phone.handle(ctx, new JSONObject(new String(body, StandardCharsets.UTF_8)));
                } catch (Exception e) {
                    status = 400;
                    out = error(e.getMessage() != null ? e.getMessage() : e.toString());
                }
            }
            byte[] data = out.toString().getBytes(StandardCharsets.UTF_8);
            OutputStream o = c.getOutputStream();
            String head = "HTTP/1.1 " + status + " " + (status == 200 ? "OK" : status == 401 ? "Unauthorized"
                    : status == 404 ? "Not Found" : "Bad Request") + "\r\nContent-Type: application/json\r\n"
                    + "Content-Length: " + data.length + "\r\nConnection: close\r\n\r\n";
            o.write(head.getBytes(StandardCharsets.UTF_8));
            o.write(data);
            o.flush();
        } catch (Exception e) {
            Log.w("NewAlCode", "phone call failed", e);
        }
    }

    static JSONObject error(String text) {
        JSONObject o = new JSONObject();
        try {
            o.put("ok", false);
            o.put("error", text);
        } catch (Exception ignored) {
        }
        return o;
    }

    private static String line(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        for (int ch; (ch = in.read()) >= 0 && ch != '\n'; ) {
            if (ch != '\r') {
                b.write(ch);
            }
            if (b.size() > 8192) {
                break;
            }
        }
        return b.toString("UTF-8");
    }
}
