package com.musab.aragpt2;

import android.app.ActivityManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Environment;
import android.os.StatFs;
import android.os.SystemClock;
import android.provider.AlarmClock;
import android.provider.MediaStore;
import android.provider.Settings;
import android.view.KeyEvent;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.Locale;

/**
 * Device control for the personal-assistant agent: real Android actions (open apps, alarms,
 * settings, flashlight, volume, media keys ...) plus screen control through
 * {@link ScreenControlService} when the user has enabled it. Calls and messages only open the
 * dialer / composer; the user presses send.
 */
final class DeviceController implements LocalTools {
    private final Context context;
    private JSONArray cached;

    DeviceController(Context context) { this.context = context.getApplicationContext(); }

    @Override public synchronized JSONArray list() {
        if (cached != null) return cached;
        JSONArray a = new JSONArray();
        try {
            add(a, "open_app", "Open an installed app by name, e.g. YouTube, WhatsApp, Camera", "{\"name\":\"string\"}", false);
            add(a, "list_apps", "Names of the installed apps", "{}", false);
            add(a, "open_url", "Open a web page in the browser", "{\"url\":\"string\"}", false);
            add(a, "web_search", "Search the web in the browser", "{\"query\":\"string\"}", false);
            add(a, "navigate", "Open maps navigation to a place", "{\"destination\":\"string\"}", false);
            add(a, "dial", "Open the dialer with a number (the user presses call)", "{\"number\":\"string\"}", false);
            add(a, "compose_sms", "Open a new SMS with text (the user presses send)", "{\"number\":\"string\",\"text\":\"string\"}", false);
            add(a, "set_alarm", "Set an alarm", "{\"hour\":\"integer\",\"minute\":\"integer\",\"label\":\"string\"}", false);
            add(a, "set_timer", "Start a countdown timer", "{\"seconds\":\"integer\",\"label\":\"string\"}", false);
            add(a, "open_settings", "Open a settings page: wifi, bluetooth, display, sound, battery, apps, location, "
                    + "notifications, accessibility, internet, main", "{\"page\":\"string\"}", false);
            add(a, "flashlight", "Turn the flashlight on or off", "{\"on\":\"boolean\"}", false);
            add(a, "volume", "Set media volume 0-100, or up / down / mute", "{\"level\":\"string\"}", false);
            add(a, "media", "Media control: play, pause, toggle, next, previous", "{\"action\":\"string\"}", false);
            add(a, "share_text", "Share text to another app", "{\"text\":\"string\"}", false);
            add(a, "open_camera", "Open the camera app", "{}", false);
            add(a, "device_status", "Battery, charging, free storage and RAM", "{}", false);
            add(a, "press", "Screen control: back, home, recents, notifications, quick_settings, lock, screenshot",
                    "{\"button\":\"string\"}", false);
            add(a, "read_screen", "Screen control: the text and buttons currently on screen", "{}", false);
            add(a, "tap", "Screen control: tap the button or item showing this text", "{\"text\":\"string\"}", false);
            add(a, "type_text", "Screen control: type text into the focused field", "{\"text\":\"string\"}", true);
            add(a, "scroll", "Screen control: scroll up or down", "{\"direction\":\"string\"}", false);
            add(a, "enable_screen_control", "Open the setting where the user enables screen control", "{}", false);
        } catch (JSONException ignored) {
        }
        cached = a;
        return a;
    }

    private static void add(JSONArray a, String name, String desc, String params, boolean confirm) throws JSONException {
        a.put(new JSONObject().put("name", name).put("description", desc).put("parameters", new JSONObject(params))
                .put("confirm", confirm).put("source", "device"));
    }

