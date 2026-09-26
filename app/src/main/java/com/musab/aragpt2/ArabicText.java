package com.musab.aragpt2;

import java.util.Locale;

/** Normalisation for matching spoken / typed Arabic (and English) against names and commands. */
final class ArabicText {
    private ArabicText() {}

    /** Lower case, no diacritics or tatweel, unified alef / ya / ta marbuta, Western digits, single spaces. */
    static String norm(String s) {
        if (s == null) return "";
        StringBuilder b = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if ((c >= 'ً' && c <= 'ْ') || c == 'ٰ' || c == 'ـ') continue;   // harakat, tatweel
            switch (c) {
                case 'أ': case 'إ': case 'آ': case 'ٱ': c = 'ا'; break;
                case 'ى': case 'ئ': c = 'ي'; break;
                case 'ة': c = 'ه'; break;
                case 'ؤ': c = 'و'; break;
                case '،': c = ','; break;
                case '؟': c = '?'; break;
                default:
                    if (c >= '٠' && c <= '٩') c = (char) ('0' + (c - '٠'));
                    else if (c >= '۰' && c <= '۹') c = (char) ('0' + (c - '۰'));
            }
            b.append(c);
        }
        return b.toString().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").trim();
    }

    /** Same as {@link #norm} without spaces and punctuation, for fuzzy name comparison. */
    static String key(String s) {
        return norm(s).replaceAll("[\\s\\p{Punct}_]", "");
    }

    private static final String[][] NUMBER_WORDS = {
            {"1", "واحد", "وحده", "one"}, {"2", "اثنين", "اتنين", "تنين", "ثنتين", "two"},
            {"3", "ثلاث", "ثلاثه", "تلات", "تلاته", "three"}, {"4", "اربع", "اربعه", "four"},
            {"5", "خمس", "خمسه", "five"}, {"6", "ست", "سته", "six"}, {"7", "سبع", "سبعه", "seven"},
            {"8", "ثمان", "ثمانيه", "تمان", "تمانيه", "eight"}, {"9", "تسع", "تسعه", "nine"},
            {"10", "عشر", "عشره", "ten"}, {"11", "احدعش", "حدعش", "eleven"}, {"12", "اثنعش", "طنعش", "twelve"},
            {"15", "ربع", "quarter"}, {"20", "عشرين", "twenty"}, {"30", "نص", "نصف", "ثلاثين", "تلاتين", "half", "thirty"},
            {"45", "خمس واربعين"},
    };

    /** Number from digits or a common Arabic / English number word, or -1. */
    static int number(String word) {
        String w = norm(word);
        if (w.matches("\\d{1,4}")) return Integer.parseInt(w);
        for (String[] row : NUMBER_WORDS) for (int i = 1; i < row.length; i++) if (row[i].equals(w)) return Integer.parseInt(row[0]);
        return -1;
    }
}
