package dev.newal.code.lite;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.Html;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.net.HttpURLConnection;
import java.net.URL;

/** NewAl Code's interface (the same web app as on a computer) in a WebView, once the service has started it. */
public class MainActivity extends Activity {
    private static final String HOME = "http://127.0.0.1:" + Setup.PORT + "/";
    private WebView web;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.setWebChromeClient(new WebChromeClient());           // confirm() and alert() dialogs
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
        page("Starting NewAl Code…", "The first start unpacks Python and NewAl Code (a few seconds).");
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 1);
        }
        startForegroundService(new Intent(this, AgentService.class));
        waitForServer();
    }

    private void waitForServer() {
        new Thread(() -> {
            for (int i = 0; i < 480; i++) {
                if (up()) {
                    runOnUiThread(() -> web.loadUrl(HOME));
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
            runOnUiThread(() -> page("NewAl Code did not start", why));
        }, "newal-wait").start();
    }

    private static boolean up() {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(HOME + "api/state").openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(3000);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (Exception e) {
            return false;
        }
    }

    private void page(String title, String text) {
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;padding:32px;color:#222'><h2>" + Html.escapeHtml(title)
                + "</h2><pre style='white-space:pre-wrap;color:#555'>" + Html.escapeHtml(text) + "</pre></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
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
