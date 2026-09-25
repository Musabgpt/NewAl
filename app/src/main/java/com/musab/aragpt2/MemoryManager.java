package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Deterministic local memory + context-window planner.
 *
 * Stable-prefix design: the window start only moves in large jumps (when the
 * token budget overflows) and the summary of dropped messages is rebuilt only
 * at those jumps. Between jumps the prompt is append-only, so the native
 * KV cache can reuse everything that was already processed.
 */
public final class MemoryManager {
    private static final int SAFETY_MARGIN_TOKENS = 64;
    private static final int MIN_HISTORY_TOKENS = 192;
    private static final int PER_MESSAGE_OVERHEAD_TOKENS = 6;
    /** After a jump, fill only this fraction of the budget so the window can grow again. */
    private static final double REFILL_FRACTION = 0.55;

    private static final int MAX_MEMORY_CHARS = 1200;
    private static final int MAX_SUMMARY_CHARS = 600;
    private static final int SUMMARY_PIECE_CHARS = 120;

    private static final Pattern EXPLICIT = Pattern.compile(
            "^(?:remember|please remember|تذكر|تذكّر)\\s*[:：-]?\\s*(.+)$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern NAME = Pattern.compile(
            "^(?:my name is|اسمي)\\s+(.+)$", Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private final ChatHistoryStore store;
    private long windowStartId = Long.MIN_VALUE;

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

    /** Call when the chat is cleared. */
    public synchronized void resetWindow() {
        windowStartId = Long.MIN_VALUE;
    }

    /**
     * Builds the turn list to send to the engine.
     *
     * @param contextTokens the model's context window size (n_ctx)
     * @param maxNewTokens  the max tokens the engine may generate this turn
     */
    public synchronized List<ChatMessage> buildTurns(List<ChatMessage> history,
                                                     int contextTokens, int maxNewTokens) {
        List<ChatMessage> result = new ArrayList<>();
        if (history == null || history.isEmpty()) return result;

        int start = indexOfId(history, windowStartId);
        if (start < 0) start = 0;

        String memory = memoryBlock(start > 0);
        int budget = historyBudget(contextTokens, maxNewTokens, memory);

        if (costFrom(history, start) > budget) {
            int target = Math.max(1, (int) (budget * REFILL_FRACTION));
            int newStart = history.size() - 1;
            int used = messageCost(history.get(newStart));
            while (newStart - 1 > start) {
                int c = messageCost(history.get(newStart - 1));
                if (used + c > target) break;
                used += c;
                newStart--;
            }
            // Chat templates expect the conversation to start with a user turn.
            while (newStart < history.size() - 1
                    && history.get(newStart).role != ChatMessage.ROLE_USER) {
                newStart++;
            }
            start = Math.max(start, newStart);
            rebuildSummary(history, start);
            memory = memoryBlock(true);
        }
        windowStartId = history.get(start).id;

        if (!memory.isEmpty()) {
            result.add(new ChatMessage(-2, ChatMessage.ROLE_SYSTEM, memory, 0));
        }
        result.addAll(history.subList(start, history.size()));
        return result;
    }

    private static int historyBudget(int contextTokens, int maxNewTokens, String memory) {
        return Math.max(MIN_HISTORY_TOKENS,
                contextTokens - maxNewTokens - SAFETY_MARGIN_TOKENS - estimateTokens(memory));
    }

    private static int indexOfId(List<ChatMessage> history, long id) {
        if (id == Long.MIN_VALUE) return -1;
        for (int i = 0; i < history.size(); i++) {
            if (history.get(i).id == id) return i;
        }
        return -1;
    }

    private static int costFrom(List<ChatMessage> history, int start) {
        int total = 0;
        for (int i = start; i < history.size(); i++) total += messageCost(history.get(i));
        return total;
    }

    private static int messageCost(ChatMessage m) {
        return estimateTokens(m.text) + PER_MESSAGE_OVERHEAD_TOKENS;
    }

    /**
     * Script-aware token estimate: ASCII (code, English) is ~3 chars/token,
     * Arabic and other non-ASCII text is far denser in tokens.
     */
    static int estimateTokens(String s) {
        if (s == null || s.isEmpty()) return 0;
        int ascii = 0, other = 0;
        for (int i = 0; i < s.length(); i++) {
            if (s.charAt(i) < 128) ascii++; else other++;
        }
        return (int) Math.ceil(ascii / 3.0 + other / 1.3);
    }

    private String memoryBlock(boolean includeSummary) {
        Map<String, String> facts = store.loadFacts();
        StringBuilder b = new StringBuilder();
        if (!facts.isEmpty()) {
            b.append("Persistent user memory (explicitly saved by the user):\n");
            for (Map.Entry<String, String> e : facts.entrySet()) {
                b.append("- ").append(e.getKey()).append(": ").append(e.getValue()).append('\n');
                if (b.length() >= MAX_MEMORY_CHARS) break;
            }
        }
        if (includeSummary) {
            String summary = store.getSummary();
            if (summary != null && !summary.trim().isEmpty()) {
                b.append("Earlier topics in this chat (context only, do not answer them again):\n")
                        .append(summary.trim());
            }
        }
        return b.length() > MAX_MEMORY_CHARS ? b.substring(0, MAX_MEMORY_CHARS) : b.toString();
    }

    /** Extracts the most recent user requests among the messages that left the window. */
    private void rebuildSummary(List<ChatMessage> history, int endExclusive) {
        List<String> pieces = new ArrayList<>();
        int total = 0;
        for (int i = endExclusive - 1; i >= 0; i--) {
            ChatMessage m = history.get(i);
            if (m.role != ChatMessage.ROLE_USER) continue;
            String t = m.text.replaceAll("\\s+", " ").trim();
            if (t.isEmpty()) continue;
            if (t.length() > SUMMARY_PIECE_CHARS) t = t.substring(0, SUMMARY_PIECE_CHARS) + "…";
            if (total + t.length() + 3 > MAX_SUMMARY_CHARS) break;
            pieces.add(0, t);
            total += t.length() + 3;
        }
        store.setSummary(String.join(" | ", pieces));
    }
}