    @Override public JSONObject call(String name, JSONObject a) throws Exception {
        switch (name) {
            case "open_app": return openApp(a.optString("name"));
            case "list_apps": return ok(String.join(", ", appLabels()));
            case "open_url": {
                String url = a.optString("url");
                if (!url.matches("(?i)^https?://.*")) url = "https://" + url;
                return start(new Intent(Intent.ACTION_VIEW, Uri.parse(url)), "opened " + url);
            }
            case "web_search":
                return start(new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/search?q="
                        + Uri.encode(a.optString("query")))), "searching " + a.optString("query"));
            case "navigate":
                return start(new Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(a.optString("destination")))),
                        "navigating to " + a.optString("destination"));
            case "dial":
                return start(new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(a.optString("number")))),
                        "dialer opened for " + a.optString("number"));
            case "compose_sms": {
                Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(a.optString("number"))));
                i.putExtra("sms_body", a.optString("text"));
                return start(i, "message ready to send");
            }
            case "set_alarm": {
                Intent i = new Intent(AlarmClock.ACTION_SET_ALARM)
                        .putExtra(AlarmClock.EXTRA_HOUR, a.optInt("hour"))
                        .putExtra(AlarmClock.EXTRA_MINUTES, a.optInt("minute"))
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
                if (!a.optString("label").isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, a.optString("label"));
                return start(i, String.format(Locale.US, "alarm set for %02d:%02d", a.optInt("hour"), a.optInt("minute")));
            }
            case "set_timer": {
                Intent i = new Intent(AlarmClock.ACTION_SET_TIMER)
                        .putExtra(AlarmClock.EXTRA_LENGTH, Math.max(1, a.optInt("seconds")))
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
                if (!a.optString("label").isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, a.optString("label"));
                return start(i, "timer started for " + a.optInt("seconds") + " s");
            }
            case "open_settings": return start(new Intent(settingsAction(a.optString("page"))), "settings opened");
            case "flashlight": return flashlight(a.optBoolean("on", true));
            case "volume": return volume(a.optString("level", "up"));
            case "media": return media(a.optString("action", "toggle"));
            case "share_text": {
                Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, a.optString("text"));
                return start(Intent.createChooser(send, null), "share sheet opened");
            }
            case "open_camera": return start(new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA), "camera opened");
            case "device_status": return ok(status());
            case "enable_screen_control":
                return start(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
                        "accessibility settings opened: enable 'NewAl screen control'");
            case "press": case "read_screen": case "tap": case "type_text": case "scroll":
                return screen(name, a);
            default: return null;
        }
    }

    // ------------------------------------------------------------------ actions

    private JSONObject openApp(String name) throws JSONException {
        PackageManager pm = context.getPackageManager();
        String wanted = norm(name);
        ResolveInfo best = null;
        int bestScore = 0;
        for (ResolveInfo r : launchers()) {
            String label = norm(String.valueOf(r.loadLabel(pm)));
            String pkg = r.activityInfo.packageName.toLowerCase(Locale.ROOT);
            int score = label.equals(wanted) ? 100 : label.startsWith(wanted) ? 80 : label.contains(wanted) ? 60
                    : wanted.contains(label) && label.length() > 2 ? 50 : pkg.contains(wanted) ? 40 : 0;
            if (score > bestScore) { bestScore = score; best = r; }
        }
        if (best == null) return fail("no installed app matches '" + name + "'. Installed: " + String.join(", ", appLabels()));
        Intent i = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        if (i == null) return fail("the app cannot be launched");
        return start(i, "opened " + best.loadLabel(pm));
    }

    private List<ResolveInfo> launchers() {
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        return context.getPackageManager().queryIntentActivities(main, 0);
    }

    private List<String> appLabels() {
        PackageManager pm = context.getPackageManager();
        List<String> names = new java.util.ArrayList<>();
        for (ResolveInfo r : launchers()) names.add(String.valueOf(r.loadLabel(pm)));
        java.util.Collections.sort(names);
        return names.size() > 200 ? names.subList(0, 200) : names;
    }

    private static String norm(String s) {
        return s == null ? "" : s.toLowerCase(Locale.ROOT).replaceAll("[\\s\\-_.]", "");
    }

    private static String settingsAction(String page) {
        switch (page.toLowerCase(Locale.ROOT)) {
            case "wifi": return Settings.ACTION_WIFI_SETTINGS;
            case "bluetooth": return Settings.ACTION_BLUETOOTH_SETTINGS;
            case "display": case "brightness": return Settings.ACTION_DISPLAY_SETTINGS;
            case "sound": return Settings.ACTION_SOUND_SETTINGS;
            case "battery": return Intent.ACTION_POWER_USAGE_SUMMARY;
            case "apps": return Settings.ACTION_APPLICATION_SETTINGS;
            case "location": return Settings.ACTION_LOCATION_SOURCE_SETTINGS;
            case "notifications": return "android.settings.NOTIFICATION_SETTINGS";
            case "accessibility": return Settings.ACTION_ACCESSIBILITY_SETTINGS;
            case "internet": case "data": return Settings.ACTION_WIRELESS_SETTINGS;
            default: return Settings.ACTION_SETTINGS;
        }
    }

    private JSONObject flashlight(boolean on) throws Exception {
        CameraManager cm = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        for (String id : cm.getCameraIdList()) {
            Boolean flash = cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
            if (Boolean.TRUE.equals(flash)) {
                cm.setTorchMode(id, on);
                return ok("flashlight " + (on ? "on" : "off"));
            }
        }
        return fail("no flashlight on this phone");
    }

    private JSONObject volume(String level) throws JSONException {
        AudioManager am = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        switch (level.toLowerCase(Locale.ROOT)) {
            case "up": am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI); break;
            case "down": am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI); break;
            case "mute": am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, AudioManager.FLAG_SHOW_UI); break;
            default:
                String digits = level.replaceAll("[^0-9]", "");
                int pct = digits.isEmpty() ? 50 : Math.max(0, Math.min(100, Integer.parseInt(digits)));
                am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(pct / 100f * max), AudioManager.FLAG_SHOW_UI);
        }
        return ok("media volume " + (am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100 / Math.max(1, max)) + "%");
    }

    private JSONObject media(String action) throws JSONException {
        int key;
        switch (action.toLowerCase(Locale.ROOT)) {
            case "play": key = KeyEvent.KEYCODE_MEDIA_PLAY; break;
            case "pause": key = KeyEvent.KEYCODE_MEDIA_PAUSE; break;
            case "next": key = KeyEvent.KEYCODE_MEDIA_NEXT; break;
            case "previous": key = KeyEvent.KEYCODE_MEDIA_PREVIOUS; break;
            default: key = KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE;
        }
        AudioManager am = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        long t = SystemClock.uptimeMillis();
        am.dispatchMediaKeyEvent(new KeyEvent(t, t, KeyEvent.ACTION_DOWN, key, 0));
        am.dispatchMediaKeyEvent(new KeyEvent(t, t, KeyEvent.ACTION_UP, key, 0));
        return ok("media " + action);
    }

    private String status() {
        BatteryManager bm = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
        int pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        boolean charging = bm.isCharging();
        StatFs fs = new StatFs(Environment.getDataDirectory().getPath());
        ActivityManager am = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        am.getMemoryInfo(mi);
        return String.format(Locale.US, "battery %d%%%s, free storage %.1f GB, RAM %.1f GB free of %.1f GB",
                pct, charging ? " (charging)" : "", fs.getAvailableBytes() / 1e9, mi.availMem / 1e9, mi.totalMem / 1e9);
    }

    private JSONObject screen(String name, JSONObject a) throws JSONException {
        ScreenControlService s = ScreenControlService.instance;
        if (s == null) {
            return fail("screen control is off. Call TOOL: enable_screen_control {} and ask the user to enable "
                    + "'NewAl screen control' in Accessibility settings.");
        }
        switch (name) {
            case "press": return s.press(a.optString("button")) ? ok("pressed " + a.optString("button")) : fail("not supported: " + a.optString("button"));
            case "read_screen": return ok(s.readScreen());
            case "tap": return s.tap(a.optString("text")) ? ok("tapped '" + a.optString("text") + "'") : fail("nothing on screen shows '" + a.optString("text") + "'");
            case "type_text": return s.type(a.optString("text")) ? ok("typed") : fail("no text field is focused");
            default: return s.scroll(!"up".equalsIgnoreCase(a.optString("direction"))) ? ok("scrolled") : fail("nothing to scroll");
        }
    }

    private JSONObject start(Intent i, String done) throws JSONException {
        try {
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(i);
            return ok(done);
        } catch (ActivityNotFoundException e) {
            return fail("no app on this phone can do that");
        } catch (SecurityException e) {
            return fail("Android blocked it: " + e.getMessage());
        }
    }

    private static JSONObject ok(String out) throws JSONException { return new JSONObject().put("ok", true).put("output", out); }
    private static JSONObject fail(String out) throws JSONException { return new JSONObject().put("ok", false).put("output", out); }
}
