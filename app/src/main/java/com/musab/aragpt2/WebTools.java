package com.musab.aragpt2;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Free internet tools that need no account or key: web search (Bing RSS, then DuckDuckGo, then
 * Wikipedia), reading a page as plain text, Wikipedia, weather (Open-Meteo) and exchange rates
 * (open.er-api.com).
 */
final class WebTools {
    interface Http { String get(String url) throws Exception; }

    static final class Result {
        final String title, url, snippet;
        Result(String title, String url, String snippet) { this.title = title; this.url = url; this.snippet = snippet; }
    }

    private static final String UA = "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36";
    private final Http http;

    WebTools(Http http) { this.http = http; }

    /** Plain HTTP GET with a phone browser user agent, 8 s timeouts and a 1.5 MB cap. */
    static final Http DEFAULT_HTTP = url -> {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(8000);
        c.setInstanceFollowRedirects(true);
        c.setRequestProperty("User-Agent", UA);
        c.setRequestProperty("Accept-Language", "ar,en;q=0.8");
        try {
            int code = c.getResponseCode();
            if (code >= 400) throw new IllegalStateException("HTTP " + code);
            String type = c.getContentType() == null ? "" : c.getContentType().toLowerCase(Locale.ROOT);
            try (InputStream in = c.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0 && out.size() < 1_500_000) out.write(buf, 0, n);
                Matcher cs = Pattern.compile("charset=([\\w-]+)").matcher(type);
                return new String(out.toByteArray(), cs.find() ? java.nio.charset.Charset.forName(cs.group(1)) : StandardCharsets.UTF_8);
            }
        } finally {
            c.disconnect();
        }
    };

    static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    // ------------------------------------------------------------------ search

    List<Result> search(String query, int max) throws Exception {
        Exception last = null;
        try {
            List<Result> r = parseBingRss(http.get("https://www.bing.com/search?format=rss&count=10&q=" + enc(query)));
            if (!r.isEmpty()) return r.subList(0, Math.min(max, r.size()));
        } catch (Exception e) { last = e; }
        try {
            List<Result> r = parseDuckDuckGo(http.get("https://html.duckduckgo.com/html/?q=" + enc(query)));
            if (!r.isEmpty()) return r.subList(0, Math.min(max, r.size()));
        } catch (Exception e) { last = e; }
        try {
            List<Result> r = wikipediaSearch(query, arabic(query) ? "ar" : "en", max);
            if (!r.isEmpty()) return r;
        } catch (Exception e) { last = e; }
        if (last != null) throw last;
        return new ArrayList<>();
    }

    static List<Result> parseBingRss(String xml) {
        List<Result> out = new ArrayList<>();
        Matcher m = Pattern.compile("<item>(.*?)</item>", Pattern.DOTALL).matcher(xml);
        while (m.find()) {
            String item = m.group(1);
            String link = tag(item, "link");
            if (link.isEmpty()) continue;
            out.add(new Result(unescape(tag(item, "title")), link, unescape(tag(item, "description"))));
        }
        return out;
    }

    static List<Result> parseDuckDuckGo(String html) {
        List<Result> out = new ArrayList<>();
        Matcher m = Pattern.compile("class=\"result__a\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>(.*?)(?=class=\"result__a\"|$)", Pattern.DOTALL).matcher(html);
        while (m.find()) {
            String href = unescape(m.group(1));
            Matcher u = Pattern.compile("uddg=([^&]+)").matcher(href);
            if (u.find()) try { href = URLDecoder.decode(u.group(1), "UTF-8"); } catch (Exception ignored) {}
            if (href.startsWith("//")) href = "https:" + href;
            Matcher sn = Pattern.compile("class=\"result__snippet\"[^>]*>(.*?)</a>", Pattern.DOTALL).matcher(m.group(3));
            out.add(new Result(stripTags(m.group(2)), href, sn.find() ? stripTags(sn.group(1)) : ""));
        }
        return out;
    }

    private static String tag(String xml, String name) {
        Matcher m = Pattern.compile("<" + name + ">(.*?)</" + name + ">", Pattern.DOTALL).matcher(xml);
        return m.find() ? m.group(1).replaceAll("^<!\\[CDATA\\[|]]>$", "").trim() : "";
    }

    static boolean arabic(String s) { return s.matches("(?s).*[\\u0600-\\u06FF].*"); }

    // ------------------------------------------------------------------ pages

    /** The page as text, keeping the paragraphs that share the most words with {@code focus}. */
    String read(String url, String focus, int maxChars) throws Exception {
        return relevant(htmlToText(http.get(url)), focus, maxChars);
    }

    static String htmlToText(String html) {
        String s = html.replaceAll("(?is)<(script|style|noscript|svg|nav|footer|header|form|aside)[^>]*>.*?</\\1>", " ")
                .replaceAll("(?is)<!--.*?-->", " ")
                .replaceAll("(?i)<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)[^>]*>", "\n")
                .replaceAll("(?i)<li[^>]*>", "\n• ");
        s = unescape(s.replaceAll("<[^>]+>", " "));
        StringBuilder b = new StringBuilder();
        for (String line : s.split("\n")) {
            String l = line.replaceAll("[ \\t\\u00A0]+", " ").trim();
            if (l.length() > 1) b.append(l).append('\n');
        }
        return b.toString().trim();
    }

    /** Paragraphs ranked by overlap with the question's words, in their original order, within maxChars. */
    static String relevant(String text, String focus, int maxChars) {
        if (text.length() <= maxChars) return text;
        String[] paras = text.split("\n");
        Set<String> words = new HashSet<>();
        for (String w : ArabicText.norm(focus == null ? "" : focus).split("[^\\p{L}\\p{N}]+")) if (w.length() > 2) words.add(w);
        double[] score = new double[paras.length];
        for (int i = 0; i < paras.length; i++) {
            String p = ArabicText.norm(paras[i]);
            int hits = 0;
            for (String w : words) if (p.contains(w)) hits++;
            score[i] = hits * 10 + Math.min(paras[i].length(), 300) / 100.0 - i * 0.01;
        }
        Integer[] order = new Integer[paras.length];
        for (int i = 0; i < order.length; i++) order[i] = i;
        Arrays.sort(order, (a, b) -> Double.compare(score[b], score[a]));
        boolean[] keep = new boolean[paras.length];
        int total = 0;
        for (int idx : order) {
            if (total + paras[idx].length() + 1 > maxChars) continue;
            keep[idx] = true;
            total += paras[idx].length() + 1;
        }
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < paras.length; i++) if (keep[i]) b.append(paras[i]).append('\n');
        return b.toString().trim();
    }

    static String stripTags(String s) { return unescape(s.replaceAll("<[^>]+>", "")).replaceAll("\\s+", " ").trim(); }

    static String unescape(String s) {
        Matcher m = Pattern.compile("&#(x?)([0-9a-fA-F]+);").matcher(s);
        StringBuffer b = new StringBuffer();
        while (m.find()) {
            int cp;
            try { cp = Integer.parseInt(m.group(2), m.group(1).isEmpty() ? 10 : 16); } catch (NumberFormatException e) { cp = '?'; }
            m.appendReplacement(b, Matcher.quoteReplacement(new String(Character.toChars(cp))));
        }
        m.appendTail(b);
        return b.toString().replace("&quot;", "\"").replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " ").replace("&amp;", "&");
    }

    // ------------------------------------------------------------------ Wikipedia

    List<Result> wikipediaSearch(String query, String lang, int max) throws Exception {
        JSONObject j = new JSONObject(http.get("https://" + lang + ".wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit="
                + max + "&srsearch=" + enc(query)));
        JSONArray a = j.optJSONObject("query") == null ? new JSONArray() : j.getJSONObject("query").optJSONArray("search");
        List<Result> out = new ArrayList<>();
        for (int i = 0; a != null && i < a.length(); i++) {
            JSONObject o = a.getJSONObject(i);
            String title = o.optString("title");
            out.add(new Result(title, "https://" + lang + ".wikipedia.org/wiki/" + enc(title.replace(' ', '_')).replace("+", "_"), stripTags(o.optString("snippet"))));
        }
        return out;
    }

    /** The introduction of the best-matching Wikipedia article. */
    String wikipedia(String query, String lang) throws Exception {
        List<Result> r = wikipediaSearch(query, lang, 1);
        if (r.isEmpty()) return "no Wikipedia article found for " + query;
        JSONObject j = new JSONObject(http.get("https://" + lang + ".wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&format=json&redirects=1&titles="
                + enc(r.get(0).title)));
        JSONObject pages = j.getJSONObject("query").getJSONObject("pages");
        String extract = pages.getJSONObject(pages.keys().next()).optString("extract");
        return r.get(0).title + " (" + r.get(0).url + ")\n" + (extract.length() > 2500 ? extract.substring(0, 2500) + "…" : extract);
    }

    // ------------------------------------------------------------------ weather and money

    String weather(String city) throws Exception {
        JSONObject g = new JSONObject(http.get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=" + (arabic(city) ? "ar" : "en") + "&name=" + enc(city)));
        JSONArray res = g.optJSONArray("results");
        if (res == null || res.length() == 0) return "city not found: " + city;
        JSONObject place = res.getJSONObject(0);
        JSONObject w = new JSONObject(http.get(String.format(Locale.US,
                "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f&timezone=auto&forecast_days=3"
                        + "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
                        + "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                place.getDouble("latitude"), place.getDouble("longitude"))));
        JSONObject cur = w.getJSONObject("current");
        StringBuilder b = new StringBuilder(place.optString("name")).append(", ").append(place.optString("country")).append('\n')
                .append(String.format(Locale.US, "now: %.0f°C (feels %.0f°C), %s, humidity %d%%, wind %.0f km/h\n",
                        cur.getDouble("temperature_2m"), cur.getDouble("apparent_temperature"), weatherText(cur.getInt("weather_code")),
                        cur.getInt("relative_humidity_2m"), cur.getDouble("wind_speed_10m")));
        JSONObject d = w.getJSONObject("daily");
        for (int i = 0; i < d.getJSONArray("time").length(); i++) {
            b.append(String.format(Locale.US, "%s: %.0f–%.0f°C, %s, rain %d%%\n", d.getJSONArray("time").getString(i),
                    d.getJSONArray("temperature_2m_min").getDouble(i), d.getJSONArray("temperature_2m_max").getDouble(i),
                    weatherText(d.getJSONArray("weather_code").getInt(i)), d.getJSONArray("precipitation_probability_max").optInt(i)));
        }
        return b.toString().trim();
    }

    static String weatherText(int code) {
        if (code == 0) return "clear";
        if (code <= 3) return "partly cloudy";
        if (code <= 48) return "fog";
        if (code <= 57) return "drizzle";
        if (code <= 67) return "rain";
        if (code <= 77) return "snow";
        if (code <= 82) return "showers";
        return "thunderstorm";
    }

    String currency(double amount, String from, String to) throws Exception {
        String f = from.trim().toUpperCase(Locale.ROOT), t = to.trim().toUpperCase(Locale.ROOT);
        JSONObject j = new JSONObject(http.get("https://open.er-api.com/v6/latest/" + enc(f)));
        if (!"success".equals(j.optString("result"))) return "unknown currency " + f;
        JSONObject rates = j.getJSONObject("rates");
        if (!rates.has(t)) return "unknown currency " + t;
        double rate = rates.getDouble(t);
        return String.format(Locale.US, "%s %s = %s %s (rate %s, updated %s)", Calculator.format(java.math.BigDecimal.valueOf(amount)), f,
                Calculator.format(java.math.BigDecimal.valueOf(amount * rate)), t, Calculator.format(java.math.BigDecimal.valueOf(rate)),
                j.optString("time_last_update_utc"));
    }
}
