package dev.newal.code.lite;

import android.app.ActivityManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.StatFs;
import android.provider.AlarmClock;
import android.provider.Settings;
import android.util.DisplayMetrics;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * What the agent's `phone` tool does on the phone. Each action answers {"ok": true, "text": "..."}: the text is what
 * the model reads. The screen, taps, typing and swipes go through PhoneControlService (Android's accessibility
 * service for NewAl Code, which the user turns on); the rest needs no special permission. Messages and calls are only
 * prepared (the user presses send or call).
 */
final class Phone {
    private Phone() {
    }

    static JSONObject handle(Context ctx, JSONObject a) throws Exception {
        String action = a.optString("action");
        switch (action) {
            case "screen":
                return PhoneControlService.need().screen();
            case "tap":
                return PhoneControlService.need().tap(a);
            case "type":
                return PhoneControlService.need().type(a);
            case "swipe":
            case "scroll":
                return PhoneControlService.need().swipe(a.optString("direction", "down"), "scroll".equals(action));
            case "key":
                return PhoneControlService.need().key(a.optString("name"));
            case "open_app":
                return openApp(ctx, a.optString("name"));
            case "open_url":
                return start(ctx, new Intent(Intent.ACTION_VIEW, Uri.parse(a.optString("url"))),
                        "opened " + a.optString("url"));
            case "apps":
                return apps(ctx);
            case "alarm":
                return alarm(ctx, a);
            case "timer":
                return timer(ctx, a);
            case "torch":
                return torch(ctx, a.optBoolean("on", true));
            case "volume":
                return volume(ctx, a.optInt("level", 50));
            case "battery":
                return ok(battery(ctx));
            case "device":
                return ok(device(ctx));
            case "clipboard":
                return clipboard(ctx, a.has("text") ? a.optString("text") : null);
            case "notify":
                return notify(ctx, a.optString("title", "NewAl Code"), a.optString("text"));
            case "share":
                return start(ctx, Intent.createChooser(new Intent(Intent.ACTION_SEND).setType("text/plain")
                        .putExtra(Intent.EXTRA_TEXT, a.optString("text")), null), "the share sheet is open");
            case "sms":
                return start(ctx, new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + a.optString("number")))
                        .putExtra("sms_body", a.optString("text")),
                        "the message to " + a.optString("number") + " is ready: the user sends it");
            case "call":
                return start(ctx, new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + a.optString("number"))),
                        "the dialer shows " + a.optString("number") + ": the user presses call");
            case "settings":
                return settings(ctx, a.optString("page"));
            case "intent":
                return intent(ctx, a.optJSONObject("intent") != null ? a.optJSONObject("intent") : a);
            case "wait":
                Thread.sleep(Math.max(0, Math.min(30, a.optInt("seconds", 1))) * 1000L);
                return ok("waited " + Math.max(0, Math.min(30, a.optInt("seconds", 1))) + " s");
            default:
                throw new IllegalArgumentException("unknown action \"" + action + "\"");
        }
    }

    static JSONObject ok(String text) throws Exception {
        JSONObject o = new JSONObject();
        o.put("ok", true);
        o.put("text", text);
        return o;
    }

    /** Runs on the main thread and waits for it (some of Android's classes want their calls there). */
    static <T> T onMain(Callable<T> job) throws Exception {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return job.call();
        }
        AtomicReference<T> out = new AtomicReference<>();
        AtomicReference<Exception> err = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                out.set(job.call());
            } catch (Exception e) {
                err.set(e);
            }
            done.countDown();
        });
        if (!done.await(20, TimeUnit.SECONDS)) {
            throw new IllegalStateException("the phone did not answer in time");
        }
        if (err.get() != null) {
            throw err.get();
        }
        return out.get();
    }

    private static JSONObject start(Context ctx, Intent i, String text) throws Exception {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            ctx.startActivity(i);
        } catch (ActivityNotFoundException e) {
            throw new IllegalStateException("no app on this phone does that (" + i.getAction() + ")");
        }
        Thread.sleep(700);                  // the screen changes: let it, before the model looks again
        return ok(text);
    }

    private static List<String[]> launchable(Context ctx) {
        PackageManager pm = ctx.getPackageManager();
        List<ResolveInfo> found = pm.queryIntentActivities(
                new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0);
        List<String[]> out = new ArrayList<>();
        for (ResolveInfo r : found) {
            out.add(new String[] {String.valueOf(r.loadLabel(pm)), r.activityInfo.packageName});
        }
        Collections.sort(out, (x, y) -> x[0].compareToIgnoreCase(y[0]));
        return out;
    }

    private static JSONObject apps(Context ctx) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (String[] app : launchable(ctx)) {
            sb.append(app[0]).append(" (").append(app[1]).append(")\n");
        }
        return ok(sb.toString().trim());
    }

    private static JSONObject openApp(Context ctx, String name) throws Exception {
        String want = name.trim().toLowerCase(Locale.ROOT);
        if (want.isEmpty()) {
            throw new IllegalArgumentException("open_app needs name: the app's name or package");
        }
        String[] best = null;
        int score = 0;
        for (String[] app : launchable(ctx)) {
            String label = app[0].toLowerCase(Locale.ROOT);
            int s = app[1].equalsIgnoreCase(want) || label.equals(want) ? 3 : label.startsWith(want) ? 2
                    : label.contains(want) || app[1].toLowerCase(Locale.ROOT).contains(want) ? 1 : 0;
            if (s > score) {
                best = app;
                score = s;
            }
        }
        if (best == null) {
            throw new IllegalArgumentException("no app called \"" + name + "\" (action apps lists them)");
        }
        Intent i = ctx.getPackageManager().getLaunchIntentForPackage(best[1]);
        if (i == null) {
            throw new IllegalStateException(best[0] + " cannot be opened");
        }
        return start(ctx, i, "opened " + best[0]);
    }

    private static JSONObject alarm(Context ctx, JSONObject a) throws Exception {
        int h = a.optInt("hour", -1), m = a.optInt("minute", 0);
        if (h < 0 || h > 23 || m < 0 || m > 59) {
            throw new IllegalArgumentException("alarm needs hour (0-23) and minute (0-59)");
        }
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, h)
                .putExtra(AlarmClock.EXTRA_MINUTES, m).putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        if (!a.optString("label").isEmpty()) {
            i.putExtra(AlarmClock.EXTRA_MESSAGE, a.optString("label"));
        }
        return start(ctx, i, String.format(Locale.ROOT, "alarm set for %02d:%02d", h, m));
    }

    private static JSONObject timer(Context ctx, JSONObject a) throws Exception {
        int s = a.optInt("seconds", 0);
        if (s <= 0 || s > 86400) {
            throw new IllegalArgumentException("timer needs seconds (1-86400)");
        }
        Intent i = new Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, s)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        if (!a.optString("label").isEmpty()) {
            i.putExtra(AlarmClock.EXTRA_MESSAGE, a.optString("label"));
        }
        return start(ctx, i, "timer set for " + s + " s");
    }

    private static JSONObject torch(Context ctx, boolean on) throws Exception {
        CameraManager cm = ctx.getSystemService(CameraManager.class);
        for (String id : cm.getCameraIdList()) {
            Boolean flash = cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
            if (Boolean.TRUE.equals(flash)) {
                cm.setTorchMode(id, on);
                return ok("torch " + (on ? "on" : "off"));
            }
        }
        throw new IllegalStateException("this phone has no torch");
    }

    private static JSONObject volume(Context ctx, int level) throws Exception {
        AudioManager am = ctx.getSystemService(AudioManager.class);
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int v = Math.round(Math.max(0, Math.min(100, level)) * max / 100f);
        am.setStreamVolume(AudioManager.STREAM_MUSIC, v, AudioManager.FLAG_SHOW_UI);
        return ok("media volume " + Math.max(0, Math.min(100, level)) + "%");
    }

    static String battery(Context ctx) {
        Intent b = ctx.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (b == null) {
            return "battery: unknown";
        }
        int level = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1), scale = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        int status = b.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
        boolean charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL;
        float temp = b.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10f;
        return String.format(Locale.ROOT, "battery %d%%, %s, %.1f °C", Math.round(100f * level / Math.max(1, scale)),
                charging ? "charging" : "not charging", temp);
    }

    static String device(Context ctx) {
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        ctx.getSystemService(ActivityManager.class).getMemoryInfo(mi);
        StatFs fs = new StatFs(Environment.getDataDirectory().getPath());
        DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        return String.format(Locale.ROOT, "%s %s, Android %s (API %d), %s; RAM %.1f GB (%.1f GB free); storage %.1f GB "
                        + "free of %.1f GB; screen %dx%d; %s",
                Build.MANUFACTURER, Build.MODEL, Build.VERSION.RELEASE, Build.VERSION.SDK_INT, Build.SUPPORTED_ABIS[0],
                mi.totalMem / 1e9, mi.availMem / 1e9, fs.getAvailableBytes() / 1e9, fs.getTotalBytes() / 1e9,
                dm.widthPixels, dm.heightPixels, battery(ctx));
    }

    private static JSONObject clipboard(Context ctx, String text) throws Exception {
        return onMain(() -> {
            ClipboardManager cm = ctx.getSystemService(ClipboardManager.class);
            if (text != null) {
                cm.setPrimaryClip(ClipData.newPlainText("NewAl Code", text));
                return ok("copied " + text.length() + " characters");
            }
            ClipData c = cm.getPrimaryClip();
            if (c == null || c.getItemCount() == 0) {
                return ok("(the clipboard is empty, or Android shows it only to the app on screen)");
            }
            CharSequence t = c.getItemAt(0).coerceToText(ctx);
            return ok(t == null ? "" : t.toString());
        });
    }

    private static JSONObject notify(Context ctx, String title, String text) throws Exception {
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("agent", "NewAl Code: from the agent",
                NotificationManager.IMPORTANCE_DEFAULT));
        Notification n = new Notification.Builder(ctx, "agent").setContentTitle(title).setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text)).setSmallIcon(R.drawable.ic_stat)
                .setAutoCancel(true).build();
        nm.notify((int) (System.currentTimeMillis() % 100000) + 10, n);
        return ok("notification posted");
    }

    private static JSONObject settings(Context ctx, String page) throws Exception {
        String p = page.trim().toLowerCase(Locale.ROOT);
        String action;
        switch (p) {
            case "wifi":
            case "wi-fi":
                action = Build.VERSION.SDK_INT >= 29 ? Settings.Panel.ACTION_WIFI : Settings.ACTION_WIFI_SETTINGS;
                break;
            case "internet":
            case "data":
            case "mobile data":
                action = Build.VERSION.SDK_INT >= 29 ? Settings.Panel.ACTION_INTERNET_CONNECTIVITY
                        : Settings.ACTION_WIRELESS_SETTINGS;
                break;
            case "bluetooth":
                action = Settings.ACTION_BLUETOOTH_SETTINGS;
                break;
            case "display":
            case "brightness":
                action = Settings.ACTION_DISPLAY_SETTINGS;
                break;
            case "sound":
            case "volume":
                action = Build.VERSION.SDK_INT >= 29 ? Settings.Panel.ACTION_VOLUME : Settings.ACTION_SOUND_SETTINGS;
                break;
            case "battery":
                action = Intent.ACTION_POWER_USAGE_SUMMARY;
                break;
            case "apps":
                action = Settings.ACTION_APPLICATION_SETTINGS;
                break;
            case "location":
                action = Settings.ACTION_LOCATION_SOURCE_SETTINGS;
                break;
            case "accessibility":
                action = Settings.ACTION_ACCESSIBILITY_SETTINGS;
                break;
            case "notifications":
                action = Settings.ACTION_APP_NOTIFICATION_SETTINGS;
                break;
            case "storage":
                action = Settings.ACTION_INTERNAL_STORAGE_SETTINGS;
                break;
            case "date":
            case "time":
                action = Settings.ACTION_DATE_SETTINGS;
                break;
            case "nfc":
                action = Settings.ACTION_NFC_SETTINGS;
                break;
            default:
                action = Settings.ACTION_SETTINGS;
        }
        Intent i = new Intent(action);
        if (Settings.ACTION_APP_NOTIFICATION_SETTINGS.equals(action)) {
            i.putExtra(Settings.EXTRA_APP_PACKAGE, ctx.getPackageName());
        }
        return start(ctx, i, "opened the " + (p.isEmpty() ? "settings" : p) + " settings");
    }

    private static JSONObject intent(Context ctx, JSONObject o) throws Exception {
        Intent i = new Intent(o.optString("action", Intent.ACTION_VIEW));
        String data = o.optString("data"), type = o.optString("type");
        if (!data.isEmpty() && !type.isEmpty()) {
            i.setDataAndType(Uri.parse(data), type);
        } else if (!data.isEmpty()) {
            i.setData(Uri.parse(data));
        } else if (!type.isEmpty()) {
            i.setType(type);
        }
        if (!o.optString("package").isEmpty()) {
            i.setPackage(o.optString("package"));
        }
        JSONObject extras = o.optJSONObject("extras");
        if (extras != null) {
            for (Iterator<String> it = extras.keys(); it.hasNext(); ) {
                String k = it.next();
                Object v = extras.get(k);
                if (v instanceof Boolean) {
                    i.putExtra(k, (Boolean) v);
                } else if (v instanceof Integer) {
                    i.putExtra(k, (Integer) v);
                } else if (v instanceof Long) {
                    i.putExtra(k, (Long) v);
                } else if (v instanceof Double) {
                    i.putExtra(k, (Double) v);
                } else {
                    i.putExtra(k, String.valueOf(v));
                }
            }
        }
        return start(ctx, i, "started " + i.getAction());
    }
}
