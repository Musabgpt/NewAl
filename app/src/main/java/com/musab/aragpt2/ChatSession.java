package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * One assistant reply: optional web grounding, the model's answer streamed as it is written,
 * and tool calls (search, calculator, weather ...) carried out between rounds until the model
 * answers. Thinking ("<think>") is kept apart from the answer.
 */
final class ChatSession {
    interface Model {
        GenerationResult generate(List<ChatMessage> turns, TextListener listener) throws Exception;
    }

    interface Listener {
        /** The reply so far (whole text each time, not a delta). */
        void onUpdate(String thinking, String answer);
        /** A tool is about to run ({@code result} null) or has finished. */
        void onTool(String name, String args, String result);
    }

    static final class Options {
        boolean tools = true;
        /** Search the web before answering (the 🌐 switch), also used for clearly time-sensitive questions. */
        boolean web;
        /** Qwen3.5 / Qwen3-Coder style XML tool calls instead of JSON. */
        boolean xmlToolCalls;
        /** LFM2 / LFM2.5: tools listed their way, Pythonic calls, results in a "tool" turn. */
        boolean lfmTools;
        String date = "";
        /** Extra instructions (memory, the user's name ...). */
        String extraSystem = "";
    }

    static final class Reply {
        String answer = "", thinking = "";
        final List<WebTools.Result> sources = new ArrayList<>();
        final List<String> toolsUsed = new ArrayList<>();
        GenerationResult last;
    }

    /** Tool rounds per answer; agent-trained models (LFM2.5) otherwise keep searching for minutes. */
    static final int MAX_TOOL_ROUNDS = 2;

    static final String PERSONA =
            "You are NewAl, a capable assistant that runs privately on the user's phone. "
            + "Answer in the user's language and dialect. Be accurate and direct; use Markdown (headings, lists, "
            + "tables, fenced code with the language). Do arithmetic with the calculator tool. When information may be "
            + "recent or you are not sure, search the web. When you use web results, cite them as [1], [2]. "
            + "Use only numbers and facts that appear in tool or web results; never invent them. "
            + "If you do not know, say so instead of guessing.";

