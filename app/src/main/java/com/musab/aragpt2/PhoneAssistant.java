package com.musab.aragpt2;

import android.Manifest;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.SystemClock;
import android.provider.ContactsContract;
import android.provider.Settings;
import android.telephony.SmsManager;
import android.telephony.TelephonyManager;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Carries out the assistant's requests on the phone: everyday commands directly (open, call,
 * WhatsApp / SMS, alarms, switches ...) and anything else with {@link PhoneAgent} driving the
 * screen through {@link ScreenControlService}.
 */
final class PhoneAssistant {
    /** What the assistant needs from the screen the user is looking at. */
    interface Ui {
        boolean confirm(String title, String message);
        /** Index of the chosen option, or -1. */
        int choose(String title, String[] options);
        /** Asks for a runtime permission if needed; true when granted. */
        boolean permission(String permission);
        void progress(String line);
    }

    private static final String[] WHATSAPP_PACKAGES = {"com.whatsapp", "com.whatsapp.w4b"};
    private static final Pattern SEND_LABEL = Pattern.compile("^(?:send|إرسال|ارسال|أرسل|ارسل)$", Pattern.CASE_INSENSITIVE);
    private static final Pattern VOICE_CALL = Pattern.compile("^(?:voice call|audio call|call|مكالمة صوتية|اتصال صوتي|مكالمه صوتيه|اتصال)$", Pattern.CASE_INSENSITIVE);

    private final Context context;
    private final DeviceController device;
    private final Ui ui;
    /** Ask before sending a message, calling, or a screen tap that sends / pays / deletes. */
    volatile boolean confirmSends = true;
    private volatile PhoneAgent agent;
    private List<ContactMatcher.Contact> contacts;
    private long contactsAt;

    PhoneAssistant(Context context, DeviceController device, Ui ui) {
        this.context = context.getApplicationContext();
        this.device = device;
        this.ui = ui;
    }

    void cancel() {
        PhoneAgent a = agent;
        if (a != null) a.cancel();
    }

    // ------------------------------------------------------------------ everyday commands

