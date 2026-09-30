package dev.newal.code.lite;

import android.app.Activity;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.webkit.WebView;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Arrays;

/**
 * Copies a GGUF file the user picked (Android's file picker: Download, Drive, a USB stick...) into NewAl Code's models
 * folder, where it is a model like the downloaded ones. For phones where "All files access" is not given (or cannot
 * be: Android Go). The page hears how it goes through window.onPhoneImport({state, name, done, total, path, error}).
 */
final class ModelImport {
    private static final byte[] MAGIC = {'G', 'G', 'U', 'F'};
    private static volatile boolean busy;

    private ModelImport() {
    }

    static void start(Activity act, WebView web, Uri uri) {
        if (busy) {
            tell(act, web, "error", "", 0, 0, "", "a file is being copied already");
            return;
        }
        busy = true;
        new Thread(() -> {
            String name = "model.gguf";
            try {
                long total = -1;
                try (Cursor c = act.getContentResolver().query(uri, null, null, null, null)) {
                    if (c != null && c.moveToFirst()) {
                        int n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                        int s = c.getColumnIndex(OpenableColumns.SIZE);
                        if (n >= 0 && c.getString(n) != null) {
                            name = new File(c.getString(n)).getName();
                        }
                        if (s >= 0 && !c.isNull(s)) {
                            total = c.getLong(s);
                        }
                    }
                }
                if (!name.toLowerCase().endsWith(".gguf")) {
                    name = name + ".gguf";
                }
                File dir = new File(new Setup(act).home, ".newal-code/models");
                if (!dir.isDirectory() && !dir.mkdirs()) {
                    throw new IOException("cannot make " + dir);
                }
                File out = new File(dir, name);
                if (total > 0 && out.length() == total) {
                    tell(act, web, "done", name, total, total, out.getPath(), "");     // copied before
                    return;
                }
                if (total > 0 && dir.getUsableSpace() < total + 64L * 1024 * 1024) {
                    throw new IOException(String.format("not enough space: %.1f GB needed, %.1f GB free",
                            total / 1e9, dir.getUsableSpace() / 1e9));
                }
                File part = new File(dir, name + ".part");
                try (InputStream in = act.getContentResolver().openInputStream(uri);
                     OutputStream o = new FileOutputStream(part)) {
                    if (in == null) {
                        throw new IOException("the file cannot be opened");
                    }
                    byte[] buf = new byte[1 << 20];
                    long done = 0, told = 0;
                    int n;
                    boolean first = true;
                    while ((n = in.read(buf)) > 0) {
                        if (first) {
                            if (n < 4 || !Arrays.equals(Arrays.copyOf(buf, 4), MAGIC)) {
                                throw new IOException(name + " is not a GGUF model file");
                            }
                            first = false;
                        }
                        o.write(buf, 0, n);
                        done += n;
                        long now = System.currentTimeMillis();
                        if (now - told > 1500) {
                            told = now;
                            tell(act, web, "copying", name, done, total, "", "");
                        }
                    }
                } catch (IOException e) {
                    part.delete();
                    throw e;
                }
                if (!part.renameTo(out)) {
                    part.delete();
                    throw new IOException("cannot move the copy into place");
                }
                tell(act, web, "done", name, out.length(), out.length(), out.getPath(), "");
            } catch (Exception e) {
                tell(act, web, "error", name, 0, 0, "", String.valueOf(e.getMessage()));
            } finally {
                busy = false;
            }
        }, "newal-import").start();
    }

    private static void tell(Activity act, WebView web, String state, String name, long done, long total, String path,
                             String error) {
        try {
            JSONObject o = new JSONObject();
            o.put("state", state);
            o.put("name", name);
            o.put("done", done);
            o.put("total", total);
            o.put("path", path);
            o.put("error", error);
            String js = "window.onPhoneImport && window.onPhoneImport(" + o + ")";
            act.runOnUiThread(() -> web.evaluateJavascript(js, null));
        } catch (Exception ignored) {
        }
    }
}
