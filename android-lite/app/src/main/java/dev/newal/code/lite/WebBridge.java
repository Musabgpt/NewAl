package dev.newal.code.lite;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * What NewAl Code's web interface may ask of the phone (window.NewAlPhone): the clipboard (a copied API key), links
 * in the browser (an API key page, GitHub), whether screen control and Termux are on, and the way to them. Only
 * NewAl Code's own pages load in this WebView (everything else opens in the browser).
 */
final class WebBridge {
    private final Activity act;
    private final WebView web;
    private final String key;

    WebBridge(Activity a, WebView w, String key) {
        act = a;
        web = w;
        this.key = key;
    }

    @JavascriptInterface
    public String clipboard() {
        try {
            return Phone.onMain(() -> {
                ClipboardManager cm = act.getSystemService(ClipboardManager.class);
                ClipData c = cm.getPrimaryClip();
                if (c == null || c.getItemCount() == 0) {
                    return "";
                }
                CharSequence t = c.getItemAt(0).coerceToText(act);
                return t == null ? "" : t.toString();
            });
        } catch (Exception e) {
            return "";
        }
    }

    @JavascriptInterface
    public void setClipboard(String text) {
        act.runOnUiThread(() -> act.getSystemService(ClipboardManager.class)
                .setPrimaryClip(ClipData.newPlainText("NewAl Code", text)));
    }

    @JavascriptInterface
    public void openUrl(String url) {
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) {
            return;
        }
        act.runOnUiThread(() -> {
            try {
                act.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (Exception ignored) {
            }
        });
    }

