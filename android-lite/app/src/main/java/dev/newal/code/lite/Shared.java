package dev.newal.code.lite;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * "Share" from another app to NewAl Code: the text, and files copied into NewAl Code's home (Shared/), become the
 * start of a new thread (the page takes them with NewAlPhone.takeShared()). Files up to 200 MB each.
 */
final class Shared {
    private static final long LIMIT = 200L * 1024 * 1024;
    private static volatile String pending = "";

    private Shared() {
    }

    private static volatile String action = "";

    /** A shortcut's action ("new", "voice"), once; "" when none. */
    static String takeAction() {
        String a = action;
        action = "";
        return a;
    }

    /** The icon's shortcuts: a new thread, or a spoken request. */
    static boolean shortcut(Intent i, Runnable tell) {
        String a = i == null ? null : i.getAction();
        if ("dev.newal.code.lite.NEW".equals(a) || "dev.newal.code.lite.VOICE".equals(a)) {
            action = a.endsWith("VOICE") ? "voice" : "new";
            i.setAction(Intent.ACTION_MAIN);
            tell.run();
            return true;
        }
        return false;
    }

    static String take() {
        String s = pending;
        pending = "";
        return s;
    }

    /** An intent that brought something shared: copied, then the page is told (it may also ask at its start). */
    static void from(Activity act, Intent i, Runnable tell) {
        if (i == null || !(Intent.ACTION_SEND.equals(i.getAction()) || Intent.ACTION_SEND_MULTIPLE.equals(i.getAction()))) {
            return;
        }
        String text = i.getStringExtra(Intent.EXTRA_TEXT);
        String subject = i.getStringExtra(Intent.EXTRA_SUBJECT);
        List<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(i.getAction())) {
            Uri u = i.getParcelableExtra(Intent.EXTRA_STREAM);
            if (u != null) {
                uris.add(u);
            }
        } else {
            ArrayList<Uri> list = i.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                uris.addAll(list);
            }
        }
        i.setAction(Intent.ACTION_MAIN);           // taken once (not again when the activity is recreated)
        new Thread(() -> {
            JSONObject o = new JSONObject();
            JSONArray files = new JSONArray();
            try {
                String t = ((subject != null && !subject.isEmpty() ? subject + "\n" : "") + (text != null ? text : ""))
                        .trim();
                o.put("text", t);
                File dir = new File(new Setup(act).home, "Shared");
                dir.mkdirs();
                for (Uri u : uris) {
                    File f = copy(act, u, dir);
                    if (f != null) {
                        files.put(f.getPath());
                    }
                }
                o.put("files", files);
            } catch (Exception ignored) {
            }
            pending = o.toString();
            act.runOnUiThread(tell);
        }, "newal-shared").start();
    }

    private static File copy(Activity act, Uri u, File dir) {
        String name = "shared";
        try (Cursor c = act.getContentResolver().query(u, null, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (n >= 0 && c.getString(n) != null) {
                    name = new File(c.getString(n)).getName();
                }
            }
        } catch (Exception ignored) {
        }
        File out = new File(dir, name);
        for (int k = 2; out.exists(); k++) {
            int dot = name.lastIndexOf('.');
            out = new File(dir, dot > 0 ? name.substring(0, dot) + "-" + k + name.substring(dot) : name + "-" + k);
        }
        try (InputStream in = act.getContentResolver().openInputStream(u); OutputStream o = new FileOutputStream(out)) {
            if (in == null) {
                return null;
            }
            byte[] buf = new byte[1 << 16];
            long total = 0;
            int r;
            while ((r = in.read(buf)) > 0) {
                total += r;
                if (total > LIMIT) {
                    throw new java.io.IOException("too large");
                }
                o.write(buf, 0, r);
            }
            return out;
        } catch (Exception e) {
            out.delete();
            return null;
        }
    }
}
