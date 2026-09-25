package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * Skills: focused know-how for common kinds of programs, added to the prompt when the user
 * picks one (/id) or when the request clearly matches it. Small models follow concrete
 * recipes far better than general instructions. Users add their own as Markdown files in
 * Termux (~/newal/skills/<id>.md, first line "# Title | keyword, keyword").
 */
public final class Skills {
    public static final class Skill {
        public final String id, icon, title, instructions;
        final String[] keywords;

        public Skill(String id, String icon, String title, String[] keywords, String instructions) {
            this.id = id; this.icon = icon; this.title = title; this.keywords = keywords; this.instructions = instructions;
        }
    }

    /** A request split into the chosen agent/skill (from /commands) and the remaining text. */
    public static final class Parsed {
        public final AgentProfile agent;
        public final Skill skill;
        public final String text;
        Parsed(AgentProfile agent, Skill skill, String text) { this.agent = agent; this.skill = skill; this.text = text; }
    }

    private static final List<Skill> BUILTIN = new ArrayList<>();
    private static final List<Skill> USER = Collections.synchronizedList(new ArrayList<>());

    private static void add(String id, String icon, String title, String keywords, String instructions) {
        BUILTIN.add(new Skill(id, icon, title, keywords.split(",\\s*"), instructions));
    }

    static {
        add("cli", "⌨", "برنامج سطر أوامر", "script, cli, command line, سكربت, برنامج بسيط",
                "Skill: command-line program. One main.py with a main() function and if __name__ == '__main__'. "
                + "Print clear results. Use argparse only if the task mentions options; give defaults so it runs without arguments.");
        add("interactive", "🎮", "برنامج تفاعلي أو لعبة", "calculator, game, menu, quiz, آلة حاسبة, حاسبة, لعبة, قائمة, مسابقة",
                "Skill: interactive program. Read input with input(); handle invalid input without crashing; "
                + "offer an exit choice. Always add a STDIN block whose lines exercise the main paths and end with the exit choice.");
        add("scraper", "🕸", "جلب بيانات من الويب", "scrape, website, html, requests, url, موقع, صفحة, سحب بيانات",
                "Skill: web scraping. Use requests and beautifulsoup4 (list both in requirements.txt), a timeout=15 on "
                + "every request, a User-Agent header, and handle network errors with a clear message instead of a traceback.");
        add("api", "🌐", "خادم API", "api, flask, server, endpoint, rest, خادم, سيرفر",
                "Skill: web API. Use Flask (in requirements.txt). Put the app in app.py with a create_app() function. "
                + "Do not start the server in the test run: add tests/test_app.py that uses app.test_client() to call "
                + "every endpoint, and RUN: python -m unittest discover -s tests -q");
        add("data", "📊", "تحليل بيانات CSV", "csv, data, statistics, average, report, بيانات, إحصاء, متوسط, تقرير",
                "Skill: data processing. Use the csv and statistics modules (no pandas). If no data file is given, "
                + "create a small sample data.csv as a FILE. Print a readable summary table.");
        add("sqlite", "🗄", "قاعدة بيانات", "database, sqlite, sql, store, قاعدة بيانات, تخزين",
                "Skill: database. Use sqlite3 with a file app.db, create tables if they do not exist, use parameterised "
                + "queries, and demonstrate insert/list/update/delete in main().");
        add("bash", "🐚", "سكربت Bash", "bash, shell, sh, termux script, سكربت شل",
                "Skill: bash. Write main.sh starting with #!/data/data/com.termux/files/usr/bin/bash and set -euo pipefail. "
                + "Quote every variable. Never touch files outside the project folder.");
        add("files", "🗂", "تنظيم الملفات", "files, rename, organize, folder, ملفات, ترتيب, مجلد, إعادة تسمية",
                "Skill: file tools. Work only inside a folder given as an argument (default ./sample). Create sample files "
                + "first so the run demonstrates the behaviour. Print every change; add a --dry-run option.");
        add("tests", "🧪", "كتابة اختبارات", "unit test, tests, testing, اختبارات",
                "Skill: tests. unittest in tests/test_*.py, one test per behaviour, include edge cases "
                + "(empty input, zero, negative, wrong type).");
        add("phone", "📱", "أتمتة الهاتف", "battery, notification, clipboard, torch, vibrate, بطارية, إشعار, حافظة",
                "Skill: phone automation. Use the phone tools when available. In scripts call termux-api commands with "
                + "subprocess.run([...], capture_output=True, text=True, timeout=20) and parse their JSON output.");
        add("algo", "🧮", "خوارزميات", "algorithm, sort, search, prime, fibonacci, خوارزمية, ترتيب, أعداد أولية",
                "Skill: algorithms. Write a clear function, print results for several example inputs, and add "
                + "assert checks for known answers at the end of main().");
        add("telegram", "✈", "بوت تيليجرام", "telegram, bot, تيليجرام, بوت",
                "Skill: Telegram bot. Use python-telegram-bot (in requirements.txt), read the token from the "
                + "TELEGRAM_TOKEN environment variable, and exit with a clear message when it is missing instead of crashing.");
    }

    private Skills() {}

    public static List<Skill> all() {
        List<Skill> l = new ArrayList<>(BUILTIN);
        l.addAll(USER);
        return l;
    }

    /** Replaces the user-defined skills (read from Termux). */
    public static void setUserSkills(List<Skill> skills) {
        synchronized (USER) {
            USER.clear();
            USER.addAll(skills);
        }
    }

    /** Parses "# Title | kw1, kw2" + body into a skill named after the file. */
    public static Skill fromMarkdown(String id, String markdown) {
        String[] lines = markdown.split("\n", 2);
        String head = lines[0].replaceFirst("^#+\\s*", "");
        String title = head, keywords = id;
        int bar = head.indexOf('|');
        if (bar >= 0) { title = head.substring(0, bar).trim(); keywords = head.substring(bar + 1).trim(); }
        String body = lines.length > 1 ? lines[1].trim() : "";
        return new Skill(id, "⭐", title, keywords.split(",\\s*"), "Skill: " + title + ". " + body);
    }

    public static Skill byId(String id) {
        for (Skill s : all()) if (s.id.equalsIgnoreCase(id)) return s;
        return null;
    }

    /** Best matching skill by keywords, or null when nothing clearly matches. */
    public static Skill match(String request) {
        String r = " " + request.toLowerCase(Locale.ROOT) + " ";
        Skill best = null;
        int bestScore = 0;
        for (Skill s : all()) {
            int score = 0;
            for (String k : s.keywords) {
                String kw = k.trim().toLowerCase(Locale.ROOT);
                if (kw.length() > 1 && r.contains(kw)) score += kw.length() > 4 ? 2 : 1;
            }
            if (score > bestScore) { best = s; bestScore = score; }
        }
        return bestScore >= 2 ? best : null;
    }

    /** Handles leading "/agent" and "/skill" commands, in any order. */
    public static Parsed parse(String input) {
        AgentProfile agent = null;
        Skill skill = null;
        String text = input.trim();
        while (text.startsWith("/")) {
            int sp = text.indexOf(' ');
            String cmd = (sp < 0 ? text.substring(1) : text.substring(1, sp)).trim();
            AgentProfile a = AgentProfile.byId(cmd);
            Skill s = a == null ? byId(cmd) : null;
            if (a == null && s == null) break;
            if (a != null) agent = a; else skill = s;
            text = sp < 0 ? "" : text.substring(sp + 1).trim();
        }
        return new Parsed(agent, skill, text);
    }
}
