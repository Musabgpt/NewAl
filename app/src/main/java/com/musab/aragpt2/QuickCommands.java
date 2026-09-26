package com.musab.aragpt2;

import java.time.LocalTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Everyday phone commands understood without a model, in Arabic (MSA and Levantine) and
 * English: open an app, call, send a WhatsApp / SMS message, alarms, timers, flashlight, volume,
 * media, Wi-Fi / Bluetooth, settings pages, web / YouTube / maps search, system buttons.
 * They run in milliseconds and cannot be misread by a small model; anything else goes to
 * {@link PhoneAgent}.
 */
final class QuickCommands {
    private QuickCommands() {}

    static final class Command {
        final String kind;
        final Map<String, String> args = new LinkedHashMap<>();

        Command(String kind, String... kv) {
            this.kind = kind;
            for (int i = 0; i + 1 < kv.length; i += 2) args.put(kv[i], kv[i + 1]);
        }

        String arg(String k) { String v = args.get(k); return v == null ? "" : v; }

        @Override public String toString() { return kind + args; }
    }

    private static final int F = Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE;
    /** Word boundary that also works between Arabic letters (Java's \\b does not by default). */
    private static final String W = "(?:(?<=[\\p{L}\\p{N}])(?![\\p{L}\\p{N}])|(?<![\\p{L}\\p{N}])(?=[\\p{L}\\p{N}]))";

    /** Patterns are written in plain Arabic and normalised the same way as the input (ى→ي, ة→ه ...). */
    private static Pattern p(String regex) {
        return Pattern.compile(regex.replace('ى', 'ي').replace('ة', 'ه').replace('أ', 'ا').replace('إ', 'ا')
                .replace('آ', 'ا').replace('ئ', 'ي').replace('ؤ', 'و'), F);
    }

    private static boolean is(String t, String regex) { return p(regex).matcher(t).matches(); }

    // Verbs and nouns are matched on ArabicText.norm() output: no hamza forms, ة→ه, ى→ي.
    private static final String OPEN = "(?:افتح(?:لي|ي)?|فتح(?:لي)?|شغل(?:لي|ي)?|open|launch|start|run)";
    private static final String ON = "(?:شغل(?:لي|ي)?|ولع(?:لي|ي)?|افتح(?:لي|ي)?|فعل(?:لي|ي)?|turn on|switch on|enable|on)";
    private static final String OFF = "(?:طفي(?:لي)?|اطفي(?:لي)?|اطف(?:ئ|ي)?|سكر(?:لي|ي)?|اقفل(?:لي|ي)?|قفل(?:لي)?|وقف(?:لي)?|عطل(?:لي|ي)?|turn off|switch off|disable|off)";
    private static final String CALL = "(?:اتصل(?:ي)?|اتصال|كلم(?:لي|ي)?|دق(?:لي)?|رن(?:لي)?|call|dial|phone)";
    private static final String SEND = "(?:ارسل(?:لي|ي)?|ابعت(?:لي|ي)?|ابعث(?:لي|ي)?|بعت(?:لي)?|راسل(?:ي)?|اكتب(?:لي)? رساله|send|message|text|msg|whatsapp)";
    private static final String WHATSAPP = "(?:(?:على|ع|عال|بال|ب|عبر|في|من|on|via|by|with)\\s*)?(?:ال)?(?:واتس ?اب|واتس|وتس|whats ?app|wa)" + W;
    private static final String SMS = "(?:(?:ك|ب|بال|عبر|on|via|by|as an?)\\s*)?(?:رساله نصيه|نصيه|اس ام اس|sms|text message)" + W;
    private static final String TELEGRAM = "(?:(?:على|ع|عال|بال|ب|عبر|on|via)\\s*)?(?:ال)?(?:تيليجرام|تلغرام|تليجرام|telegram)" + W;

