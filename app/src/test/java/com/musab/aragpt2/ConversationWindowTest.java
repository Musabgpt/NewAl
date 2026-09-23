package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class ConversationWindowTest {
    @Test
    public void keepsNewestUserTurnWithinBudgetInsteadOfDroppingWholeWindow() {
        List<ChatMessage> history = Arrays.asList(
                new ChatMessage(1, ChatMessage.ROLE_USER, "old question", 0),
                new ChatMessage(2, ChatMessage.ROLE_ASSISTANT, "old answer", 0),
                new ChatMessage(3, ChatMessage.ROLE_USER, "new question", 0),
                new ChatMessage(4, ChatMessage.ROLE_ASSISTANT, "new answer", 0));

        List<ChatMessage> result = ConversationWindow.select(history, 35);

        assertTrue(result.size() < history.size());
        assertEquals("new question", result.get(result.size() - 2).text);
        assertEquals("new answer", result.get(result.size() - 1).text);
    }

    @Test
    public void preservesSystemMessageAndNewestTurnWhenThereIsRoom() {
        List<ChatMessage> history = Arrays.asList(
                new ChatMessage(-1, ChatMessage.ROLE_SYSTEM, "memory", 0),
                new ChatMessage(1, ChatMessage.ROLE_USER, "old", 0),
                new ChatMessage(2, ChatMessage.ROLE_ASSISTANT, "old answer", 0),
                new ChatMessage(3, ChatMessage.ROLE_USER, "new", 0));

        List<ChatMessage> result = ConversationWindow.select(history, 64);

        assertEquals(ChatMessage.ROLE_SYSTEM, result.get(0).role);
        assertEquals("new", result.get(result.size() - 1).text);
    }
}
