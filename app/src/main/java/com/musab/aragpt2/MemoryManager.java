package com.musab.aragpt2;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Deterministic local memory; durable facts require an explicit remember request. */
public final class MemoryManager {
    // Keep the Java-side prompt conservative; the native bridge also enforces the real token limit.
    private static final int MAX_CONTEXT_CHARS = 2800;
    private static final int MAX_MEMORY_CHARS = 800;
    private static final int MAX_SUMMARY_CHARS = 700;

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

    public List<ChatMessage> buildTurns(List<ChatMessage> allHistory) {
        // Code, terminal output and result cards are for display only, not chat context.
        java.util.ArrayList<ChatMessage> history = new java.util.ArrayList<>(allHistory.size());
        for (ChatMessage m : allHistory) if (ChatMessage.isConversation(m.role)) history.add(m);
        String memory = memoryBlock();
        int budget = Math.max(0, MAX_CONTEXT_CHARS - memory.length());
        List<ChatMessage> recent = ConversationWindow.select(history, budget);

        if (!memory.isEmpty()) {
            java.util.ArrayList<ChatMessage> result =
                    new java.util.ArrayList<>(recent.size() + 1);
            result.add(new ChatMessage(-2, ChatMessage.ROLE_SYSTEM, memory, 0));
            result.addAll(recent);
            return result;
        }
        return recent;
    }

    /** What the user asked to be remembered, and an extract of the earlier conversation ("" when none). */
    public String memoryText() { return memoryBlock(); }

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

    public void refreshExtractiveSummary(List<ChatMessage> history) {
        if (history.size() < 10) return;
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