    private static final String VERB = "(?:افتح|ابعت|ارسل|ابعث|اتصل|كلم|دق|ابحث|دور|شغل|ولع|طفي|اطفي|سكر|اقفل|اضبط|حط|ارفع|"
            + "وطي|علي|نبهني|صحيني|ذكرني|خذني|وديني|ارجع|open|call|send|text|message|search|play|turn|set|take|go|lock|mute)";
    /** Joins between commands: "ثم", "وبعدين", "then", or "و" / "and" / "," right before a verb. */
    private static final Pattern SPLIT = p("\\s*(?:" + W + "ثم" + W + "|" + W + "وبعدين" + W + "|" + W + "وبعدها" + W + "|" + W + "بعدين" + W + "|" + W + "and then" + W + "|" + W + "then" + W + ")\\s*"
            + "|\\s*(?:,|،|" + W + "and" + W + ")\\s*(?=" + VERB + ")|\\s+و(?=" + VERB + ")");

    /** The commands in {@code text}, or null when any part of it is not an everyday command. */
    static List<Command> parse(String text, LocalTime now) {
        String t = ArabicText.norm(text).replaceAll("[.!?؟]+$", "").trim();
        if (t.isEmpty()) return null;
        List<Command> out = new ArrayList<>();
        // "افتح يوتيوب وابحث عن ..." is one YouTube search, not "open" + "search".
        if (is(t, ".*(?:يوتيوب|youtube).*(?:ابحث|دور|فتش|شغل|search|play|find).*")) {
            Command whole = one(t, now);
            if (whole != null && whole.kind.equals("youtube")) { out.add(whole); return out; }
        }
        Matcher m = SPLIT.matcher(t);
        int start = 0;
        while (true) {
            boolean more = m.find();
            String part = t.substring(start, more ? m.start() : t.length()).trim();
            Command c = part.isEmpty() ? null : one(part, now);
            // A message takes the rest of the request as its text ("tell him I'm coming and then call").
            if (c != null && c.kind.equals("message") && more) {
                c = one(t.substring(start).trim(), now);
                if (c != null) out.add(c);
                return c == null ? null : out;
            }
            if (c == null) return null;
            out.add(c);
            if (!more) return out;
            start = m.end();
        }
    }

