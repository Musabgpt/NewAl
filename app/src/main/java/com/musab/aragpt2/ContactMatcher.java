package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Finds the contact a spoken or typed name means ("لأحمد", "بماما", "sara") and formats numbers. */
final class ContactMatcher {
    private ContactMatcher() {}

    static final class Contact {
        final String name, number;
        Contact(String name, String number) { this.name = name; this.number = number; }
        @Override public String toString() { return name + " (" + number + ")"; }
    }

    /** A recipient found at the start of a request, and the message text after it. */
    static final class Split {
        final List<Contact> contacts;
        final String text;
        Split(List<Contact> contacts, String text) { this.contacts = contacts; this.text = text; }
    }

    /** The name without an attached Arabic preposition: لأحمد → أحمد, بسارة → سارة, للمدير → المدير. */
    static List<String> variants(String who) {
        String w = ArabicText.norm(who).trim();
        List<String> v = new ArrayList<>();
        v.add(w);
        for (String prefix : new String[]{"لل", "بال", "ل", "ب", "ع"}) {
            if (w.startsWith(prefix) && w.length() > prefix.length() + 1 && w.charAt(prefix.length()) != ' ') {
                String rest = w.substring(prefix.length());
                v.add(rest);
                if (prefix.equals("لل") || prefix.equals("بال")) v.add("ال" + rest);
            }
        }
        return v;
    }

    /** 0-100: how well {@code query} names this contact. */
    static int score(String query, String name) {
        String q = ArabicText.key(query), n = ArabicText.key(name);
        if (q.length() < 2 || n.isEmpty()) return 0;
        if (q.equals(n)) return 100;
        String[] words = ArabicText.norm(name).split(" ");
        if (ArabicText.key(words[0]).equals(q)) return 85;
        for (String w : words) if (ArabicText.key(w).equals(q)) return 70;
        if (n.startsWith(q) && q.length() >= 3) return 60;
        if (q.startsWith(n) && n.length() >= 3) return 50;
        return 0;
    }

    /** The best matching contacts (several when equally good, e.g. two Ahmads); empty when none. */
    static List<Contact> find(String who, List<Contact> all) {
        int best = 0;
        List<Contact> found = new ArrayList<>();
        for (String q : variants(who)) {
            for (Contact c : all) {
                int s = score(q, c.name);
                if (s <= 0 || s < best) continue;
                if (s > best) { best = s; found.clear(); }
                boolean dup = false;
                for (Contact f : found) dup |= f.name.equals(c.name) && digits(f.number).equals(digits(c.number));
                if (!dup) found.add(c);
            }
        }
        return best >= 50 ? found : new ArrayList<>();
    }

    /** "أحمد علي مرحبا كيفك" → contact أحمد علي + text "مرحبا كيفك" (longest name that matches). */
    static Split split(String rest, List<Contact> all) {
        String[] words = ArabicText.norm(rest).split(" ");
        for (int n = Math.min(4, words.length); n >= 1; n--) {
            String who = String.join(" ", java.util.Arrays.copyOfRange(words, 0, n));
            List<Contact> found = find(who, all);
            if (!found.isEmpty() && (found.get(0) != null)) {
                boolean strong = false;
                for (String q : variants(who)) for (Contact c : found) strong |= score(q, c.name) >= 70;
                if (!strong && n > 1) continue;
                String text = String.join(" ", java.util.Arrays.copyOfRange(words, n, words.length)).trim();
                return new Split(found, text);
            }
        }
        return new Split(new ArrayList<>(), rest);
    }

    static boolean isNumber(String s) {
        String t = ArabicText.norm(s);
        return t.matches("[+0-9][0-9 ()\\-]{2,20}") && digits(t).length() >= 3;
    }

    static String digits(String s) { return ArabicText.norm(s).replaceAll("[^0-9]", ""); }

    /** International digits for wa.me links: +963 99…, 00963 99…, 099… (local, with the SIM's country code). */
    static String international(String number, String countryIso) {
        String t = ArabicText.norm(number).replaceAll("[\\s()\\-]", "");
        if (t.startsWith("+")) return digits(t);
        if (t.startsWith("00")) return digits(t).substring(2);
        String d = digits(t);
        String cc = CALLING_CODES.get(countryIso == null ? "" : countryIso.toLowerCase(Locale.ROOT));
        if (cc == null) return d;
        if (d.startsWith("0")) return cc + d.substring(1);
        if (d.startsWith(cc) && d.length() > 10) return d;
        return d.length() <= 10 ? cc + d : d;
    }

    private static final Map<String, String> CALLING_CODES = new java.util.HashMap<>();
    static {
        String[] pairs = {"sy", "963", "sa", "966", "ae", "971", "jo", "962", "lb", "961", "iq", "964", "eg", "20",
                "tr", "90", "ps", "970", "kw", "965", "qa", "974", "om", "968", "bh", "973", "ye", "967", "ly", "218",
                "tn", "216", "dz", "213", "ma", "212", "sd", "249", "de", "49", "at", "43", "ch", "41", "nl", "31",
                "be", "32", "fr", "33", "se", "46", "no", "47", "dk", "45", "gb", "44", "it", "39", "es", "34",
                "gr", "30", "us", "1", "ca", "1", "ru", "7", "ir", "98", "pk", "92", "in", "91", "au", "61",
                "br", "55", "cy", "357", "my", "60", "id", "62"};
        for (int i = 0; i < pairs.length; i += 2) CALLING_CODES.put(pairs[i], pairs[i + 1]);
    }
}
