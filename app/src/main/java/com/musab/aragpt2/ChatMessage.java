package com.musab.aragpt2;

public final class ChatMessage {
    public static final int ROLE_USER=0, ROLE_ASSISTANT=1, ROLE_SYSTEM=2;
    /** Agent-mode bubbles: generated code, program/terminal output, and the task result card. */
    public static final int ROLE_CODE=3, ROLE_TERMINAL=4, ROLE_RESULT=5;
    /** A tool result sent back to the model (LFM-style templates have a "tool" role). */
    public static final int ROLE_TOOL=6;
    /** Roles that are conversation turns for the model (the others are display only). */
    public static boolean isConversation(int role){return role<=ROLE_SYSTEM;}
    public final long id;
    public final int role;
    public final String text;
    public final long timeMs;
    /** Display extras for an answer as JSON: thinking, tools used, web sources, speed ("" when none). */
    public final String meta;
    /** Still being written (shows the cursor, hides the action row). */
    public final boolean streaming;

    public ChatMessage(long id, int role, String text, long timeMs) {
        this(id,role,text,timeMs,"",false);
    }

    public ChatMessage(long id, int role, String text, long timeMs, String meta, boolean streaming) {
        this.id=id;
        this.role=role;
        this.text=text;
        this.timeMs=timeMs;
        this.meta=meta==null?"":meta;
        this.streaming=streaming;
    }
}