    static Command one(String t, LocalTime now) {
        Matcher m;
        // --- system buttons and screen
        if (is(t, "(?:ارجع|رجوع|رجعني|go back|back)")) return new Command("press", "button", "back");
        if (is(t, "(?:(?:روح|رجعني|خذني)? ?(?:ع|على|ل|لل)?(?:ال)?شاشه الرئيسيه|الرئيسيه|home|go home|home screen)")) return new Command("press", "button", "home");
        if (is(t, "(?:(?:افتح )?(?:ال)?اشعارات|(?:open )?notifications)")) return new Command("press", "button", "notifications");
        if (is(t, "(?:(?:ال)?تطبيقات (?:ال)?اخيره|recent apps|recents)")) return new Command("press", "button", "recents");
        if (is(t, "(?:(?:اقفل|قفل|سكر) (?:ال)?شاشه|lock(?: the)? screen|lock)")) return new Command("press", "button", "lock");
        if (is(t, ".*(?:سكرين ?شوت|لقطه (?:ال)?شاشه|صور(?:لي)? (?:ال)?شاشه|screenshot|screen shot).*")) return new Command("press", "button", "screenshot");

        // --- flashlight
        if ((m = p("^(?:(" + OFF + ")|(" + ON + "))?\\s*(?:ال)?(?:كشاف|فلاش|ضو|بيل|torch|flashlight|flash ?light)(?:\\s*(on|off))?$").matcher(t)).find()) {
            boolean off = m.group(1) != null || "off".equalsIgnoreCase(m.group(3));
            return new Command("flashlight", "on", off ? "false" : "true");
        }
        // --- Wi-Fi / Bluetooth (Android lets apps open the switch, the assistant then flips it on screen)
        if ((m = p("^(?:(" + OFF + ")|(" + ON + "))\\s*(?:ال)?(واي ?فاي|wi-?fi|wifi|نت|انترنت|بلوتوث|bluetooth|بلو توث)$").matcher(t)).find()) {
            String what = m.group(3).matches("(?i).*(بلو|blue).*") ? "bluetooth" : "wifi";
            return new Command("toggle", "setting", what, "on", m.group(1) != null ? "false" : "true");
        }
        // --- volume
        if ((m = p("^(?:(علي|ارفع|زيد|كبر|raise|turn up|increase)|(وطي|اخفض|نزل|قلل|خفض|خفف|lower|turn down|decrease)|(اكتم|سكت|mute))\\s*(?:ال)?(?:صوت|volume)(?:\\s*(?:ل|الى|to)?\\s*(\\d{1,3}))?$").matcher(t)).find()) {
            String level = m.group(4) != null ? m.group(4) : m.group(1) != null ? "up" : m.group(2) != null ? "down" : "mute";
            return new Command("volume", "level", level);
        }
        if ((m = p("^(?:(?:ال)?صوت|volume)\\s*(?:على|ع|ل|الى|to)?\\s*(\\d{1,3})\\s*%?$").matcher(t)).find()) return new Command("volume", "level", m.group(1));
        if (is(t, "(?:(?:حط|خلي) (?:ال)?(?:جوال|تلفون|موبايل) )?(?:ع|على)? ?(?:ال)?صامت|silent|mute")) return new Command("volume", "level", "mute");
        // --- media
        if (is(t, "(?:pause|(?:وقف|اوقف|stop) (?:ال)?(?:اغنيه|موسيقي|اغاني|music|song|the music))")) return new Command("media", "action", "pause");
        if (is(t, "(?:(?:ال)?اغنيه (?:ال)?(?:جايه|تاليه|بعدها)|(?:ال)?تالي|next(?: song| track)?|skip)")) return new Command("media", "action", "next");
        if (is(t, "(?:(?:ال)?اغنيه (?:ال)?(?:سابقه|قبلها)|(?:ال)?سابق|previous(?: song| track)?)")) return new Command("media", "action", "previous");
        if (is(t, "(?:(?:شغل|كمل) (?:ال)?(?:موسيقي|اغاني|اغنيه)|play(?: music)?|resume(?: music)?)")) return new Command("media", "action", "play");
        // --- battery / status
        if (is(t, ".*(?:بطاريه|الشحن|battery).*") && is(t, ".*(?:كم|قديش|شقد|ايش|شو|قد ايش|نسبه|how much|level|status|what|\\?).*"))
            return new Command("status");

        // --- timers ("مؤقت 5 دقايق", "ذكرني بعد 10 دقائق", "timer for 2 minutes")
        if ((m = p("^(?:(?:حط|اضبط|شغل|ابدا|set|start)\\s*)?(?:(?:ال)?مؤقت|موقت|تايمر|timer|عداد|ذكرني|نبهني|صحيني|remind me)\\s*(?:بعد|ل|لمده|for|in|after|من)?\\s*(.+?)(?:\\s+(?:ل|عشان|مشان|انو|ان|to|for|about)\\s+(.+))?$").matcher(t)).find()) {
            int seconds = durationSeconds(m.group(1));
            if (seconds > 0) return new Command("timer", "seconds", Integer.toString(seconds), "label", m.group(2) == null ? "" : m.group(2));
        }
        // --- alarms ("منبه الساعه 7 الصبح", "صحيني 6:30", "set an alarm for 7 pm")
        if ((m = p("^(?:(?:حط|اضبط|ضبط|اعمل|set|make)\\s*(?:لي)?\\s*)?(?:(?:ال)?منبه|نبهني|صحيني|فيقني|قومني|alarm|an alarm|wake me(?: up)?)\\s*(?:على|ع|ل|الساعه|at|for|بكره|بكرا|tomorrow)?\\s*(.+)$").matcher(t)).find()) {
            int[] hm = time(m.group(1), now);
            if (hm != null) return new Command("alarm", "hour", Integer.toString(hm[0]), "minute", Integer.toString(hm[1]), "label", "");
        }

        // --- calls ("اتصل بأحمد", "call mom", "دق على 0991234567")
        // The name keeps an attached ب / ل ("باحمد"); ContactMatcher.variants() tries it with and without.
        if ((m = p("^" + CALL + "\\s+(?:(?:على|ع|مع|ب|ل|to)\\s+)?(.+)$").matcher(t)).find()) {
            String who = stripChannel(m.group(1));
            if (!who.isEmpty()) return new Command("call", "who", who, "app", is(t, ".*" + WHATSAPP + ".*") ? "whatsapp" : "phone");
        }
        // --- messages
        if ((m = p("^" + SEND + "\\s*(?:(?:ال)?رساله|مسج|مسيج|a message|message)?\\s*(.+)$").matcher(t)).find()) {
            String rest = m.group(1);
            String app = is(rest, ".*" + WHATSAPP + ".*") || t.startsWith("whatsapp") ? "whatsapp"
                    : is(rest, ".*" + TELEGRAM + ".*") ? "telegram" : is(rest, ".*" + SMS + ".*") ? "sms" : "";
            // Telegram has no link that opens a chat by phone number; the screen agent does it.
            if (app.equals("telegram")) return null;
            Command c = message(stripChannel(rest));
            if (c != null) { c.args.put("app", app); return c; }
        }

        // --- search / maps / YouTube
        if (is(t, ".*(?:يوتيوب|youtube).*") && is(t, "^(?:" + OPEN + "|ابحث|دور|فتش|شغل|search|play|find|يوتيوب|youtube|ال).*")) {
            String q = youtubeQuery(t);
            if (!q.isEmpty()) return new Command("youtube", "query", q);
        }
        if ((m = p("^(?:خذني|وديني|وصلني|روح|طريق|الطريق|اتجاهات|navigate|directions|take me|drive)\\s*(?:ل|لل|على|ع|الى|to|for)?\\s*(.+)$").matcher(t)).find())
            return new Command("maps", "destination", m.group(1));
        if ((m = p("^(?:وين|اين|where is|where's)\\s+(.+)$").matcher(t)).find() && m.group(1).matches(".*(?:مطعم|صيدليه|محطه|مستشفي|بنك|سوق|مول|restaurant|pharmacy|station|hospital|bank|near).*"))
            return new Command("maps", "destination", m.group(1));
        if ((m = p("^(?:ابحث|دور|فتش|غوغل|جوجل|search|google|look up)(?:لي)?\\s*(?:عن|على|ع|في (?:ال)?نت|for|about)?\\s*(.+)$").matcher(t)).find())
            return new Command("web_search", "query", m.group(1));

        // --- settings pages
        if ((m = p("^(?:" + OPEN + "\\s*)?(?:ال)?(?:اعدادات|settings)\\s*(?:ال)?(.*)$").matcher(t)).find()) {
            return new Command("settings", "page", settingsPage(m.group(1)));
        }
        // --- camera
        if (is(t, "(?:" + OPEN + " )?(?:ال)?(?:كاميرا|camera)|(?:صور|صورني|take a (?:photo|picture|selfie)|سيلفي|selfie)")) return new Command("camera");
        // --- typing into the focused field
        if ((m = p("^(?:اكتب|type)\\s*[:：]\\s*(.+)$").matcher(t)).find()) return new Command("type", "text", m.group(1));
        // --- open an app (last: "open X" for any installed app)
        if ((m = p("^" + OPEN + "\\s+(?:(?:ال)?تطبيق|برنامج|app|the app)?\\s*(.+?)(?:\\s+app)?$").matcher(t)).find()) {
            String name = m.group(1).trim();
            if (!name.isEmpty() && name.split(" ").length <= 4) return new Command("open_app", "name", name);
        }
        return null;
    }

