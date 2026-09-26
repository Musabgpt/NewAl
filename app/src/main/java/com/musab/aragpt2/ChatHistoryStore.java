package com.musab.aragpt2;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class ChatHistoryStore extends SQLiteOpenHelper {
    private static final String DB_NAME = "chat_history.db";
    private static final int DB_VERSION = 3;
    private static final String TABLE = "messages";

    /** A saved chat in the side list. */
    public static final class Conversation {
        public final long id;
        public final String title;
        public final long updatedMs;
        Conversation(long id, String title, long updatedMs) { this.id = id; this.title = title; this.updatedMs = updatedMs; }
    }

    /** The open conversation; messages are appended to and loaded from it. */
    private long current = -1;

    public ChatHistoryStore(Context context) {
        super(context.getApplicationContext(), DB_NAME, null, DB_VERSION);
    }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE " + TABLE +
                " (id INTEGER PRIMARY KEY AUTOINCREMENT, role INTEGER NOT NULL, " +
                "text TEXT NOT NULL, time_ms INTEGER NOT NULL, conv INTEGER NOT NULL DEFAULT 1, meta TEXT NOT NULL DEFAULT '')");
        createMemoryTables(db);
        createConversations(db);
    }

    private void createMemoryTables(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS memory_facts " +
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, fact_key TEXT NOT NULL UNIQUE, " +
                "fact_value TEXT NOT NULL, time_ms INTEGER NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS conversation_meta " +
                "(id INTEGER PRIMARY KEY CHECK(id=1), summary TEXT NOT NULL DEFAULT '')");
        db.execSQL("INSERT OR IGNORE INTO conversation_meta(id, summary) VALUES(1, '')");
    }

    private void createConversations(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                + "title TEXT NOT NULL DEFAULT '', updated_ms INTEGER NOT NULL)");
    }

    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) createMemoryTables(db);
        if (oldVersion < 3) {
            // The single old chat becomes conversation 1.
            db.execSQL("ALTER TABLE " + TABLE + " ADD COLUMN conv INTEGER NOT NULL DEFAULT 1");
            db.execSQL("ALTER TABLE " + TABLE + " ADD COLUMN meta TEXT NOT NULL DEFAULT ''");
            createConversations(db);
            db.execSQL("INSERT INTO conversations(id, title, updated_ms) SELECT 1, "
                    + "COALESCE((SELECT substr(text, 1, 60) FROM " + TABLE + " WHERE role=0 ORDER BY id LIMIT 1), ''), "
                    + "COALESCE((SELECT MAX(time_ms) FROM " + TABLE + "), 0) WHERE EXISTS (SELECT 1 FROM " + TABLE + ")");
        }
    }

    // ------------------------------------------------------------------ conversations

    /** The open conversation, creating the first one if there is none. */
    public synchronized long current() {
        if (current > 0) return current;
        try (Cursor c = getReadableDatabase().rawQuery("SELECT id FROM conversations ORDER BY updated_ms DESC LIMIT 1", null)) {
            current = c.moveToFirst() ? c.getLong(0) : newConversation();
        }
        return current;
    }

    /** Starts an empty conversation (reuses the open one when it has no messages yet). */
    public synchronized long newConversation() {
        if (current > 0 && count(current) == 0) return current;
        ContentValues v = new ContentValues();
        v.put("title", "");
        v.put("updated_ms", System.currentTimeMillis());
        current = getWritableDatabase().insert("conversations", null, v);
        setSummary("");
        return current;
    }

    public synchronized void open(long id) {
        current = id;
        setSummary("");
    }

    public synchronized List<Conversation> conversations() {
        List<Conversation> out = new ArrayList<>();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT c.id, c.title, c.updated_ms FROM conversations c "
                + "WHERE EXISTS (SELECT 1 FROM " + TABLE + " m WHERE m.conv=c.id) ORDER BY c.updated_ms DESC", null)) {
            while (c.moveToNext()) out.add(new Conversation(c.getLong(0), c.getString(1), c.getLong(2)));
        }
        return out;
    }

    public synchronized void rename(long id, String title) {
        ContentValues v = new ContentValues();
        v.put("title", title);
        getWritableDatabase().update("conversations", v, "id=?", new String[]{Long.toString(id)});
    }

    public synchronized void deleteConversation(long id) {
        SQLiteDatabase db = getWritableDatabase();
        db.delete(TABLE, "conv=?", new String[]{Long.toString(id)});
        db.delete("conversations", "id=?", new String[]{Long.toString(id)});
        if (current == id) current = -1;
    }

    /** Removes this message and everything after it in the open conversation (edit / regenerate). */
    public synchronized void deleteFrom(long messageId) {
        getWritableDatabase().delete(TABLE, "conv=? AND id>=?", new String[]{Long.toString(current()), Long.toString(messageId)});
    }

    private long count(long conv) {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM " + TABLE + " WHERE conv=?", new String[]{Long.toString(conv)})) {
            return c.moveToFirst() ? c.getLong(0) : 0;
        }
    }

    public synchronized long append(int role, String text) { return append(role, text, ""); }

    public synchronized long append(int role, String text, String meta) {
        long conv = current();
        long now = System.currentTimeMillis();
        ContentValues v = new ContentValues();
        v.put("role", role);
        v.put("text", text);
        v.put("time_ms", now);
        v.put("conv", conv);
        v.put("meta", meta == null ? "" : meta);
        long id = getWritableDatabase().insert(TABLE, null, v);
        ContentValues c = new ContentValues();
        c.put("updated_ms", now);
        getWritableDatabase().update("conversations", c, "id=?", new String[]{Long.toString(conv)});
        if (role == ChatMessage.ROLE_USER) {
            // The first question names the conversation.
            getWritableDatabase().execSQL("UPDATE conversations SET title=? WHERE id=? AND title=''",
                    new Object[]{text.replaceAll("\\s+", " ").trim().length() > 60 ? text.replaceAll("\\s+", " ").trim().substring(0, 60) + "…" : text.replaceAll("\\s+", " ").trim(), conv});
        }
        return id;
    }

    public synchronized List<ChatMessage> loadAll() {
        List<ChatMessage> out = new ArrayList<>();
        try (Cursor c = getReadableDatabase().query(TABLE, null, "conv=?", new String[]{Long.toString(current())}, null, null, "id ASC")) {
            int i = c.getColumnIndexOrThrow("id");
            int r = c.getColumnIndexOrThrow("role");
            int t = c.getColumnIndexOrThrow("text");
            int tm = c.getColumnIndexOrThrow("time_ms");
            int mt = c.getColumnIndexOrThrow("meta");
            while (c.moveToNext()) out.add(new ChatMessage(
                    c.getLong(i), c.getInt(r), c.getString(t), c.getLong(tm), c.getString(mt), false));
        }
        return out;
    }

    public synchronized void saveFact(String key, String value) {
        if (key == null || value == null || key.trim().isEmpty() || value.trim().isEmpty()) return;
        ContentValues v = new ContentValues();
        v.put("fact_key", key.trim());
        v.put("fact_value", value.trim());
        v.put("time_ms", System.currentTimeMillis());
        getWritableDatabase().insertWithOnConflict(
                "memory_facts", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    public synchronized Map<String, String> loadFacts() {
        Map<String, String> out = new LinkedHashMap<>();
        try (Cursor c = getReadableDatabase().query(
                "memory_facts", new String[]{"fact_key", "fact_value"},
                null, null, null, null, "time_ms ASC")) {
            while (c.moveToNext()) out.put(c.getString(0), c.getString(1));
        }
        return out;
    }

    public synchronized String getSummary() {
        try (Cursor c = getReadableDatabase().query(
                "conversation_meta", new String[]{"summary"}, "id=1",
                null, null, null, null)) {
            return c.moveToFirst() ? c.getString(0) : "";
        }
    }

    public synchronized void setSummary(String summary) {
        ContentValues v = new ContentValues();
        v.put("id", 1);
        v.put("summary", summary == null ? "" : summary);
        getWritableDatabase().insertWithOnConflict(
                "conversation_meta", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    /** Deletes every conversation and remembered fact. */
    public synchronized void clear() {
        SQLiteDatabase db = getWritableDatabase();
        db.delete(TABLE, null, null);
        db.delete("conversations", null, null);
        current = -1;
        db.delete("memory_facts", null, null);
        ContentValues v = new ContentValues();
        v.put("id", 1);
        v.put("summary", "");
        db.insertWithOnConflict("conversation_meta", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }
}
