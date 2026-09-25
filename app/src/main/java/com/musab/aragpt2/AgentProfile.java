package com.musab.aragpt2;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Specialised agents that share the same local model and execution loop but differ in
 * instructions and in what counts as done.
 */
public enum AgentProfile {
    AUTO("auto", "🤖", "تلقائي", "", false, false),
    CODER("code", "💻", "مبرمج",
            "Role: coder. Build exactly what is asked, run it, and fix it until it works.", false, false),
    ARCHITECT("plan", "🏗", "مهندس",
            "Role: architect-coder. Follow the plan given in the task: create every planned file, "
            + "keep modules small and focused.", true, false),
    TESTER("test", "🧪", "مختبر",
            "Role: tester. Write unit tests (Python: unittest in tests/test_*.py) that check the real "
            + "behaviour of the existing project, then run them. Change project code only when a test "
            + "exposes a real bug.", false, false),
    FIXER("fix", "🔧", "مصلح",
            "Role: fixer. The user gives code and/or an error. Recreate their code as files exactly, "
            + "then make the smallest change that fixes the error.", false, false),
    EXPLAINER("explain", "📖", "شارح",
            "Role: explainer. Do not write files. Read the project with tools if needed and answer "
            + "with SAY: lines only, clear and short.", false, true),
    AUTOMATOR("phone", "📱", "مساعد الهاتف",
            "Role: personal phone assistant. Do what the user asks by calling the device tools (open_app, "
            + "set_alarm, open_settings, flashlight, volume, media, screen tools ...), one TOOL line per action, "
            + "then confirm briefly with SAY: in the user's language. Only write a program when the user asks "
            + "for a repeated or scheduled job.", false, false);

    public final String id, icon, title, instructions;
    /** Plan the files first in a separate pass, then write them. */
    public final boolean planFirst;
    /** Answers only; nothing is written or executed. */
    public final boolean answerOnly;

    AgentProfile(String id, String icon, String title, String instructions, boolean planFirst, boolean answerOnly) {
        this.id = id; this.icon = icon; this.title = title; this.instructions = instructions;
        this.planFirst = planFirst; this.answerOnly = answerOnly;
    }

    private static final Pattern QUESTION = Pattern.compile(
            "^(what|why|how|explain|describe|where|which|ما |ماذا|لماذا|كيف|اشرح|وضح|شو |ليش|وين)|\\?$|؟$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern ERROR = Pattern.compile(
            "traceback|error:|exception|خطأ|ايرور|error\\b|لا يعمل|ما بيشتغل|مش شغال", Pattern.CASE_INSENSITIVE);
    private static final Pattern TEST = Pattern.compile("\\btests?\\b|unittest|pytest|اختبار|اختبر", Pattern.CASE_INSENSITIVE);
    private static final Pattern PHONE = Pattern.compile(
            "battery|notification|notify|clipboard|vibrat|flashlight|torch|location|wifi|speak|bluetooth|volume|"
            + "\\bopen\\b|launch|alarm|timer|call |dial|navigate|screen|brightness|pause|play music|"
            + "بطاري|إشعار|اشعار|حافظة|اهتزاز|كشاف|موقع|واي فاي|انطق|نبهني|ذكرني|افتح|شغل تطبيق|اتصل|منبه|"
            + "مؤقت|الصوت|بلوتوث|الشاشة|السطوع|خريطة|وقف الموسيقى|شغل الموسيقى|ارجع|الرئيسية",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private static final Pattern CODE = Pattern.compile(
            "program|script|code|function|app that|برنامج|برمج|كود|سكربت|دالة|اكتب لي|اكتبلي|اصنع",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    /** Picks an agent for AUTO from the request and whether a project already exists. */
    public static AgentProfile choose(String request, boolean hasProject) {
        String r = request.trim().toLowerCase(Locale.ROOT);
        if (ERROR.matcher(r).find() && (r.contains("\n") || r.length() > 120)) return FIXER;
        // "open YouTube" is a device action; "write a program that opens files" is a coding task.
        if (PHONE.matcher(r).find() && !CODE.matcher(r).find()) return AUTOMATOR;
        if (TEST.matcher(r).find() && hasProject) return TESTER;
        if (QUESTION.matcher(r).find() && hasProject) return EXPLAINER;
        if (r.split("\\s+").length > 30) return ARCHITECT;
        return CODER;
    }

    public static AgentProfile byId(String id) {
        for (AgentProfile a : values()) if (a.id.equalsIgnoreCase(id) || a.name().equalsIgnoreCase(id)) return a;
        return null;
    }
}