    private static final Pattern PHONE_TASK = p("^(?:" + OPEN + "|" + CALL + "|" + SEND + "|" + VERB + "|اضغط|دوس|اختار|اختر|احجز|اطلب|حمل|نزل|ثبت|احذف|امسح|سجل|"
            + "غير|فعل|عطل|رد|علق|انشر|شارك|حول|ادفع|press|tap|click|book|order|install|download|delete|remove|reply|post|share|"
            + "enable|disable|change|check|find|read)(?:لي|ي)?" + W
            + "|.*(?:واتس|يوتيوب|انستا|فيسبوك|تلغرام|تيليجرام|تيك ?توك|سناب|تويتر|جيميل|الاعدادات|الشاشه|whatsapp|youtube|instagram|facebook|telegram|tiktok|snapchat|gmail|settings|screen).*");

    /** A request to do something on the phone (for the screen agent), as opposed to a chat question. */
    static boolean looksLikePhoneTask(String text) {
        return PHONE_TASK.matcher(ArabicText.norm(text)).find();
    }

    private static final Pattern YT_FILLER = p("^و?(?:" + OPEN + "|ابحث|دور|فتش|شغل|search|play|find|look|لي|و|and|عن|على|ع|في|بال|من|ب|for|on|in|at|up)(?:\\s+|$)|\\s+(?:على|ع|في|بال|من|on|in|at)$");