    private static final Pattern TIME_SENSITIVE = Pattern.compile(
            "اليوم|الان|حاليا|هلق|هلا|هلأ|هالفتره|اخر|أخبار|اخبار|سعر|اسعار|طقس|الطقس|نتيجه|مباراه|دوري|انتخابات|"
            + "20[2-9][0-9]|today|now|current|currently|latest|recent|news|price|prices|weather|score|stock|this week",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private static final Pattern WEATHER = Pattern.compile("طقس|الطقس|حراره|الحراره|مطر|تمطر|درجه الحراره|weather|temperature|forecast|rain",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern NOT_A_PLACE = Pattern.compile(
            "^(?:كيف|شو|ما|هو|هي|الطقس|طقس|حاله|اليوم|بكرا|بكره|غدا|الان|هلق|هلا|بالاسبوع|الاسبوع|هالاسبوع|الجو|جو|الحراره|درجه|حراره|"
            + "في|ب|عن|رح|يكون|بيكون|راح|مطر|تمطر|the|what|what's|whats|how|is|in|at|weather|today|tomorrow|forecast|like|will|it|rain|temperature|for|week|this)$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    /** "كيف الطقس بدمشق بكرا" → "دمشق"; null when the question is not about weather or names no place. */
    static String weatherCity(String question) {
        String q = ArabicText.norm(question).replaceAll("[?!.,:؟]", " ");
        if (!WEATHER.matcher(q).find()) return null;
        StringBuilder city = new StringBuilder();
        for (String w : q.split("\\s+")) {
            String word = w;
            if (NOT_A_PLACE.matcher(word).matches()) { if (city.length() > 0) break; continue; }
            if (city.length() == 0) word = word.replaceFirst("^(?:بال|ب|في|لل|ل)(?=\\S{3,})", "");
            if (NOT_A_PLACE.matcher(word).matches()) continue;
            city.append(city.length() == 0 ? "" : " ").append(word);
        }
        return city.length() == 0 ? null : city.toString();
    }

    /** The question without question words and punctuation: what a search engine matches best. */
    static String searchQuery(String question) {
        String q = question.replaceAll("[?!؟،,.:«»\"]", " ")
                .replaceAll("(?i)(?:^|\\s)(?:كم|ما|ماذا|هل|شو|قديش|قديه|ايش|وش|شنو|ممكن|لو سمحت|please|what|what's|is|are|how much|tell me)(?=\\s|$)", " ");
        return q.replaceAll("\\s+", " ").trim();
    }

    private static final String[][] CURRENCIES = {
            {"USD", "دولار|دولارات|الدولار|\\$|usd|dollars?"}, {"EUR", "يورو|اليورو|€|eur|euros?"},
            {"TRY", "ليره تركيه|الليره التركيه|تركي|تركيه|try|turkish lira"}, {"SYP", "ليره سوريه|الليره السوريه|سوري|syp|syrian pound"},
            {"LBP", "ليره لبنانيه|لبناني|lbp"}, {"SAR", "ريال سعودي|سعودي|ريال|sar|riyal"}, {"AED", "درهم|درهم اماراتي|اماراتي|aed|dirham"},
            {"JOD", "دينار اردني|اردني|jod"}, {"IQD", "دينار عراقي|عراقي|iqd"}, {"KWD", "دينار كويتي|كويتي|kwd"},
            {"QAR", "ريال قطري|قطري|qar"}, {"EGP", "جنيه مصري|مصري|جنيه|egp"}, {"GBP", "استرليني|باوند|جنيه استرليني|gbp|pounds?"},
            {"JPY", "ين|yen|jpy"}, {"CNY", "يوان|yuan|cny"}, {"RUB", "روبل|ruble|rub"}, {"CAD", "دولار كندي|كندي|cad"},
            {"SEK", "كرون سويدي|سويدي|sek"}, {"CHF", "فرنك سويسري|فرنك|chf"}};
    private static final Pattern CONVERT = Pattern.compile("حول|حولي|كم|يساوي|سعر|بكم|صرف|قديش|convert|exchange|how much|in|to|=",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    /** "حول 250 دولار لليرة التركية" → {"250", "USD", "TRY"}; null when it is not a conversion question. */
    static String[] currencyQuery(String question) {
        String q = " " + ArabicText.norm(question).replaceAll("[?!.,؟]", " ") + " ";
        if (!CONVERT.matcher(q).find()) return null;
        java.util.TreeMap<Integer, String> found = new java.util.TreeMap<>();
        for (String[] c : CURRENCIES) {
            // Longer names first inside each alternation, so "ليره تركيه" wins over a bare "ليره".
            Matcher m = Pattern.compile("(?<![\\p{L}])(?:ب|بال|لل|ل|ال|و)?(?:" + c[1] + ")(?![\\p{L}])", Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE).matcher(q);
            if (m.find() && !found.containsValue(c[0])) found.put(m.start(), c[0]);
        }
        if (!found.containsValue("TRY") && !found.containsValue("SYP") && !found.containsValue("LBP")
                && Pattern.compile("(?<![\\p{L}])(?:ب|بال|لل|ل|ال)?ليره(?![\\p{L}])").matcher(q).find()) {
            Matcher m = Pattern.compile("ليره").matcher(q);
            if (m.find()) found.put(m.start(), "SYP");
        }
        if (found.size() < 2) return null;
        java.util.Iterator<String> it = found.values().iterator();
        String from = it.next(), to = it.next();
        Matcher n = Pattern.compile("(\\d+(?:\\.\\d+)?)").matcher(q);
        return new String[]{n.find() ? n.group(1) : "1", from, to};
    }

    static boolean timeSensitive(String question) {
        return TIME_SENSITIVE.matcher(ArabicText.norm(question)).find();
    }

    private final Model model;
    private final ChatTools tools;
    private volatile boolean cancelled;

    ChatSession(Model model, ChatTools tools) { this.model = model; this.tools = tools; }

    void cancel() { cancelled = true; }

    /** {@code history} are the conversation turns, the last one being the user's new message. */
    Reply run(List<ChatMessage> history, Options o, Listener listener) throws Exception {
        Reply reply = new Reply();
        boolean grounded = false;
        List<ChatMessage> turns = new ArrayList<>();
        StringBuilder system = new StringBuilder(PERSONA);
        if (!o.date.isEmpty()) system.append(" Today is ").append(o.date).append('.');
        if (!o.extraSystem.isEmpty()) system.append("\n\n").append(o.extraSystem);
        if (o.tools) system.append("\n\n").append(o.lfmTools ? tools.lfmBlock() : tools.systemBlock(o.xmlToolCalls));
        turns.add(new ChatMessage(0, ChatMessage.ROLE_SYSTEM, system.toString(), 0));
        for (ChatMessage m : history) if (m.role == ChatMessage.ROLE_USER || m.role == ChatMessage.ROLE_ASSISTANT) turns.add(m);

        // Grounding before the model writes, done by code so it does not depend on the model
        // choosing a tool: exact results of any calculation, the weather, or web results.
        ChatMessage last = turns.get(turns.size() - 1);
        if (last.role == ChatMessage.ROLE_USER) {
            StringBuilder facts = new StringBuilder();
            for (String solved : Calculator.findAndSolve(last.text)) {
                facts.append("calculator: ").append(solved).append('\n');
                reply.toolsUsed.add("calculator");
            }
            String[] money = tools.online ? currencyQuery(last.text) : null;
            if (money != null) {
                ChatTools.Call c = new ChatTools.Call("currency", new org.json.JSONObject()
                        .put("amount", Double.parseDouble(money[0])).put("from", money[1]).put("to", money[2]));
                listener.onTool("currency", c.args.toString(), null);
                String conv = tools.run(c, reply.sources);
                listener.onTool("currency", c.args.toString(), conv);
                if (!conv.startsWith("error") && !conv.startsWith("unknown")) {
                    facts.append("currency tool: ").append(conv).append('\n');
                    reply.toolsUsed.add("currency");
                }
            }
            String city = tools.online ? weatherCity(last.text) : null;
            if (city != null) {
                listener.onTool("weather", city, null);
                ChatTools.Call c = new ChatTools.Call("weather", new org.json.JSONObject().put("city", city));
                String w = tools.run(c, reply.sources);
                if (w.startsWith("error")) w = tools.run(c, reply.sources);   // one retry on a slow network
                listener.onTool("weather", city, w);
                if (!w.startsWith("error") && !w.startsWith("city not found")) {
                    facts.append("weather tool:\n").append(w).append('\n');
                    reply.toolsUsed.add("weather");
                } else city = null;
            }
            if (city == null && money == null && tools.online && (o.web || timeSensitive(last.text))) {
                String query = searchQuery(last.text);
                listener.onTool("web_search", query, null);
                String found;
                try {
                    found = tools.searchText(query, reply.sources);
                } catch (Exception e) {
                    found = "";
                }
                listener.onTool("web_search", query, found.isEmpty() ? "no results" : reply.sources.size() + " results");
                if (!found.isEmpty() && !found.startsWith("no results")) {
                    reply.toolsUsed.add("web_search");
                    facts.append("<web_results>\n").append(found).append("\n</web_results>\n");
                }
            }
            grounded = facts.length() > 0;
            String language = WebTools.arabic(last.text) ? " Reply in Arabic." : "";
            if (grounded) {
                turns.set(turns.size() - 1, new ChatMessage(last.id, ChatMessage.ROLE_USER, last.text + "\n\n" + facts
                        + "Answer the question above now using this information (cite web results as [n])." + language, last.timeMs));
            } else if (!language.isEmpty()) {
                turns.set(turns.size() - 1, new ChatMessage(last.id, ChatMessage.ROLE_USER, last.text + "\n\n(" + language.trim() + ")", last.timeMs));
            }
        }
        // With the facts already in the question one tool round is plenty.
        int maxRounds = grounded ? 1 : MAX_TOOL_ROUNDS;
        java.util.Set<String> seen = new java.util.HashSet<>();
        boolean finalRound = false;

        for (int round = 0; ; round++) {
            if (cancelled) break;
            StringBuilder raw = new StringBuilder();
            GenerationResult r = model.generate(turns, delta -> {
                raw.append(delta);
                String[] parts = split(raw.toString());
                listener.onUpdate(parts[0], parts[1]);
            });
            reply.last = r;
            String text = r.text;
            String[] parts = split(text);
            if (!parts[0].isEmpty()) reply.thinking = parts[0];
            List<ChatTools.Call> calls = o.tools && !finalRound ? ChatTools.parseCalls(text) : new ArrayList<>();
            // A call already made (same tool, same arguments) is not run again: the model has that result.
            List<ChatTools.Call> fresh = new ArrayList<>();
            for (ChatTools.Call c : calls) if (seen.add(c.toString())) fresh.add(c);
            if (calls.isEmpty() || cancelled) {
                reply.answer = parts[1].trim();
                listener.onUpdate(reply.thinking, reply.answer);
                break;
            }
            if (fresh.isEmpty() || round >= maxRounds) {
                // Enough tools: one last pass that must answer from what it has.
                turns.add(new ChatMessage(0, ChatMessage.ROLE_ASSISTANT, stripThinking(text).trim(), 0));
                turns.add(new ChatMessage(0, ChatMessage.ROLE_USER, "Answer my question now with the information you already have. Do not call any tool."
                        + (WebTools.arabic(last.text) ? " Reply in Arabic." : ""), 0));
                finalRound = true;
                continue;
            }
            StringBuilder responses = new StringBuilder();
            for (ChatTools.Call c : fresh) {
                listener.onTool(c.name, c.args.toString(), null);
                String result = tools.run(c, reply.sources);
                if (result.length() > 6000) result = result.substring(0, 6000) + "…";
                reply.toolsUsed.add(c.name);
                listener.onTool(c.name, c.args.toString(), result);
                if (o.lfmTools) responses.append(responses.length() == 0 ? "" : "\n").append(result);
                else responses.append(responses.length() == 0 ? "" : "\n").append("<tool_response>\n").append(result).append("\n</tool_response>");
            }
            // The call stays in the conversation as the assistant's turn; results come back as a user turn,
            // which is how Qwen's templates render tool messages.
            turns.add(new ChatMessage(0, ChatMessage.ROLE_ASSISTANT, stripThinking(text).trim(), 0));
            turns.add(new ChatMessage(0, o.lfmTools ? ChatMessage.ROLE_TOOL : ChatMessage.ROLE_USER, responses.toString(), 0));
        }
        return reply;
    }

    private static final String[] HELD = {"<tool_call>", "<think>", "</think>"};

    /**
     * {thinking, answer} from raw model output: the think block apart, tool calls removed, and a
     * tag that is still being written held back so it never flashes on screen.
     */
    static String[] split(String raw) {
        String thinking = "", rest = raw;
        int open = rest.indexOf("<think>"), close = rest.indexOf("</think>");
        if (close >= 0) {
            thinking = rest.substring(open >= 0 && open < close ? open + 7 : 0, close);
            rest = rest.substring(close + 8);
        } else if (open >= 0 && rest.substring(0, open).trim().isEmpty()) {
            thinking = rest.substring(open + 7);
            rest = "";
        }
        rest = rest.replaceAll("(?s)<tool_call>.*?(?:</tool_call>|$)", "");
        rest = ChatTools.PY_CALLS.matcher(rest).replaceAll("");
        rest = rest.replaceAll("(?s)\\[(?:" + ChatTools.TOOL_NAMES + ")\\([^\\]]*$", "");
        for (String tag : HELD) {
            for (int n = tag.length() - 1; n > 0; n--) {
                if (rest.endsWith(tag.substring(0, n))) { rest = rest.substring(0, rest.length() - n); break; }
            }
        }
        return new String[]{thinking.trim(), rest.replaceFirst("^\\s+", "")};
    }

    static String stripThinking(String raw) {
        return raw.replaceAll("(?s)^.*?</think>", "").replaceAll("(?s)<think>.*$", "");
    }
}
