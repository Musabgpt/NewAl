package com.musab.aragpt2;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The chat model's tools, offered in Qwen's own function-calling format (JSON calls for Qwen2.5 /
 * Qwen3, XML calls for Qwen3.5 / Qwen3-Coder, chosen from the model's template).
 */
final class ChatTools {
    static final class Call {
        final String name;
        final JSONObject args;
        Call(String name, JSONObject args) { this.name = name; this.args = args; }
        @Override public String toString() { return name + args; }
    }

    private final WebTools web;
    /** Internet tools on or off (off: calculator and time only). */
    volatile boolean online = true;
    /** Extra tools implemented elsewhere (device control). */
    LocalTools local;

    ChatTools(WebTools web) { this.web = web; }

    JSONArray definitions() {
        JSONArray a = new JSONArray();
        try {
            if (online) {
                a.put(fn("web_search", "Search the internet for current or factual information (news, prices, people, places, recent events). Returns results with snippets and the text of the best page.",
                        "query", "string", "search words, in the language most likely to find it"));
                a.put(fn("read_page", "Read the text of a web page.", "url", "string", "the page address"));
                a.put(fn("wikipedia", "The introduction of a Wikipedia article.", "topic", "string", "article subject"));
                a.put(fn("weather", "Current weather and a 3-day forecast for a city.", "city", "string", "city name"));
                a.put(new JSONObject().put("type", "function").put("function", new JSONObject()
                        .put("name", "currency").put("description", "Convert money with today's exchange rate.")
                        .put("parameters", new JSONObject().put("type", "object")
                                .put("properties", new JSONObject()
                                        .put("amount", new JSONObject().put("type", "number"))
                                        .put("from", new JSONObject().put("type", "string").put("description", "ISO code, e.g. USD"))
                                        .put("to", new JSONObject().put("type", "string").put("description", "ISO code, e.g. SYP")))
                                .put("required", new JSONArray().put("amount").put("from").put("to")))));
            }
            a.put(fn("calculator", "Exact arithmetic. Use it for any calculation instead of computing in your head.",
                    "expression", "string", "e.g. (1250*0.15)+99 or sqrt(2)^3"));
            a.put(new JSONObject().put("type", "function").put("function", new JSONObject().put("name", "current_time")
                    .put("description", "The current date, time and weekday on the phone.")
                    .put("parameters", new JSONObject().put("type", "object").put("properties", new JSONObject()))));
        } catch (org.json.JSONException ignored) {
        }
        return a;
    }

    private static JSONObject fn(String name, String desc, String param, String type, String paramDesc) throws org.json.JSONException {
        return new JSONObject().put("type", "function").put("function", new JSONObject().put("name", name).put("description", desc)
                .put("parameters", new JSONObject().put("type", "object")
                        .put("properties", new JSONObject().put(param, new JSONObject().put("type", type).put("description", paramDesc)))
                        .put("required", new JSONArray().put(param))));
    }

    /** The system-prompt block that describes the tools, worded exactly as the model's template does. */
    String systemBlock(boolean xmlCalls) {
        StringBuilder b = new StringBuilder("# Tools\n\n");
        JSONArray defs = definitions();
        if (xmlCalls) {
            b.append("You have access to the following functions:\n\n<tools>");
            for (int i = 0; i < defs.length(); i++) b.append('\n').append(defs.optJSONObject(i).toString());
            b.append("\n</tools>\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n"
                    + "<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n</function>\n</tool_call>\n\n"
                    + "<IMPORTANT>\nReminder:\n- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags\n"
                    + "- Required parameters MUST be specified\n"
                    + "- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>");
        } else {
            b.append("You may call one or more functions to assist with the user query.\n\n"
                    + "You are provided with function signatures within <tools></tools> XML tags:\n<tools>");
            for (int i = 0; i < defs.length(); i++) b.append('\n').append(defs.optJSONObject(i).toString());
            b.append("\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
                    + "<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>");
        }
        return b.toString();
    }

    private static final Pattern CALL = Pattern.compile("<tool_call>(.*?)(?:</tool_call>|$)", Pattern.DOTALL);
    private static final Pattern XML_FN = Pattern.compile("<function=([^>\\s]+)>(.*?)(?:</function>|$)", Pattern.DOTALL);
    private static final Pattern XML_PARAM = Pattern.compile("<parameter=([^>\\s]+)>\\s*(.*?)\\s*(?:</parameter>|(?=<parameter=)|$)", Pattern.DOTALL);

    /** Tool calls in a reply, in either format. */
    static List<Call> parseCalls(String text) {
        List<Call> out = new ArrayList<>();
        Matcher m = CALL.matcher(text);
        while (m.find()) {
            String body = m.group(1).trim();
            try {
                Matcher x = XML_FN.matcher(body);
                if (x.find()) {
                    JSONObject args = new JSONObject();
                    Matcher p = XML_PARAM.matcher(x.group(2));
                    while (p.find()) args.put(p.group(1), p.group(2));
                    out.add(new Call(x.group(1).trim(), args));
                    continue;
                }
                int s = body.indexOf('{'), e = body.lastIndexOf('}');
                if (s < 0 || e <= s) continue;
                JSONObject j = new JSONObject(body.substring(s, e + 1));
                Object args = j.opt("arguments");
                JSONObject a = args instanceof JSONObject ? (JSONObject) args
                        : args instanceof String ? new JSONObject((String) args) : new JSONObject();
                out.add(new Call(j.optString("name"), a));
            } catch (Exception ignored) {
                // A malformed call is ignored; the text around it is shown as the answer.
            }
        }
        return out;
    }

    /** Runs a tool; errors come back as text so the model can react to them. */
    String run(Call c, List<WebTools.Result> sources) {
        try {
            switch (c.name) {
                case "calculator": {
                    String expr = c.args.optString("expression");
                    return expr + " = " + Calculator.format(Calculator.eval(Calculator.percentOf(expr)));
                }
                case "current_time":
                    return ZonedDateTime.now().format(DateTimeFormatter.ofPattern("EEEE yyyy-MM-dd HH:mm z", Locale.ENGLISH));
                case "web_search": return searchText(c.args.optString("query"), sources);
                case "read_page": return web.read(c.args.optString("url"), "", 3000);
                case "wikipedia": {
                    String t = c.args.optString("topic");
                    return web.wikipedia(t, WebTools.arabic(t) ? "ar" : "en");
                }
                case "weather": return web.weather(c.args.optString("city"));
                case "currency": return web.currency(c.args.optDouble("amount", 1), c.args.optString("from"), c.args.optString("to"));
                default:
                    if (local != null) {
                        JSONObject r = local.call(c.name, c.args);
                        if (r != null) return r.optString("output");
                    }
                    return "unknown tool " + c.name;
            }
        } catch (Exception e) {
            return "error: " + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
        }
    }

    /** Search results, numbered for citation, plus the relevant text of the first readable page. */
    String searchText(String query, List<WebTools.Result> sources) throws Exception {
        List<WebTools.Result> results = web.search(query, 5);
        if (results.isEmpty()) return "no results for " + query;
        StringBuilder b = new StringBuilder();
        int first = sources.size() + 1;
        for (int i = 0; i < results.size(); i++) {
            WebTools.Result r = results.get(i);
            sources.add(r);
            b.append('[').append(first + i).append("] ").append(r.title).append(" — ").append(r.url).append('\n');
            if (!r.snippet.isEmpty()) b.append(r.snippet).append('\n');
        }
        for (int i = 0; i < Math.min(2, results.size()); i++) {
            try {
                String page = web.read(results.get(i).url, query, 1500);
                if (page.length() > 200) {
                    b.append("\nText of [").append(first + i).append("]:\n").append(page).append('\n');
                    break;
                }
            } catch (Exception ignored) {
                // Some sites refuse robots; the snippets are still there.
            }
        }
        return b.toString().trim();
    }
}
