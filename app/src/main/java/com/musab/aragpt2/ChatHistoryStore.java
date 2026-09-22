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
    private static final int DB_VERSION = 2;
    private static final String TABLE = "messages";

    public ChatHistoryStore(Context context) {
        super(context.getApplicationContext(), DB_NAME, null, DB_VERSION);
    }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE " + TABLE +
                " (id INTEGER PRIMARY KEY AUTOINCREMENT, role INTEGER NOT NULL, " +
                "text TEXT NOT NULL, time_ms INTEGER NOT NULL)");
        createMemoryTables(db);
    }

    private void createMemoryTables(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS memory_facts " +
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, fact_key TEXT NOT NULL UNIQUE, " +
                "fact_value TEXT NOT NULL, time_ms INTEGER NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS conversation_meta " +
                "(id INTEGER PRIMARY KEY CHECK(id=1), summary TEXT NOT NULL DEFAULT '')");
        db.execSQL("INSERT OR IGNORE INTO conversation_meta(id, summary) VALUES(1, '')");
    }

    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) createMemoryTables(db);
    }

    public synchronized long append(int role, String text) {
        ContentValues v = new ContentValues();
        v.put("role", role);
        v.put("text", text);
        v.put("time_ms", System.currentTimeMillis());
        return getWritableDatabase().insert(TABLE, null, v);
    }

    public synchronized List<ChatMessage> loadAll() {
        List<ChatMessage> out = new ArrayList<>();
        try (Cursor c = getReadableDatabase().query(TABLE, null, null, null, null, null, "id ASC")) {
            int i = c.getColumnIndexOrThrow("id");
            int r = c.getColumnIndexOrThrow("role");
            int t = c.getColumnIndexOrThrow("text");
            int tm = c.getColumnIndexOrThrow("time_ms");
            while (c.moveToNext()) out.add(new ChatMessage(
                    c.getLong(i), c.getInt(r), c.getString(t), c.getLong(tm)));
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

    public synchronized void clear() {
        SQLiteDatabase db = getWritableDatabase();
        db.delete(TABLE, null, null);
        db.delete("memory_facts", null, null);
        ContentValues v = new ContentValues();
        v.put("id", 1);
        v.put("summary", "");
        db.insertWithOnConflict("conversation_meta", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }
}
