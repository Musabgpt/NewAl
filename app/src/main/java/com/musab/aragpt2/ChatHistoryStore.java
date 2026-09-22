package com.musab.aragpt2;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.ArrayList;
import java.util.List;

public final class ChatHistoryStore extends SQLiteOpenHelper {
    private static final String DB_NAME="chat_history.db"; private static final int DB_VERSION=1; private static final String TABLE="messages";
    public ChatHistoryStore(Context context){super(context.getApplicationContext(),DB_NAME,null,DB_VERSION);}
    @Override public void onCreate(SQLiteDatabase db){db.execSQL("CREATE TABLE "+TABLE+" (id INTEGER PRIMARY KEY AUTOINCREMENT, role INTEGER NOT NULL, text TEXT NOT NULL, time_ms INTEGER NOT NULL)");}
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion){db.execSQL("DROP TABLE IF EXISTS "+TABLE);onCreate(db);}
    public synchronized long append(int role,String text){ContentValues v=new ContentValues();v.put("role",role);v.put("text",text);v.put("time_ms",System.currentTimeMillis());return getWritableDatabase().insert(TABLE,null,v);}
    public synchronized List<ChatMessage> loadAll(){List<ChatMessage> out=new ArrayList<>();try(Cursor c=getReadableDatabase().query(TABLE,null,null,null,null,null,"id ASC")){int i=c.getColumnIndexOrThrow("id"),r=c.getColumnIndexOrThrow("role"),t=c.getColumnIndexOrThrow("text"),tm=c.getColumnIndexOrThrow("time_ms");while(c.moveToNext())out.add(new ChatMessage(c.getLong(i),c.getInt(r),c.getString(t),c.getLong(tm)));}return out;}
    public synchronized void clear(){getWritableDatabase().delete(TABLE,null,null);}
}
