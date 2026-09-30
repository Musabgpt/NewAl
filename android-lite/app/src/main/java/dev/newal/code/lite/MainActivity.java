package dev.newal.code.lite;

import android.app.Activity;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.Html;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.net.HttpURLConnection;
import java.net.URL;

/** NewAl Code's interface (the same web app as on a computer) in a WebView, once the service has started it. */
public class MainActivity extends Activity {
    static final int PICK_MODEL = 7;
    private static final String HOME = "http://127.0.0.1:" + Setup.PORT + "/";
    private FrameLayout root;
    private WebView web;
    private String key;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        betweenBars();
        bars(night ? 0xff1e1e20 : Color.WHITE, night);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.setWebChromeClient(new WebChromeClient());           // confirm() and alert() dialogs
        key = new Setup(this).key();
        web.addJavascriptInterface(new WebBridge(this, web, key), "NewAlPhone");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if ("127.0.0.1".equals(u.getHost())) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));   // GitHub, Hugging Face: the browser
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        page("Starting NewAl Code…", "The first start unpacks Python and NewAl Code (a few seconds).", night);
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 1);
        }
        startForegroundService(new Intent(this, AgentService.class));
        waitForServer();
    }

    /**
     * The page between the status bar (and a camera cutout) and the navigation bar or the keyboard. Android 15 draws
     * apps under those bars, so from Android 11 on the app lays itself out that way and pads the page; Android 8 to
     * 10 stop the window at the bars, and adjustResize at the keyboard.
     */
    private void betweenBars() {
        if (Build.VERSION.SDK_INT < 30) {
            return;
        }
        getWindow().setDecorFitsSystemWindows(false);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            return WindowInsets.CONSUMED;
        });
        root.requestApplyInsets();
    }

    /** The status and navigation bars in the page's colour, with icons that show on it (the page tells its colour). */
    void bars(int color, boolean dark) {
        root.setBackgroundColor(color);
        web.setBackgroundColor(color);
        Window w = getWindow();
        if (Build.VERSION.SDK_INT >= 30) {
            w.setStatusBarColor(Color.TRANSPARENT);
            w.setNavigationBarColor(Color.TRANSPARENT);
            WindowInsetsController c = w.getInsetsController();
            int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            if (c != null) {
                c.setSystemBarsAppearance(dark ? 0 : light, light);
            }
        } else {
            w.setStatusBarColor(color);
            w.setNavigationBarColor(color);
            View d = w.getDecorView();
            int light = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            int flags = d.getSystemUiVisibility();
            d.setSystemUiVisibility(dark ? flags & ~light : flags | light);
        }
    }

    private void waitForServer() {
        new Thread(() -> {
            for (int i = 0; i < 480; i++) {
                if (up(key)) {
                    runOnUiThread(() -> web.loadUrl(HOME + "?key=" + Uri.encode(key)));
                    return;
                }
                if (!AgentService.error.isEmpty()) {
                    break;
                }
                try {
                    Thread.sleep(250);
                } catch (InterruptedException e) {
                    return;
                }
            }
            String why = AgentService.error + "\n" + new Setup(this).logTail();
            boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                    == Configuration.UI_MODE_NIGHT_YES;
            runOnUiThread(() -> page("NewAl Code did not start", why, night));
        }, "newal-wait").start();
    }

    private static boolean up(String key) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(HOME + "api/state").openConnection();
            c.setRequestProperty("X-NewAl-Key", key);
            c.setConnectTimeout(800);
            c.setReadTimeout(3000);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (Exception e) {
            return false;
        }
    }

    private void page(String title, String text, boolean night) {
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;padding:24px;margin:0;background:" + (night ? "#1e1e20" : "#fff")
                + ";color:" + (night ? "#ececf0" : "#222") + "'><h2>" + Html.escapeHtml(title)
                + "</h2><pre style='white-space:pre-wrap;color:" + (night ? "#a9a9b2" : "#555") + "'>"
                + Html.escapeHtml(text) + "</pre></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    /** A GGUF file the user picked (the Models page's "Copy a GGUF into the app"): copied into the models folder. */
    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_MODEL && result == RESULT_OK && data != null && data.getData() != null) {
            ModelImport.start(this, web, data.getData());
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            moveTaskToBack(true);        // keep working in the background (the service stays)
        }
    }
}
