package com.musab.aragpt2;

import java.util.Locale;

/** Scores how well a spoken app name ("الواتس", "يوتيوب", "insta") fits an installed app. */
final class AppMatcher {
    private AppMatcher() {}

    /** Arabic / short names → words found in the app's English label or package name. */
    private static final String[][] ALIASES = {
            {"يوتيوب", "youtube"}, {"واتساب", "whatsapp"}, {"واتس", "whatsapp"}, {"وتس", "whatsapp"},
            {"فيسبوك", "facebook"}, {"فيس", "facebook"}, {"ماسنجر", "messenger"}, {"انستغرام", "instagram"},
            {"انستقرام", "instagram"}, {"انستا", "instagram"}, {"insta", "instagram"}, {"تلغرام", "telegram"},
            {"تيليجرام", "telegram"}, {"تليجرام", "telegram"}, {"تيك توك", "tiktok"}, {"تيكتوك", "tiktok"},
            {"سناب", "snapchat"}, {"سناب شات", "snapchat"}, {"تويتر", "twitter"}, {"اكس", "twitter"},
            {"كروم", "chrome"}, {"المتصفح", "browser"}, {"متصفح", "browser"}, {"خرائط", "maps"}, {"الخرائط", "maps"},
            {"ماب", "maps"}, {"جيميل", "gmail"}, {"الايميل", "gmail"}, {"البريد", "mail"}, {"متجر", "play store"},
            {"بلاي", "play store"}, {"جوجل بلاي", "play store"}, {"الكاميرا", "camera"}, {"كاميرا", "camera"},
            {"الاعدادات", "settings"}, {"اعدادات", "settings"}, {"الساعه", "clock"}, {"المنبه", "clock"},
            {"الحاسبه", "calculator"}, {"حاسبه", "calculator"}, {"الاله الحاسبه", "calculator"}, {"المعرض", "gallery"},
            {"الصور", "photos"}, {"الاستوديو", "gallery"}, {"الرسائل", "messages"}, {"الهاتف", "phone"},
            {"جهات الاتصال", "contacts"}, {"الاسماء", "contacts"}, {"التقويم", "calendar"}, {"الملفات", "files"},
            {"الموسيقي", "music"}, {"نتفلكس", "netflix"}, {"شاهد", "shahid"}, {"سبوتيفاي", "spotify"},
            {"انغامي", "anghami"}, {"زووم", "zoom"}, {"تيمز", "teams"}, {"تيرمكس", "termux"}, {"ترمكس", "termux"},
            {"الترجمه", "translate"}, {"مترجم", "translate"}, {"درايف", "drive"}, {"الطقس", "weather"},
            {"المسجل", "recorder"}, {"مسجل الصوت", "recorder"}, {"الملاحظات", "notes"}, {"كيب", "keep"},
            {"اوبر", "uber"}, {"كريم", "careem"}, {"ديسكورد", "discord"}, {"ريديت", "reddit"}, {"لينكدان", "linkedin"},
    };

    private static String key(String s) { return ArabicText.key(s).replaceFirst("^ال", ""); }

    /** 0-100; 0 means not this app. */
    static int score(String wanted, String label, String packageName) {
        String w = key(wanted), l = key(label), pkg = packageName.toLowerCase(Locale.ROOT);
        if (w.isEmpty()) return 0;
        int s = direct(w, l, pkg);
        String nw = ArabicText.norm(wanted).replaceFirst("^(?:تطبيق|برنامج)\\s+", "");
        for (String[] a : ALIASES) {
            if (key(a[0]).equals(key(nw)) || key(a[0]).equals(w)) s = Math.max(s, direct(key(a[1]), l, pkg) - 5);
        }
        return s;
    }

    private static int direct(String w, String l, String pkg) {
        if (l.equals(w)) return 100;
        if (l.startsWith(w) && w.length() >= 2) return 80;
        if (l.contains(w) && w.length() >= 3) return 60;
        if (w.contains(l) && l.length() > 2) return 50;
        if (pkg.contains(w) && w.length() >= 3) return 45;
        return 0;
    }
}
