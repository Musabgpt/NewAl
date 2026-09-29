package dev.newal.code.lite;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.net.Uri;
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
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    @JavascriptInterface
    public void openAccessibilitySettings() {
        act.runOnUiThread(() -> {
            act.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            Toast.makeText(act, "Turn on NewAl Code here (under Downloaded or Installed apps)", Toast.LENGTH_LONG)
                    .show();
        });
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

    /** Shows NewAl Code in Termux (where = "termux") or in the app (anything else) in this window. */
    @JavascriptInterface
    public void go(String where) {
        int port = "termux".equals(where) ? Termux.PORT : Setup.PORT;
        act.runOnUiThread(() -> web.loadUrl("http://127.0.0.1:" + port + "/?key=" + Uri.encode(key)));
    }
}