    /** Runs the commands one after another and returns the reply for the user. */
    String runQuick(List<QuickCommands.Command> commands) throws Exception {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < commands.size(); i++) {
            if (i > 0) {
                ScreenControlService s = ScreenControlService.instance;
                if (s != null) s.waitForIdle(400, 2500); else SystemClock.sleep(800);
            }
            String r = runOne(commands.get(i));
            out.append(out.length() == 0 ? "" : "\n").append(r);
            ui.progress(r);
            if (r.startsWith("❌")) break;
        }
        return out.toString();
    }

    private String runOne(QuickCommands.Command c) throws Exception {
        switch (c.kind) {
            case "open_app": return reply(device.openApp(c.arg("name")), "✅ فتحت ", "❌ ما لقيت تطبيق باسم «" + c.arg("name") + "»");
            case "press": return press(c.arg("button"));
            case "flashlight": return reply(device.call("flashlight", new JSONObject().put("on", Boolean.parseBoolean(c.arg("on")))),
                    Boolean.parseBoolean(c.arg("on")) ? "🔦 شغّلت الكشاف" : "🔦 طفيت الكشاف", "❌ ");
            case "volume": return reply(device.call("volume", new JSONObject().put("level", c.arg("level"))), "🔊 ", "❌ ");
            case "media": return reply(device.call("media", new JSONObject().put("action", c.arg("action"))), "🎵 ", "❌ ");
            case "status": return reply(device.call("device_status", new JSONObject()), "🔋 ", "❌ ");
            case "camera": return reply(device.call("open_camera", new JSONObject()), "📷 فتحت الكاميرا", "❌ ");
            case "settings": return reply(device.call("open_settings", new JSONObject().put("page", c.arg("page"))), "⚙️ فتحت الإعدادات", "❌ ");
            case "alarm": {
                JSONObject a = new JSONObject().put("hour", Integer.parseInt(c.arg("hour"))).put("minute", Integer.parseInt(c.arg("minute"))).put("label", c.arg("label"));
                return reply(device.call("set_alarm", a), String.format(Locale.US, "⏰ ضبطت منبه الساعة %02d:%02d", a.getInt("hour"), a.getInt("minute")), "❌ ");
            }
            case "timer": {
                int s = Integer.parseInt(c.arg("seconds"));
                return reply(device.call("set_timer", new JSONObject().put("seconds", s).put("label", c.arg("label"))),
                        "⏱ مؤقت " + (s >= 60 ? s / 60 + " دقيقة" + (s % 60 > 0 ? " و" + s % 60 + " ثانية" : "") : s + " ثانية"), "❌ ");
            }
            case "web_search": return reply(device.call("web_search", new JSONObject().put("query", c.arg("query"))), "🔎 بحثت عن «" + c.arg("query") + "»", "❌ ");
            case "maps": return reply(device.call("navigate", new JSONObject().put("destination", c.arg("destination"))), "🗺 فتحت الطريق إلى «" + c.arg("destination") + "»", "❌ ");
            case "youtube": {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.youtube.com/results?search_query=" + Uri.encode(c.arg("query"))));
                return reply(device.start(i, "ok"), "▶️ بحثت في يوتيوب عن «" + c.arg("query") + "»", "❌ ");
            }
            case "type": {
                ScreenControlService s = ScreenControlService.instance;
                if (s == null) return needScreenControl();
                return s.typeFocused(c.arg("text")) ? "⌨️ كتبت" : "❌ ما في خانة كتابة على الشاشة";
            }
            case "toggle": return toggle(c.arg("setting"), Boolean.parseBoolean(c.arg("on")));
            case "call": return call(c);
            case "message": return message(c);
            default: return "❌ أمر غير معروف: " + c.kind;
        }
    }

    private static String reply(JSONObject r, String ok, String failPrefix) {
        if (r == null) return failPrefix + "غير مدعوم";
        String out = r.optString("output");
        if (!r.optBoolean("ok")) return failPrefix.equals("❌ ") ? "❌ " + out : failPrefix;
        return ok.endsWith(" ") ? ok + out.replaceFirst("^opened ", "") : ok;
    }

    private String press(String button) {
        ScreenControlService s = ScreenControlService.instance;
        if (s != null) return s.press(button) ? "✅ " + button : "❌ لم ينجح " + button;
        if (button.equals("home")) {
            context.startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return "✅ home";
        }
        return needScreenControl();
    }

    static String needScreenControl() {
        return "❌ هذا يحتاج «التحكم بالشاشة»: فعّل NewAl screen control من إعدادات إمكانية الوصول (زر 🖐).";
    }

    /** Wi-Fi / Bluetooth: Android only lets apps open the page, so the switch is flipped on screen. */
    private String toggle(String setting, boolean on) throws Exception {
        boolean bt = setting.equals("bluetooth");
        String name = bt ? "البلوتوث" : "الواي فاي";
        device.start(new Intent(bt ? Settings.ACTION_BLUETOOTH_SETTINGS : Settings.ACTION_WIFI_SETTINGS), "ok");
        ScreenControlService s = ScreenControlService.instance;
        if (s == null) return "⚙️ فتحت إعدادات " + name + " — فعّل التحكم بالشاشة (🖐) ليتم التبديل تلقائياً";
        s.waitForPackage("com.android.settings", 3000);
        s.waitForIdle(300, 2000);
        Pattern label = Pattern.compile(bt ? "(?i)bluetooth|بلوتوث" : "(?i)wi-?fi|واي ?فاي|wlan", Pattern.UNICODE_CASE);
        String r = s.setSwitch(label, on, 4000);
        if (r == null) return "⚙️ فتحت إعدادات " + name + " لكن ما لقيت المفتاح — بدّله يدوياً";
        SystemClock.sleep(400);
        s.press("back");
        return r.startsWith("already") ? "✅ " + name + (on ? " شغّال أصلاً" : " مطفي أصلاً") : "✅ " + (on ? "شغّلت " : "طفيت ") + name;
    }

    // ------------------------------------------------------------------ calls and messages

    private String countryIso() {
        TelephonyManager tm = (TelephonyManager) context.getSystemService(Context.TELEPHONY_SERVICE);
        String iso = tm == null ? "" : tm.getSimCountryIso();
        if ((iso == null || iso.isEmpty()) && tm != null) iso = tm.getNetworkCountryIso();
        return iso == null || iso.isEmpty() ? Locale.getDefault().getCountry() : iso;
    }

    private synchronized List<ContactMatcher.Contact> contacts() {
        if (contacts != null && SystemClock.elapsedRealtime() - contactsAt < 60_000) return contacts;
        List<ContactMatcher.Contact> list = new ArrayList<>();
        try (Cursor c = context.getContentResolver().query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                new String[]{ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER},
                null, null, null)) {
            while (c != null && c.moveToNext()) {
                String name = c.getString(0), number = c.getString(1);
                if (name != null && number != null) list.add(new ContactMatcher.Contact(name, number));
            }
        }
        contacts = list;
        contactsAt = SystemClock.elapsedRealtime();
        return list;
    }

    /** A number, or the contact the user means (asks which one when several match); null with {@code why} set. */
    private ContactMatcher.Contact recipient(String who, String[] why) {
        if (ContactMatcher.isNumber(who)) return new ContactMatcher.Contact(who, who);
        if (!ui.permission(Manifest.permission.READ_CONTACTS)) {
            why[0] = "❌ أحتاج إذن جهات الاتصال لأعرف رقم «" + who + "»";
            return null;
        }
        List<ContactMatcher.Contact> found = ContactMatcher.find(who, contacts());
        return pick(who, found, why);
    }

    private ContactMatcher.Contact pick(String who, List<ContactMatcher.Contact> found, String[] why) {
        if (found.isEmpty()) {
            why[0] = "❌ ما لقيت «" + who.replaceFirst("^[لب](?=\\S{2})", "") + "» بجهات الاتصال";
            return null;
        }
        if (found.size() == 1) return found.get(0);
        String[] options = new String[found.size()];
        for (int i = 0; i < options.length; i++) options[i] = found.get(i).name + " — " + found.get(i).number;
        int i = ui.choose("مين تقصد؟", options);
        if (i < 0) { why[0] = "أُلغي"; return null; }
        return found.get(i);
    }

    private String call(QuickCommands.Command c) throws Exception {
        String[] why = {""};
        ContactMatcher.Contact to = recipient(c.arg("who"), why);
        if (to == null) return why[0];
        if (c.arg("app").equals("whatsapp")) return whatsappCall(to);
        if (confirmSends && !ui.confirm("📞 اتصال", "اتصال بـ " + to.name + (to.name.equals(to.number) ? "" : "\n" + to.number) + "؟")) return "أُلغي الاتصال";
        Uri tel = Uri.parse("tel:" + Uri.encode(to.number));
        if (ui.permission(Manifest.permission.CALL_PHONE)) {
            device.start(new Intent(Intent.ACTION_CALL, tel), "ok");
            return "📞 عم اتصل بـ " + to.name;
        }
        device.start(new Intent(Intent.ACTION_DIAL, tel), "ok");
        return "📞 فتحت الاتصال بـ " + to.name + " — اضغط اتصال";
    }

    private String installedWhatsapp() {
        PackageManager pm = context.getPackageManager();
        for (String p : WHATSAPP_PACKAGES) {
            try { pm.getPackageInfo(p, 0); return p; } catch (PackageManager.NameNotFoundException ignored) {}
        }
        return null;
    }

    private boolean openWhatsappChat(String pkg, ContactMatcher.Contact to, String text) {
        String url = "https://api.whatsapp.com/send?phone=" + ContactMatcher.international(to.number, countryIso())
                + (text.isEmpty() ? "" : "&text=" + Uri.encode(text));
        try {
            context.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(pkg).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return true;
        } catch (ActivityNotFoundException e) {
            return false;
        }
    }

    private String whatsappCall(ContactMatcher.Contact to) {
        String pkg = installedWhatsapp();
        if (pkg == null) return "❌ واتساب غير مثبت";
        if (confirmSends && !ui.confirm("📞 مكالمة واتساب", "مكالمة واتساب مع " + to.name + "؟")) return "أُلغي الاتصال";
        if (!openWhatsappChat(pkg, to, "")) return "❌ ما قدرت افتح واتساب";
        ScreenControlService s = ScreenControlService.instance;
        if (s == null) return "💬 فتحت محادثة " + to.name + " — اضغط زر الاتصال";
        s.waitForPackage(pkg, 8000);
        return s.clickFirst(new String[]{pkg + ":id/voice_call", pkg + ":id/menuitem_voice_call"}, VOICE_CALL, 6000)
                ? "📞 عم اتصل بـ " + to.name + " على واتساب" : "💬 فتحت محادثة " + to.name + " — اضغط زر الاتصال";
    }

    private String message(QuickCommands.Command c) throws Exception {
        String[] why = {""};
        ContactMatcher.Contact to;
        String text;
        if (!c.arg("who").isEmpty()) {
            to = recipient(c.arg("who"), why);
            text = c.arg("text");
        } else {
            // "لسامر مرحبا كيفك": the contact list decides where the name ends.
            String rest = c.arg("rest");
            String first = rest.split(" ")[0];
            if (ContactMatcher.isNumber(first)) {
                to = new ContactMatcher.Contact(first, first);
                text = rest.substring(first.length()).trim();
            } else if (!ui.permission(Manifest.permission.READ_CONTACTS)) {
                return "❌ أحتاج إذن جهات الاتصال لأعرف لمين الرسالة";
            } else {
                ContactMatcher.Split sp = ContactMatcher.split(rest, contacts());
                to = pick(first, sp.contacts, why);
                text = sp.text;
            }
        }
        if (to == null) return why[0];
        if (text.isEmpty()) return "✍️ شو بدك تكتب لـ " + to.name + "؟ مثلاً: ابعت لـ" + to.name + ": مرحبا";

        String app = c.arg("app");
        String wa = installedWhatsapp();
        if (app.isEmpty()) app = wa != null ? "whatsapp" : "sms";
        String via = app.equals("whatsapp") ? "واتساب" : "رسالة SMS";
        if (confirmSends && !ui.confirm("✉️ إرسال عبر " + via, "إلى: " + to.name + "\n\n«" + text + "»")) return "أُلغي الإرسال";

        if (app.equals("sms")) {
            if (ui.permission(Manifest.permission.SEND_SMS)) {
                SmsManager sms = android.os.Build.VERSION.SDK_INT >= 31 ? context.getSystemService(SmsManager.class) : SmsManager.getDefault();
                ArrayList<String> parts = sms.divideMessage(text);
                sms.sendMultipartTextMessage(to.number, null, parts, null, null);
                return "✅ أرسلت SMS لـ " + to.name;
            }
            Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(to.number))).putExtra("sms_body", text);
            device.start(i, "ok");
            return "✉️ الرسالة جاهزة لـ " + to.name + " — اضغط إرسال";
        }
        if (wa == null) return "❌ واتساب غير مثبت";
        if (!openWhatsappChat(wa, to, text)) return "❌ ما قدرت افتح واتساب";
        ScreenControlService s = ScreenControlService.instance;
        if (s == null) return "💬 الرسالة جاهزة بمحادثة " + to.name + " — اضغط إرسال (فعّل 🖐 ليرسل تلقائياً)";
        s.waitForPackage(wa, 8000);
        boolean sent = s.clickFirst(new String[]{wa + ":id/send"}, SEND_LABEL, 8000);
        return sent ? "✅ أرسلت لـ " + to.name + " على واتساب" : "💬 الرسالة جاهزة بمحادثة " + to.name + " — اضغط إرسال";
    }

    // ------------------------------------------------------------------ anything else: the screen agent

    PhoneAgent.Result runAgent(String goal, PhoneAgent.Model model, PhoneAgent.Listener listener) throws Exception {
        ScreenControlService s = ScreenControlService.instance;
        if (s == null) return new PhoneAgent.Result(false, needScreenControl(), 0);
        // Start from the home screen, not from this app's own chat.
        s.press("home");
        s.waitForIdle(300, 1500);
        PhoneAgent a = new PhoneAgent(new Device(s), model, listener);
        a.confirmRisky = confirmSends;
        agent = a;
        try {
            return a.run(goal);
        } finally {
            agent = null;
        }
    }

    /** The phone as {@link PhoneAgent} sees it. */
    private final class Device implements PhoneAgent.Device {
        private final ScreenControlService s;

        Device(ScreenControlService s) { this.s = s; }

        @Override public ScreenState observe() {
            s.waitForIdle(350, 2500);
            return s.snapshot();
        }

        @Override public String perform(PhoneAction a, ScreenState on) throws Exception {
            switch (a.kind) {
                case OPEN_APP: {
                    JSONObject r = device.openApp(a.text);
                    if (!r.optBoolean("ok")) return "failed: " + r.optString("output");
                    String pkg = r.optString("package");
                    if (!pkg.isEmpty() && !s.waitForPackage(pkg, 6000)) return "opened, still loading";
                    return r.optString("output");
                }
                case TAP: return on.byId(a.target) == null ? "failed: no element " + a.target + " on this screen" : s.tap(a.target) ? "ok" : "failed: could not tap";
                case LONG_PRESS: return s.longPress(a.target) ? "ok" : "failed: could not long-press";
                case TYPE: return on.byId(a.target) == null ? "failed: no element " + a.target : s.type(a.target, a.text) ? "ok" : "failed: could not type there";
                case ENTER: return s.enter() ? "ok" : "failed: no enter key here; tap the search or send button";
                case SCROLL: return s.scroll(a.text) ? "ok" : "failed: nothing to scroll";
                case BACK: return s.press("back") ? "ok" : "failed";
                case HOME: return s.press("home") ? "ok" : "failed";
                case WAIT: SystemClock.sleep(1500); return "waited";
                default: return "ok";
            }
        }
    }
}