    /** "افتح يوتيوب وابحث عن اغاني فيروز" → "اغاني فيروز". */
    static String youtubeQuery(String t) {
        String q = p("(?:ال)?يوتيوب|youtube").matcher(t).replaceAll(" ").replaceAll("\\s+", " ").trim();
        for (String prev = null; !q.equals(prev); ) { prev = q; q = YT_FILLER.matcher(q).replaceAll("").trim(); }
        return q;
    }

    private static String stripChannel(String s) {
        String r = p(WHATSAPP).matcher(s).replaceAll(" ");
        r = p(TELEGRAM).matcher(r).replaceAll(" ");
        return p(SMS).matcher(r).replaceAll(" ").replaceAll("\\s+", " ").trim();
    }

    private static final Pattern MESSAGE_SEP = p("\\s*(?::|\\s-\\s|" + W + "انو" + W + "|" + W + "انه" + W + "|" + W + "انها" + W + "|" + W + "قله" + W + "|" + W + "قلو" + W + "|" + W + "قلها" + W + "|" + W + "قوله" + W + "|" + W + "قولها" + W + "|" + W + "يقول" + W + "|" + W + "وقله" + W + "|" + W + "وقلو" + W + "|" + W + "وقلها" + W + "|" + W + "فيها" + W + "|" + W + "that" + W + "|" + W + "saying" + W + ")\\s*");

    /**
     * "لأحمد: انا جاي" → who=أحمد, text=انا جاي. Without a separator the recipient and the text
     * are split later against the contact list ({@link ContactMatcher#split}).
     */
    static Command message(String rest) {
        String r = rest.trim();
        Matcher sep = MESSAGE_SEP.matcher(r);
        String who, text = "";
        if (sep.find() && sep.start() > 0) {
            who = r.substring(0, sep.start());
            text = r.substring(sep.end()).trim();
        } else who = r;
        who = who.replaceFirst("^(?:an?\\s+)?(?:(?:ال)?رساله|مسج|message|msg)?\\s*", "").replaceFirst("^(?:الي|to|for|ل)\\s+", "").trim();
        if (who.isEmpty()) return null;
        return text.isEmpty() ? new Command("message", "rest", who) : new Command("message", "who", who, "text", text);
    }

    private static String settingsPage(String s) {
        String t = s.trim();
        if (is(t, ".*(?:واي ?فاي|wi-?fi|wifi|نت|انترنت|شبكه|network).*")) return "wifi";
        if (is(t, ".*(?:بلوتوث|bluetooth).*")) return "bluetooth";
        if (is(t, ".*(?:شاشه|سطوع|عرض|display|brightness).*")) return "display";
        if (is(t, ".*(?:صوت|رنين|sound).*")) return "sound";
        if (is(t, ".*(?:بطاريه|battery).*")) return "battery";
        if (is(t, ".*(?:تطبيقات|apps).*")) return "apps";
        if (is(t, ".*(?:موقع|gps|location).*")) return "location";
        if (is(t, ".*(?:اشعارات|notifications).*")) return "notifications";
        if (is(t, ".*(?:وصول|accessibility).*")) return "accessibility";
        return "main";
    }

