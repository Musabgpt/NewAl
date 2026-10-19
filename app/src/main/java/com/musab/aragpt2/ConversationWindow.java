package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Selects a bounded conversation window without losing the current user request. */
public final class ConversationWindow {
    private static final int TURN_OVERHEAD_CHARS = 16;

    private ConversationWindow() {}

    public static List<ChatMessage> select(List<ChatMessage> history, int maxChars) {
        if (history == null || history.isEmpty() || maxChars <= 0) return Collections.emptyList();

        List<ChatMessage> result = new ArrayList<>();
        int remaining = maxChars;

        for (ChatMessage message : history) {
            if (message.role != ChatMessage.ROLE_SYSTEM) continue;
            int cost = cost(message);
            if (cost <= remaining) {
                result.add(message);
                remaining -= cost;
            }
        }

        int newestUserIndex = -1;
        for (int i = history.size() - 1; i >= 0; i--) {
            if (history.get(i).role == ChatMessage.ROLE_USER) {
                newestUserIndex = i;
                break;
            }
        }

        List<ChatMessage> recent = new ArrayList<>();
        if (newestUserIndex >= 0) {
            ChatMessage newest = history.get(newestUserIndex);
            int availableText = Math.max(0, remaining - TURN_OVERHEAD_CHARS);
            if (newest.text.length() + TURN_OVERHEAD_CHARS <= remaining) {
                recent.add(newest);
                remaining -= cost(newest);
            } else {
                // Never drop the current request completely. Keep its leading portion.
                String clipped = newest.text.substring(0, Math.min(newest.text.length(), availableText));
                recent.add(new ChatMessage(newest.id, newest.role, clipped, newest.timeMs));
                remaining = 0;
            }
        }

        for (int i = newestUserIndex - 1; i >= 0; i--) {
            ChatMessage message = history.get(i);
            if (message.role == ChatMessage.ROLE_SYSTEM) continue;
            int cost = cost(message);
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
