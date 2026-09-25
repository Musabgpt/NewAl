package com.musab.aragpt2;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Separates ordinary project execution (runs without asking) from destructive or system-level
 * operations (needs the user's confirmation). Applied to the command and to shell scripts.
 */
public final class SafetyPolicy {
    private static final String[][] RULES = {
            {"(?:^|[\\s;&|(])(?:sudo|su|tsu)\\s", "تشغيل بصلاحيات الجذر"},
            {"\\brm\\s+(?:-\\S+\\s+)*(?:/|~|\\$HOME|\\$PREFIX|/sdcard|/storage)(?:[\\s/'\"]|$)", "حذف ملفات خارج المشروع"},
            {"\\brm\\s+(?:-\\S+\\s+)*(?:\\.\\.|\\*)(?:[\\s/'\"]|$)", "حذف جماعي بنمط عام"},
            {"\\b(?:mkfs|fdisk|parted)\\b|\\bdd\\s+[^\\n]*\\bof=", "كتابة مباشرة على أقراص"},
            {">\\s*/dev/(?:sd|block|mmc)", "كتابة مباشرة على أقراص"},
            {"\\b(?:reboot|shutdown|poweroff)\\b", "إيقاف أو إعادة تشغيل الجهاز"},
            {":\\(\\)\\s*\\{\\s*:\\s*\\|\\s*:\\s*&\\s*\\}\\s*;\\s*:", "fork bomb"},
            {"\\bch(?:mod|own)\\s+-R\\s+\\S+\\s+(?:/|~|\\$HOME)", "تغيير صلاحيات خارج المشروع"},
            {"\\b(?:curl|wget)\\b[^\\n|]*\\|\\s*(?:ba|z)?sh\\b", "تشغيل سكربت من الإنترنت مباشرة"},
            {"\\b(?:pkg|apt|apt-get)\\s+(?:install|uninstall|remove|purge|upgrade|autoremove)\\b", "تعديل حزم النظام"},
            {"shutil\\.rmtree\\(\\s*['\"](?:/|~)", "حذف مجلدات خارج المشروع"},
            {"os\\.(?:remove|unlink|rmdir)\\(\\s*['\"]/", "حذف ملفات خارج المشروع"},
    };
    private static final Pattern[] PATTERNS = new Pattern[RULES.length];
    static {
        for (int i = 0; i < RULES.length; i++) PATTERNS[i] = Pattern.compile(RULES[i][0], Pattern.MULTILINE);
    }

    private SafetyPolicy() {}

    /** Returns a reason when the text needs the user's approval, or null when it is ordinary. */
    public static String check(String text) {
        if (text == null) return null;
        for (int i = 0; i < PATTERNS.length; i++) {
            Matcher m = PATTERNS[i].matcher(text);
            if (m.find()) return RULES[i][1] + ": " + m.group().trim();
        }
        return null;
    }
}