    /** "5 دقايق", "ساعه ونص", "30 ثانيه", "2 minutes" → seconds, or -1. */
    static int durationSeconds(String s) {
        String t = ArabicText.norm(s);
        if (is(t, "(?:ربع ساعه|quarter (?:of an )?hour)")) return 900;
        if (is(t, "(?:نص ساعه|نصف ساعه|half (?:an )?hour)")) return 1800;
        if (is(t, "(?:ساعه|hour|an hour|one hour)")) return 3600;
        if (is(t, "(?:ساعتين|two hours)")) return 7200;
        if (is(t, "(?:ساعه ونص|ساعه و نص|an hour and a half)")) return 5400;
        if (is(t, "(?:دقيقه|minute|a minute|one minute)")) return 60;
        if (is(t, "(?:دقيقتين|two minutes)")) return 120;
        Matcher m = p("^(\\S+)\\s*(ثانيه|ثواني|ثوان|ث|sec|secs|second|seconds|s|دقيقه|دقايق|دقائق|دقيقة|د|min|mins|minute|minutes|m|ساعه|ساعات|hour|hours|h)(?:\\s*(?:و|and)\\s*(نص|نصف|ربع|half|a half))?$").matcher(t);
        if (!m.find()) return -1;
        int n = ArabicText.number(m.group(1));
        if (n <= 0) return -1;
        String unit = m.group(2);
        int mul = is(unit, "ثانيه|ثواني|ثوان|ث|sec|secs|second|seconds|s") ? 1
                : is(unit, "ساعه|ساعات|hour|hours|h") ? 3600 : 60;
        int extra = m.group(3) == null ? 0 : m.group(3).matches("ربع") ? mul / 4 : mul / 2;
        return n * mul + extra;
    }

    /** "7", "7:30", "7 ونص", "6 الا ربع", "7 الصبح", "8 pm" → {hour 0-23, minute}, the next such time after now. */
    static int[] time(String s, LocalTime now) {
        String t = ArabicText.norm(s).replaceFirst("^(?:الساعه|ساعه|at)\\s*", "");
        Matcher m = p("^(\\d{1,2}|\\S+?)(?:\\s*[:.]\\s*(\\d{2}))?"
                + "(?:\\s*(?:(?:و|and)\\s*(نص|نصف|ربع|half|quarter|\\d{1,2}(?:\\s*(?:دقيقه|دقايق|دقائق))?)|(?:الا|ال|to)\\s*(ربع|quarter|\\d{1,2}(?:\\s*(?:دقيقه|دقايق|دقائق))?)))?"
                + "\\s*(الصبح|الصباح|صباحا|صبحا|بكير|الفجر|فجرا|am|a\\.m\\.|in the morning|morning|الظهر|ظهرا|العصر|عصرا|المسا|المساء|مساء|مسا|بالليل|الليل|ليلا|pm|p\\.m\\.|in the evening|evening|at night|tonight|الليله)?\\s*(?:بكره|بكرا|tomorrow)?$").matcher(t);
        if (!m.find()) return null;
        int h = ArabicText.number(m.group(1));
        if (h < 0 || h > 23) return null;
        int min = 0;
        if (m.group(2) != null) min = Integer.parseInt(m.group(2));
        else if (m.group(3) != null) min = m.group(3).matches("نص|نصف|half") ? 30 : m.group(3).matches("ربع|quarter") ? 15 : Integer.parseInt(m.group(3).replaceAll("\\D", ""));
        else if (m.group(4) != null) {
            int before = m.group(4).matches("ربع|quarter") ? 15 : Integer.parseInt(m.group(4).replaceAll("\\D", ""));
            h = (h + 23) % 24;
            min = 60 - before;
        }
        if (min < 0 || min > 59) return null;
        String part = m.group(5) == null ? "" : m.group(5).toLowerCase(Locale.ROOT);
        boolean am = is(part, "الصبح|الصباح|صباحا|صبحا|بكير|الفجر|فجرا|am|a\\.m\\.|in the morning|morning");
        boolean pm = !part.isEmpty() && !am;
        if (pm && h < 12) h += 12;
        else if (am && h == 12) h = 0;
        else if (!am && !pm && h <= 12) {
            // No am/pm: the next time this clock time comes round.
            LocalTime a = LocalTime.of(h % 12, min), b = LocalTime.of(h % 12 + 12, min);
            h = a.isAfter(now) ? h % 12 : b.isAfter(now) ? h % 12 + 12 : h % 12;
        }
        return new int[]{h, min};
    }
}
