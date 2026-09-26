package com.musab.aragpt2;

import static org.junit.Assert.*;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.json.JSONObject;
import org.junit.Test;

public class ChatToolsTest {
    @Test public void calculatorIsExact() {
        assertEquals("409.5", Calculator.format(Calculator.eval(Calculator.percentOf("17.5% من 2340"))));
        assertEquals("0.3", Calculator.format(Calculator.eval("0.1+0.2")));
        assertEquals("100", Calculator.format(Calculator.eval("٢٥×٤")));
        assertEquals("1024", Calculator.format(Calculator.eval("2^10")));
        assertEquals("512", Calculator.format(Calculator.eval("2^3^2")));
        assertEquals("-7", Calculator.format(Calculator.eval("-(3+4)")));
        assertEquals("12500", Calculator.format(Calculator.eval("12,500")));
        assertEquals("4", Calculator.format(Calculator.eval("sqrt(16)")));
        assertEquals("0.333333333333", Calculator.format(Calculator.eval("1/3")));
        try { Calculator.eval("1/0"); fail(); } catch (IllegalArgumentException expected) {}
        try { Calculator.eval("2+"); fail(); } catch (IllegalArgumentException expected) {}
        assertTrue(Calculator.looksLikeMath("(3+4)*12 ="));
        assertFalse(Calculator.looksLikeMath("اكتب قصة"));
        assertEquals("[17.5% من 2340 = 409.5]", Calculator.findAndSolve("كم 17.5% من 2340؟").toString());
        assertEquals("[12*37+5 = 449]", Calculator.findAndSolve("what is 12*37+5 please").toString());
        assertTrue(Calculator.findAndSolve("عمري 25 سنة").isEmpty());
    }

    @Test public void parsesBothToolCallFormats() {
        List<ChatTools.Call> json = ChatTools.parseCalls("ok\n<tool_call>\n{\"name\": \"web_search\", \"arguments\": {\"query\": \"سعر الذهب\"}}\n</tool_call>");
        assertEquals(1, json.size());
        assertEquals("web_search", json.get(0).name);
        assertEquals("سعر الذهب", json.get(0).args.optString("query"));
        List<ChatTools.Call> xml = ChatTools.parseCalls("<tool_call>\n<function=currency>\n<parameter=amount>\n250\n</parameter>\n<parameter=from>\nUSD\n</parameter>\n<parameter=to>\nTRY\n</parameter>\n</function>\n</tool_call>");
        assertEquals("currency", xml.get(0).name);
        assertEquals("250", xml.get(0).args.optString("amount"));
        assertEquals("TRY", xml.get(0).args.optString("to"));
        assertTrue(ChatTools.parseCalls("no calls here").isEmpty());
        assertTrue(ChatTools.parseCalls("<tool_call>{broken</tool_call>").isEmpty());
    }

    @Test public void parsesLfmPythonicCalls() {
        List<ChatTools.Call> c = ChatTools.parseCalls("Let me check.[currency(amount=250, from='USD', to='TRY')]");
        assertEquals(1, c.size());
        assertEquals("currency", c.get(0).name);
        assertEquals(250.0, c.get(0).args.optDouble("amount"), 0);
        assertEquals("TRY", c.get(0).args.optString("to"));
        c = ChatTools.parseCalls("<|tool_call_start|>[weather(city=\"Damascus\"), calculator(expression='(3-1)*2 + 2')]<|tool_call_end|>");
        assertEquals("[weather{\"city\":\"Damascus\"}, calculator{\"expression\":\"(3-1)*2 + 2\"}]", c.toString());
        assertTrue(ChatTools.parseCalls("an array [1, 2] and f(x) are not calls").isEmpty());
        assertArrayEquals(new String[]{"", "Let me check."}, ChatSession.split("Let me check.[currency(amount=250, from='USD', to='TRY')]"));
        assertArrayEquals(new String[]{"", "Wait "}, ChatSession.split("Wait [weather(city='Dam"));
    }

    @Test public void routesCurrencyQuestions() {
        assertArrayEquals(new String[]{"250", "USD", "TRY"}, ChatSession.currencyQuery("حول 250 دولار لليرة التركية"));
        assertArrayEquals(new String[]{"1", "USD", "SYP"}, ChatSession.currencyQuery("كم سعر الدولار بالسوري؟"));
        assertArrayEquals(new String[]{"100", "EUR", "USD"}, ChatSession.currencyQuery("how much is 100 euros in dollars"));
        assertArrayEquals(new String[]{"50", "SAR", "EGP"}, ChatSession.currencyQuery("قديش 50 ريال سعودي بالجنيه المصري"));
        assertNull(ChatSession.currencyQuery("كم سعر الذهب اليوم بالدولار؟"));
        assertNull(ChatSession.currencyQuery("اشرح لي الاقتصاد"));
    }

    @Test public void streamSplitKeepsThinkingApartAndHidesPartialTags() {
        assertArrayEquals(new String[]{"let me see", "The answer"}, ChatSession.split("<think>\nlet me see\n</think>\n\nThe answer"));
        assertArrayEquals(new String[]{"still thinking", ""}, ChatSession.split("<think>still thinking"));
        assertArrayEquals(new String[]{"", "Hello "}, ChatSession.split("Hello <tool_ca"));
        assertArrayEquals(new String[]{"", "Let me check.\n"}, ChatSession.split("Let me check.\n<tool_call>\n{\"name\":"));
        assertArrayEquals(new String[]{"", "plain"}, ChatSession.split("plain"));
    }

