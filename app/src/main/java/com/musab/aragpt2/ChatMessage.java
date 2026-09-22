package com.musab.aragpt2;

public final class ChatMessage {
    public static final int ROLE_USER=0, ROLE_ASSISTANT=1;
    public final long id; public final int role; public final String text; public final long timeMs;
    public ChatMessage(long id,int role,String text,long timeMs){this.id=id;this.role=role;this.text=text;this.timeMs=timeMs;}
}
