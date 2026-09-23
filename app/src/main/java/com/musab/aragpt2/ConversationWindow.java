package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Selects a bounded conversation window without letting one large turn discard newer turns. */
public final class ConversationWindow {
    private static final int TURN_OVERHEAD_CHARS = 16;

    private ConversationWindow() {}

    public static List<ChatMessage> select(List<ChatMessage> history, int maxChars) {
        if (history == null || history.isEmpty() || maxChars <= 0) return Collections.emptyList();

        List<ChatMessage> result = new ArrayList<>();
        int remaining = maxChars;

        // System context is structural and should survive ordinary history trimming.
        for (ChatMessage message : history) {
            if (message.role != ChatMessage.ROLE_SYSTEM) continue;
            int cost = cost(message);
            if (cost <= remaining) {
                result.add(message);
                remaining -= cost;
            }
        }

        List<ChatMessage> recent = new ArrayList<>();
        for (int i = history.size() - 1; i >= 0; i--) {
            ChatMessage message = history.get(i);
            if (message.role == ChatMessage.ROLE_SYSTEM) continue;
            int cost = cost(message);
            // Skip an oversized old turn instead of breaking and losing all newer turns.
            if (cost <= remaining) {
                recent.add(message);
                remaining -= cost;
            }
        }
        Collections.reverse(recent);
        result.addAll(recent);
        return result;
    }

    private static int cost(ChatMessage message) {
        return (message.text == null ? 0 : message.text.length()) + TURN_OVERHEAD_CHARS;
    }
}