    /** {"accessibility": the screen control is on, "termux": {...}} */
    @JavascriptInterface
    public String status() {
        JSONObject o = new JSONObject();
        try {
            o.put("accessibility", PhoneControlService.on != null);
            JSONObject t = new JSONObject();
            t.put("installed", Termux.installed(act));
            t.put("allowed", Termux.allowed(act));
            t.put("up", Termux.up());
            o.put("termux", t);
            o.put("phone", "http://127.0.0.1:" + PhoneServer.PORT);
            o.put("sdk", Build.VERSION.SDK_INT);
            o.put("files", Access.files(act));
            o.put("notifications", Build.VERSION.SDK_INT < 33 || act.checkSelfPermission(
                    "android.permission.POST_NOTIFICATIONS") == PackageManager.PERMISSION_GRANTED);
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    @JavascriptInterface
    public void openAccessibilitySettings() {
        act.runOnUiThread(() -> {
            act.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            Toast.makeText(act, Build.VERSION.SDK_INT >= 33
                    ? "Turn on NewAl Code here. If Android says \"Restricted setting\": App info > \u22ee > Allow "
                    + "restricted settings, then again"
                    : "Turn on NewAl Code here (under Downloaded or Installed apps)", Toast.LENGTH_LONG).show();
        });
    }

    /**
     * This app's App info page: on Android 13 and up, an app installed from an APK has its accessibility switch
     * greyed out ("Restricted setting") until "Allow restricted settings" in the menu there.
     */
    @JavascriptInterface
    public void openAppInfo() {
        act.runOnUiThread(() -> act.startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + act.getPackageName()))));
    }

    /** The one-time setup: the command goes to the clipboard, Termux opens, the user pastes it and presses Enter. */
    @JavascriptInterface
    public void termuxSetup(String command) {
        act.runOnUiThread(() -> {
            if (!Termux.installed(act)) {
                act.startActivity(new Intent(Intent.ACTION_VIEW,
                        Uri.parse("https://f-droid.org/packages/com.termux/")));
                return;
            }
            act.getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("NewAl Code", command));
            Toast.makeText(act, "Copied: paste it in Termux and press Enter", Toast.LENGTH_LONG).show();
            Termux.open(act);
        });
    }

    /** Asks Android for Termux's RUN_COMMAND permission (then the app starts NewAl Code in Termux by itself). */
    @JavascriptInterface
    public void termuxAllow() {
        act.runOnUiThread(() -> act.requestPermissions(new String[] {Termux.PERMISSION}, 2));
    }

    /** Starts NewAl Code in Termux (once it was set up there), without opening Termux. */
    @JavascriptInterface
    public String termuxStart() {
        if (!Termux.allowed(act)) {
            return "not allowed";
        }
        try {
            Termux.run(act, "newal-termux start");
            return "started";
        } catch (Exception e) {
            return String.valueOf(e.getMessage());
        }
    }

    /** Speech to text for the composer: Android's recognizer (lang: "" for the phone's language). */
    @JavascriptInterface
    public void listen(String lang) {
        act.runOnUiThread(() -> {
            Intent i = new Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            i.putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            i.putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT, "NewAl Code");
            if (lang != null && !lang.isEmpty()) {
                i.putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, lang);
            }
            try {
                act.startActivityForResult(i, MainActivity.VOICE);
            } catch (Exception e) {
                Toast.makeText(act, "No speech recognition on this phone (Google's app or another speech service "
                        + "adds it)", Toast.LENGTH_LONG).show();
            }
        });
    }

    /** What another app shared ({"text", "files"}), once; "" when nothing. */
    @JavascriptInterface
    public String takeShared() {
        return Shared.take();
    }

    /** The one permission's phone part: Android's own grants, one after another (see Access). */
    @JavascriptInterface
    public void fullAccess() {
        act.runOnUiThread(() -> Access.start((MainActivity) act));
    }

    /** The page's colour (its theme): the status and navigation bars take it. */
    @JavascriptInterface
    public void theme(String color, boolean dark) {
        try {
            int c = Color.parseColor(color.trim());
            act.runOnUiThread(() -> ((MainActivity) act).bars(c, dark));
        } catch (Exception ignored) {
        }
    }

    /** {"root": the phone's shared storage, "granted": NewAl Code may read its files (the GGUF files there)}. */
    @JavascriptInterface
    public String storage() {
        JSONObject o = new JSONObject();
        try {
            o.put("root", Environment.getExternalStorageDirectory().getPath());
            o.put("granted", storageGranted());
            o.put("all_files", Build.VERSION.SDK_INT >= 30);
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    private boolean storageGranted() {
        if (Build.VERSION.SDK_INT >= 30) {
            return Environment.isExternalStorageManager();
        }
        return act.checkSelfPermission("android.permission.READ_EXTERNAL_STORAGE") == PackageManager.PERMISSION_GRANTED;
    }

    /** Asks for the phone's files: Android's "All files access" page for this app (11 and up), else the permission. */
    @JavascriptInterface
    public void allowStorage() {
        act.runOnUiThread(() -> {
            if (Build.VERSION.SDK_INT >= 30) {
                try {
                    act.startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + act.getPackageName())));
                } catch (Exception e) {
                    try {
                        act.startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                    } catch (Exception e2) {
                        Toast.makeText(act, "This phone does not let apps read all files: use \"Copy a GGUF into the "
                                + "app\" instead", Toast.LENGTH_LONG).show();
                        return;
                    }
                }
                Toast.makeText(act, "Turn on \"Allow access to manage all files\", then come back",
                        Toast.LENGTH_LONG).show();
            } else {
                act.requestPermissions(new String[] {"android.permission.READ_EXTERNAL_STORAGE",
                        "android.permission.WRITE_EXTERNAL_STORAGE"}, 3);
            }
        });
    }

    /** Android's file picker for a GGUF file, which is then copied into NewAl Code's models folder (ModelImport). */
    @JavascriptInterface
    public void importModel() {
        act.runOnUiThread(() -> {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            i.addCategory(Intent.CATEGORY_OPENABLE);
            i.setType("*/*");
            try {
                act.startActivityForResult(i, MainActivity.PICK_MODEL);
            } catch (Exception e) {
                Toast.makeText(act, "No file picker on this phone", Toast.LENGTH_LONG).show();
            }
        });
    }

    /** Shows NewAl Code in Termux (where = "termux") or in the app (anything else) in this window. */
    @JavascriptInterface
    public void go(String where) {
        int port = "termux".equals(where) ? Termux.PORT : Setup.PORT;
        act.runOnUiThread(() -> web.loadUrl("http://127.0.0.1:" + port + "/?key=" + Uri.encode(key)));
    }
}
