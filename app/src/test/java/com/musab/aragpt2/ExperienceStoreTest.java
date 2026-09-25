package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class ExperienceStoreTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    private static Map<String, String> files(String code) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("main.py", code);
        return m;
    }

    @Test public void recallsSimilarSolvedTasksAndSurvivesRestart() throws Exception {
        File dir = tmp.newFolder();
        ExperienceStore s = new ExperienceStore(dir);
        s.record(new ExperienceStore.Experience("write a prime number printer", "algo", "code", "algo", true, 1, files("print(2)")));
        s.record(new ExperienceStore.Experience("scrape a website", "scraper", "code", "", false, 3, new LinkedHashMap<>()));
        ExperienceStore reloaded = new ExperienceStore(dir);
        assertEquals(2, reloaded.count());
        assertEquals(1, reloaded.successes());
        List<ExperienceStore.Experience> hits = reloaded.similar("print the first prime number values", 2);
        assertEquals(1, hits.size());
        assertEquals("print(2)", hits.get(0).files.get("main.py"));
        assertTrue(reloaded.similar("totally unrelated banana", 2).isEmpty());
    }

    @Test public void remembersFixes() throws Exception {
        File dir = tmp.newFolder();
        new ExperienceStore(dir).recordFix("nameerror: name 'x' is not defined", "EDIT: main.py ...");
        ExperienceStore s = new ExperienceStore(dir);
        assertEquals("EDIT: main.py ...", s.knownFix("nameerror: name 'x' is not defined"));
        assertNull(s.knownFix("other"));
    }

    @Test public void banditPrefersTheStrategyThatEarnsReward() throws Exception {
        ExperienceStore s = new ExperienceStore(tmp.newFolder());
        List<String> arms = Arrays.asList("code", "plan");
        assertEquals("code", s.choose("api", arms, "code"));   // heuristic default tried first
        s.reward("api", "code", 0);
        assertEquals("plan", s.choose("api", arms, "code"));   // untried arm next
        for (int i = 0; i < 6; i++) { s.reward("api", "plan", 1.0); s.reward("api", "code", 0.0); }
        assertEquals("plan", s.choose("api", arms, "code"));   // learned from rewards
        assertEquals("code", s.choose("other", arms, "code")); // categories are separate
        assertTrue(s.summary().contains("api:"));
    }

    @Test public void rewardFavoursFewerAttempts() {
        assertEquals(1.0, ExperienceStore.reward(true, 1), 1e-9);
        assertTrue(ExperienceStore.reward(true, 3) < ExperienceStore.reward(true, 2));
        assertEquals(0.0, ExperienceStore.reward(false, 1), 1e-9);
    }

    @Test public void exportsChatFormatTrainingData() throws Exception {
        ExperienceStore s = new ExperienceStore(tmp.newFolder());
        s.record(new ExperienceStore.Experience("hello", "general", "code", "", true, 1, files("print('hi')\n")));
        s.record(new ExperienceStore.Experience("broken", "general", "code", "", false, 6, new LinkedHashMap<>()));
        File out = tmp.newFile("train.jsonl");
        assertEquals(1, s.exportTrainingData(out, "SYS"));
        String line = new String(Files.readAllBytes(out.toPath())).trim();
        org.json.JSONArray msgs = new org.json.JSONObject(line).getJSONArray("messages");
        assertEquals("system", msgs.getJSONObject(0).getString("role"));
        assertEquals("Task: hello", msgs.getJSONObject(1).getString("content"));
        assertTrue(msgs.getJSONObject(2).getString("content").startsWith("FILE: main.py\n```python\nprint('hi')"));
    }
}
