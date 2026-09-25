package com.musab.aragpt2;

public final class ChatMessage {
    public static final int ROLE_USER=0, ROLE_ASSISTANT=1, ROLE_SYSTEM=2;
    /** Agent-mode bubbles: generated code, program/terminal output, and the task result card. */
    public static final int ROLE_CODE=3, ROLE_TERMINAL=4, ROLE_RESULT=5;
    /** Roles that are conversation turns for the model (the others are display only). */
    public static boolean isConversation(int role){return role<=ROLE_SYSTEM;}
    public final long id;
    public final int role;
    public final String text;
    public final long timeMs;

    public ChatMessage(long id, int role, String text, long timeMs) {
        this.id=id;
        this.role=role;
        this.text=text;
        this.timeMs=timeMs;
    }
}