    @Test public void routesWeatherAndCleansSearchQueries() {
        assertEquals("دمشق", ChatSession.weatherCity("كيف الطقس بدمشق بكرا؟"));
        assertEquals("حلب", ChatSession.weatherCity("شو درجة الحرارة في حلب اليوم"));
        assertEquals("berlin", ChatSession.weatherCity("What's the weather in Berlin tomorrow?"));
        assertNull(ChatSession.weatherCity("من هي فيروز"));
        assertEquals("سعر الذهب اليوم بالدولار", ChatSession.searchQuery("كم سعر الذهب اليوم بالدولار؟"));
        assertTrue(ChatSession.timeSensitive("شو آخر الأخبار"));
        assertFalse(ChatSession.timeSensitive("اشرح لي الجاذبية"));
    }

    @Test public void readsSearchResultsAndPages() {
        String rss = "<rss><channel><item><title>Gold &amp; Silver</title><link>https://a.example/gold</link><description>Gold is 4,270 USD</description></item>"
                + "<item><title>Second</title><link>https://b.example</link><description>x</description></item></channel></rss>";
        List<WebTools.Result> r = WebTools.parseBingRss(rss);
        assertEquals(2, r.size());
        assertEquals("Gold & Silver", r.get(0).title);
        assertEquals("https://a.example/gold", r.get(0).url);
        String ddg = "<a rel=\"nofollow\" class=\"result__a\" href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fp&amp;rut=1\">Ex <b>Page</b></a>"
                + "<a class=\"result__snippet\" href=\"x\">A <b>snippet</b></a>";
        List<WebTools.Result> d = WebTools.parseDuckDuckGo(ddg);
        assertEquals("https://example.com/p", d.get(0).url);
        assertEquals("Ex Page", d.get(0).title);
        assertEquals("A snippet", d.get(0).snippet);
        String text = WebTools.htmlToText("<html><script>var x=1;</script><nav>menu</nav><h1>Title</h1><p>First &amp; best</p><p>&#1601;&#1610;&#1585;&#1608;&#1586;</p></html>");
        assertEquals("Title\nFirst & best\nفيروز", text);
        String rel = WebTools.relevant("intro text here\nnothing\nthe gold price is 4270 dollars today\nfooter", "gold price", 45);
        assertTrue(rel, rel.contains("gold price"));
    }

    /** The whole loop against a scripted model and fake internet: search grounding, a tool call, the answer. */
    @Test public void sessionRunsToolsThenAnswers() throws Exception {
        Map<String, String> web = new HashMap<>();
        WebTools.Http http = url -> {
            if (url.contains("bing.com")) return "<item><title>Gold today</title><link>https://g.example</link><description>Gold 4270 USD per ounce</description></item>";
            if (url.contains("g.example")) return "<p>Gold price today is 4270 dollars per ounce according to the market data feed.</p>" + "<p>" + "x".repeat(250) + "</p>";
            if (url.contains("open.er-api.com")) return "{\"result\":\"success\",\"time_last_update_utc\":\"today\",\"rates\":{\"TRY\":48.9}}";
            throw new IllegalStateException("unexpected " + url);
        };
        ChatTools tools = new ChatTools(new WebTools(http));
        List<List<ChatMessage>> prompts = new ArrayList<>();
        String[] replies = {
                "<tool_call>\n{\"name\": \"currency\", \"arguments\": {\"amount\": 10, \"from\": \"USD\", \"to\": \"TRY\"}}\n</tool_call>",
                "Gold is 4270 USD [1]; 10 USD is 489 TRY."};
        int[] n = {0};
        ChatSession s = new ChatSession((turns, l) -> {
            prompts.add(new ArrayList<>(turns));
            String out = replies[n[0]++];
            if (l != null) l.onText(out);
            return new GenerationResult(out, 10, 5, 1, 10, 5, GenerationResult.STOP_EOG, 0);
        }, tools);
        List<ChatMessage> h = new ArrayList<>();
        h.add(new ChatMessage(1, ChatMessage.ROLE_USER, "كم سعر الذهب اليوم؟ وبدي كمان تحويل مبلغ صغير", 0));
        List<String> events = new ArrayList<>();
        ChatSession.Reply r = s.run(h, new ChatSession.Options(), new ChatSession.Listener() {
            @Override public void onUpdate(String thinking, String answer) {}
            @Override public void onTool(String name, String args, String result) { if (result != null) events.add(name); }
        });
        assertEquals("Gold is 4270 USD [1]; 10 USD is 489 TRY.", r.answer);
        assertEquals("[web_search, currency]", events.toString());
        assertEquals(1, r.sources.size());
        String firstPrompt = prompts.get(0).get(prompts.get(0).size() - 1).text;
        assertTrue(firstPrompt, firstPrompt.contains("<web_results>") && firstPrompt.contains("Gold price today is 4270"));
        assertTrue(prompts.get(0).get(0).text.contains("# Tools"));
        String second = prompts.get(1).get(prompts.get(1).size() - 1).text;
        assertTrue(second, second.startsWith("<tool_response>") && second.contains("10 USD = 489 TRY"));
        assertNotNull(web);
        assertEquals("10 USD = 489 TRY (rate 48.9, updated today)", tools.run(new ChatTools.Call("currency",
                new JSONObject().put("amount", 10).put("from", "usd").put("to", "try")), new ArrayList<>()));
    }
}
