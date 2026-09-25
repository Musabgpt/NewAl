package com.musab.aragpt2;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * What the agent has learned from real outcomes (Termux exit codes), kept on the phone:
 * <ul>
 *   <li>solved tasks with their working files, recalled as examples for similar requests;</li>
 *   <li>fixes that resolved a given error, suggested when the same error appears again;</li>
 *   <li>a reward-driven strategy choice (UCB1 bandit) of which agent works best per kind of task.</li>
 * </ul>
 * It changes the prompts and choices, not the model weights; {@link #exportTrainingData} writes
 * the successful trajectories as a fine-tuning dataset for training the weights elsewhere.
 */
public final class ExperienceStore {
    public static final class Experience {
        public final String request, category, agent, skill;
        public final boolean success;
        public final int attempts;
        public final Map<String, String> files;

        public Experience(String request, String category, String agent, String skill, boolean success,
                          int attempts, Map<String, String> files) {
            this.request = request; this.category = category; this.agent = agent; this.skill = skill;
            this.success = success; this.attempts = attempts; this.files = files;
        }
    }

    private static final int MAX_FILE_CHARS = 6000;
    private final File experiencesFile, fixesFile, statsFile;
    private final List<Experience> experiences = new ArrayList<>();
    private final Map<String, String> fixes = new LinkedHashMap<>();
    /** category -> arm -> {count, reward sum} */
    private final Map<String, Map<String, double[]>> stats = new LinkedHashMap<>();

    public ExperienceStore(File dir) {
        dir.mkdirs();
        experiencesFile = new File(dir, "experiences.jsonl");
        fixesFile = new File(dir, "fixes.json");
        statsFile = new File(dir, "strategy_stats.json");
        load();
    }

    // ------------------------------------------------------------ experiences

    public synchronized void record(Experience e) {
        experiences.add(e);
        try {
            JSONObject o = new JSONObject().put("request", e.request).put("category", e.category)
                    .put("agent", e.agent).put("skill", e.skill).put("success", e.success)
                    .put("attempts", e.attempts).put("files", new JSONObject(e.files))
                    .put("t", System.currentTimeMillis());
            append(experiencesFile, o.toString() + "\n");
        } catch (JSONException | IOException ignored) {
        }
    }

    public synchronized int count() { return experiences.size(); }

    public synchronized int successes() {
        int n = 0;
        for (Experience e : experiences) if (e.success) n++;
        return n;
    }

    /** Most similar successful experiences (word overlap), best first. */
    public synchronized List<Experience> similar(String request, int k) {
        Set<String> q = words(request);
        List<Experience> ranked = new ArrayList<>();
        List<Double> scores = new ArrayList<>();
        for (Experience e : experiences) {
            if (!e.success || e.files.isEmpty()) continue;
            double s = jaccard(q, words(e.request));
            if (s < 0.2) continue;
            int i = 0;
            while (i < scores.size() && scores.get(i) >= s) i++;
            ranked.add(i, e);
            scores.add(i, s);
        }
        return ranked.subList(0, Math.min(k, ranked.size()));
    }

    // ------------------------------------------------------------ fixes

    /** Remembers the model output that fixed an error with this signature. */
    public synchronized void recordFix(String signature, String fix) {
        if (signature == null || signature.isEmpty() || fix == null || fix.trim().isEmpty()) return;
        fixes.remove(signature);
        fixes.put(signature, fix.length() > 2000 ? fix.substring(0, 2000) : fix);
        while (fixes.size() > 300) fixes.remove(fixes.keySet().iterator().next());
        try { write(fixesFile, new JSONObject(fixes).toString()); } catch (IOException ignored) {}
    }

    public synchronized String knownFix(String signature) { return fixes.get(signature); }

    // ------------------------------------------------------------ strategy learning (UCB1)

    /** Reward in [0, 1]: success counts most, fewer attempts is better. */
    public static double reward(boolean success, int attempts) {
        if (!success) return 0;
        return Math.max(0.3, 1.0 - 0.15 * Math.max(0, attempts - 1));
    }

    public synchronized void reward(String category, String arm, double reward) {
        double[] s = stats.computeIfAbsent(category, k -> new LinkedHashMap<>()).computeIfAbsent(arm, k -> new double[2]);
        s[0] += 1;
        s[1] += reward;
        saveStats();
    }

    /**
     * Picks an arm by UCB1. Arms never tried in this category are tried first, starting with
     * {@code preferred} (the heuristic's choice), so good defaults are kept until data says otherwise.
     */
    public synchronized String choose(String category, List<String> arms, String preferred) {
        Map<String, double[]> cat = stats.getOrDefault(category, new LinkedHashMap<>());
        if (!cat.containsKey(preferred) && arms.contains(preferred)) return preferred;
        for (String a : arms) if (!cat.containsKey(a)) return a;
        double total = 0;
        for (String a : arms) total += cat.get(a)[0];
        String best = preferred;
        double bestScore = -1;
        for (String a : arms) {
            double[] s = cat.get(a);
            double score = s[1] / s[0] + Math.sqrt(2 * Math.log(Math.max(1, total)) / s[0]);
            if (score > bestScore) { bestScore = score; best = a; }
        }
        return best;
    }

    /** Human-readable learning summary: success rate per category and strategy. */
    public synchronized String summary() {
        StringBuilder b = new StringBuilder();
        b.append(String.format(Locale.US, "%d tasks, %d solved, %d remembered fixes\n", count(), successes(), fixes.size()));
        for (Map.Entry<String, Map<String, double[]>> c : stats.entrySet()) {
            b.append('\n').append(c.getKey()).append(":\n");
            for (Map.Entry<String, double[]> a : c.getValue().entrySet()) {
                b.append(String.format(Locale.US, "  %s: %d runs, avg reward %.2f\n", a.getKey(), (int) a.getValue()[0],
                        a.getValue()[1] / a.getValue()[0]));
            }
        }
        return b.toString();
    }

    // ------------------------------------------------------------ export

    /**
     * Writes successful trajectories as chat-format JSONL (system / user / assistant), ready
     * for supervised fine-tuning (e.g. Unsloth on a free Colab GPU) and conversion to GGUF.
     */
    public synchronized int exportTrainingData(File out, String systemPrompt) throws IOException {
        StringBuilder b = new StringBuilder();
        int n = 0;
        for (Experience e : experiences) {
            if (!e.success || e.files.isEmpty()) continue;
            StringBuilder answer = new StringBuilder();
            for (Map.Entry<String, String> f : e.files.entrySet()) {
                answer.append("FILE: ").append(f.getKey()).append("\n```").append(AgentLoop.languageFor(f.getKey()))
                        .append('\n').append(f.getValue());
                if (!f.getValue().endsWith("\n")) answer.append('\n');
                answer.append("```\n");
            }
            try {
                JSONArray msgs = new JSONArray()
                        .put(new JSONObject().put("role", "system").put("content", systemPrompt))
                        .put(new JSONObject().put("role", "user").put("content", "Task: " + e.request))
                        .put(new JSONObject().put("role", "assistant").put("content", answer.toString().trim()));
                b.append(new JSONObject().put("messages", msgs)).append('\n');
                n++;
            } catch (JSONException ignored) {
            }
        }
        write(out, b.toString());
        return n;
    }

    // ------------------------------------------------------------ persistence

    private void load() {
        try {
            for (String line : read(experiencesFile).split("\n")) {
                if (line.trim().isEmpty()) continue;
                JSONObject o = new JSONObject(line);
                Map<String, String> files = new LinkedHashMap<>();
                JSONObject f = o.optJSONObject("files");
                if (f != null) for (java.util.Iterator<String> it = f.keys(); it.hasNext(); ) {
                    String k = it.next();
                    files.put(k, f.getString(k));
                }
                experiences.add(new Experience(o.optString("request"), o.optString("category"), o.optString("agent"),
                        o.optString("skill"), o.optBoolean("success"), o.optInt("attempts"), files));
            }
        } catch (IOException | JSONException ignored) {
        }
        try {
            JSONObject f = new JSONObject(read(fixesFile));
            for (java.util.Iterator<String> it = f.keys(); it.hasNext(); ) {
                String k = it.next();
                fixes.put(k, f.getString(k));
            }
        } catch (IOException | JSONException ignored) {
        }
        try {
            JSONObject s = new JSONObject(read(statsFile));
            for (java.util.Iterator<String> it = s.keys(); it.hasNext(); ) {
                String cat = it.next();
                JSONObject arms = s.getJSONObject(cat);
                Map<String, double[]> m = new LinkedHashMap<>();
                for (java.util.Iterator<String> a = arms.keys(); a.hasNext(); ) {
                    String arm = a.next();
                    JSONArray v = arms.getJSONArray(arm);
                    m.put(arm, new double[]{v.getDouble(0), v.getDouble(1)});
                }
                stats.put(cat, m);
            }
        } catch (IOException | JSONException ignored) {
        }
    }

    private void saveStats() {
        try {
            JSONObject s = new JSONObject();
            for (Map.Entry<String, Map<String, double[]>> c : stats.entrySet()) {
                JSONObject arms = new JSONObject();
                for (Map.Entry<String, double[]> a : c.getValue().entrySet()) {
                    arms.put(a.getKey(), new JSONArray().put(a.getValue()[0]).put(a.getValue()[1]));
                }
                s.put(c.getKey(), arms);
            }
            write(statsFile, s.toString());
        } catch (JSONException | IOException ignored) {
        }
    }

    /** Files of a finished project, trimmed so memory stays small. */
    public static Map<String, String> snapshot(ProjectWorkspace ws) {
        Map<String, String> m = new LinkedHashMap<>();
        int budget = MAX_FILE_CHARS;
        for (ProjectWorkspace.Entry e : ws.entries()) {
            if (e.status != ProjectWorkspace.FileStatus.COMPLETE) continue;
            try {
                String c = ws.read(e.path);
                if (c.length() > budget) continue;
                m.put(e.path, c);
                budget -= c.length();
            } catch (IOException ignored) {
            }
        }
        return m;
    }

    static Set<String> words(String s) {
        Set<String> w = new HashSet<>();
        for (String t : s.toLowerCase(Locale.ROOT).split("[^\\p{L}\\p{N}]+")) if (t.length() > 1) w.add(t);
        return w;
    }

    private static double jaccard(Set<String> a, Set<String> b) {
        if (a.isEmpty() || b.isEmpty()) return 0;
        int inter = 0;
        for (String x : a) if (b.contains(x)) inter++;
        return (double) inter / (a.size() + b.size() - inter);
    }

    private static String read(File f) throws IOException {
        if (!f.isFile()) return "";
        return new String(ProjectWorkspace.readBytes(f), StandardCharsets.UTF_8);
    }

    private static void write(File f, String s) throws IOException {
        File tmp = new File(f.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) { out.write(s.getBytes(StandardCharsets.UTF_8)); }
        if (!tmp.renameTo(f)) throw new IOException("rename failed");
    }

    private static void append(File f, String s) throws IOException {
        try (FileOutputStream out = new FileOutputStream(f, true)) { out.write(s.getBytes(StandardCharsets.UTF_8)); }
    }
}
