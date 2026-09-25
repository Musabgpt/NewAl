package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Deterministic local memory; durable facts require an explicit remember request. */
public final class MemoryManager {
    // Conservative chars-per-token estimate covering mixed Arabic/English text;
    // real tokenizers give ~1-2 chars/token for Arabic, so this keeps the char
    // budget safely under the model's actual token budget.
    private static final double CHARS_PER_TOKEN_ESTIMATE = 2.0;
    private static final int GENERATION_SAFETY_MARGIN_TOKENS = 64;
    private static final int MIN_CONTEXT_TOKENS_BUDGET = 128;

    private static final int MAX_MEMORY_CHARS = 1200;
    private static final int MAX_SUMMARY_CHARS = 1000;
    private static final int SUMMARY_REFRESH_INTERVAL = 5;

    private static final Pattern EXPLICIT = Pattern.compile(
            "^(?:remember|please remember|تذكر|تذكّر)\\s*[:：-]?\\s*(.+)$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern NAME = Pattern.compile(
            "^(?:my name is|اسمي)\\s+(.+)$", Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private final ChatHistoryStore store;

    public MemoryManager(ChatHistoryStore store) { this.store = store; }

    public void rememberExplicit(String text) {
        if (text == null) return;
        Matcher m = EXPLICIT.matcher(text.trim());
        if (!m.matches()) return;
        String body = m.group(1).trim();
        if (body.isEmpty()) return;

        Matcher name = NAME.matcher(body);
        if (name.matches()) {
            store.saveFact("name", name.group(1).trim());
            return;
        }

        int eq = body.indexOf('=');
        if (eq > 0 && eq < body.length() - 1) {
            store.saveFact(body.substring(0, eq).trim().toLowerCase(Locale.ROOT),
                    body.substring(eq + 1).trim());
        } else {
            store.saveFact("note_" + Integer.toHexString(body.hashCode()), body);
        }
    }

    /**
     * Builds the turn list to send to the engine.
     *
     * @param contextTokens the model's context window size (n_ctx)
     * @param maxNewTokens  the max tokens the engine is allowed to generate this turn
     */
    public List<ChatMessage> buildTurns(List<ChatMessage> history, int contextTokens, int maxNewTokens) {
        int availableTokens = Math.max(MIN_CONTEXT_TOKENS_BUDGET,
                contextTokens - maxNewTokens - GENERATION_SAFETY_MARGIN_TOKENS);
        int maxContextChars = (int) (availableTokens * CHARS_PER_TOKEN_ESTIMATE);

        List<ChatMessage> recent = new ArrayList<>();
        String memory = memoryBlock();
        int budget = maxContextChars - memory.length();

        for (int i = history.size() - 1; i >= 0; i--) {
            ChatMessage m = history.get(i);
            int cost = m.text.length() + 16;
            if (cost > budget) break;
            budget -= cost;
            recent.add(0, m);
        }

        if (!memory.isEmpty()) {
            List<ChatMessage> result = new ArrayList<>(recent.size() + 1);
            result.add(new ChatMessage(-2, ChatMessage.ROLE_SYSTEM, memory, 0));
            result.addAll(recent);
            return result;
        }
        return recent;
    }

    private String memoryBlock() {
        Map<String, String> facts = store.loadFacts();
        String summary = store.getSummary();
        StringBuilder b = new StringBuilder();
        if (!facts.isEmpty()) {
            b.append("Persistent user memory (explicitly saved by the user):\n");
            for (Map.Entry<String, String> e : facts.entrySet()) {
                b.append("- ").append(e.getKey()).append(": ").append(e.getValue()).append('\n');
                if (b.length() >= MAX_MEMORY_CHARS) break;
            }
        }
        if (summary != null && !summary.trim().isEmpty()) {
            b.append("Earlier conversation extract:\n").append(summary.trim());
        }
        return b.length() > MAX_MEMORY_CHARS ? b.substring(0, MAX_MEMORY_CHARS) : b.toString();
    }

    /**
     * Recomputes the extractive summary only every SUMMARY_REFRESH_INTERVAL user
     * turns. Recomputing on every turn changes the memory block's text on every
     * request, which invalidates the native KV-cache prefix match and forces a
     * full context reprocess each time -- this is the main cause of the app
     * getting progressively slower during a long conversation.
     */
    public void refreshExtractiveSummary(List<ChatMessage> history) {
        if (history.size() < 10) return;
        if (history.size() % SUMMARY_REFRESH_INTERVAL != 0) return;

        LinkedHashSet<String> pieces = new LinkedHashSet<>();
        int recentStart = Math.max(0, history.size() - 20);
        for (int i = 0; i < recentStart; i++) {
            ChatMessage m = history.get(i);
            if (m.role != ChatMessage.ROLE_USER) continue;
            String text = m.text.replaceAll("\\s+", " ").trim();
            if (text.isEmpty()) continue;
            int dot = text.indexOf('.');
            int end = dot > 40 ? dot + 1 : Math.min(text.length(), 140);
            pieces.add(text.substring(0, end));
        }
        StringBuilder summary = new StringBuilder();
        for (String piece : pieces) {
            if (summary.length() + piece.length() + 2 > MAX_SUMMARY_CHARS) break;
            if (summary.length() > 0) summary.append(" | ");
            summary.append(piece);
        }
        store.setSummary(summary.toString());
    }
}
