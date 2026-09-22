package com.musab.aragpt2;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.DocumentsContract;
import android.text.TextUtils;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "h33_prefs", PREF_MODEL_URI = "model_uri", PREF_MODEL_NAME = "model_name";
    private static final int CONTEXT_TOKENS = 2048, MAX_NEW_TOKENS = 256, TOP_K = 40;
    private static final float TEMPERATURE = 0.8f;
    private static final int PROMPT_CHAR_BUDGET = (CONTEXT_TOKENS - MAX_NEW_TOKENS - 64) * 2;
    private static final Set<String> LOCAL_STORAGE_AUTHORITIES = new HashSet<>(Arrays.asList(
            "com.android.externalstorage.documents", "com.android.providers.downloads.documents",
            "com.android.providers.media.documents", "com.android.providers.MediaDocumentsProvider"));

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile LlamaEngine engine;
    private volatile boolean generating = false;
    private ParcelFileDescriptor modelFd;
    private ChatHistoryStore historyStore;
    private ChatAdapter adapter;
    private SharedPreferences prefs;
    private TextView status;
    private RecyclerView chatList;
    private EditText inputBox;
    private Button loadModelButton, sendButton, clearButton;
    private ActivityResultLauncher<String[]> pickModelLauncher;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState); setContentView(R.layout.activity_main);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE); historyStore = new ChatHistoryStore(this);
        status = findViewById(R.id.status); chatList = findViewById(R.id.chatList); inputBox = findViewById(R.id.inputBox);
        loadModelButton = findViewById(R.id.loadModelButton); sendButton = findViewById(R.id.sendButton); clearButton = findViewById(R.id.clearButton);
        adapter = new ChatAdapter(); chatList.setLayoutManager(new LinearLayoutManager(this)); chatList.setAdapter(adapter);
        adapter.setAll(historyStore.loadAll()); scrollToEnd();
        pickModelLauncher = registerForActivityResult(new ActivityResultContracts.OpenDocument() {
            @NonNull @Override public Intent createIntent(@NonNull Context context, @NonNull String[] input) {
                Intent intent = super.createIntent(context, input);
                try { intent.putExtra(Intent.EXTRA_INITIAL_URI, DocumentsContract.buildDocumentUri("com.android.providers.downloads.documents", "downloads")); } catch (Exception ignored) {}
                return intent;
            }
        }, this::onModelPicked);
        loadModelButton.setOnClickListener(v -> pickModelLauncher.launch(new String[]{"*/*"}));
        sendButton.setOnClickListener(v -> onSendOrStopClicked()); clearButton.setOnClickListener(v -> clearChat());
        String savedUri = prefs.getString(PREF_MODEL_URI, null);
        if (savedUri != null) loadModelFromUri(Uri.parse(savedUri), prefs.getString(PREF_MODEL_NAME, "النموذج"));
        else setWorking(false, "اضغط «تحميل نموذج» واختر ملف GGUF من الهاتف");
    }

    private void onModelPicked(Uri uri) {
        if (uri == null) return;
        if (!isLocalStorageUri(uri)) { setWorking(false, "اختر ملف GGUF من تخزين الهاتف المحلي فقط"); return; }
        try { getContentResolver().takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION); } catch (SecurityException ignored) {}
        String name = queryDisplayName(uri);
        prefs.edit().putString(PREF_MODEL_URI, uri.toString()).putString(PREF_MODEL_NAME, name).apply();
        loadModelFromUri(uri, name);
    }

    private boolean isLocalStorageUri(Uri uri) { String a = uri.getAuthority(); return a != null && LOCAL_STORAGE_AUTHORITIES.contains(a); }

    private String queryDisplayName(Uri uri) {
        try (android.database.Cursor c = getContentResolver().query(uri, null, null, null, null)) {
            if (c != null && c.moveToFirst()) { int idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME); if (idx >= 0) { String n=c.getString(idx); if(n!=null&&!n.isEmpty()) return n; } }
        } catch (Exception ignored) {}
        return "النموذج";
    }

    private void loadModelFromUri(Uri uri, String displayName) {
        setWorking(true, "جاري فتح النموذج: " + displayName); loadModelButton.setEnabled(false);
        executor.execute(() -> {
            ParcelFileDescriptor pfd = null;
            try {
                pfd = getContentResolver().openFileDescriptor(uri, "r"); if (pfd == null) throw new IllegalStateException("تعذر فتح الملف المختار");
                String fdPath = "/proc/self/fd/" + pfd.getFd();
                if (engine != null) { engine.close(); engine = null; }
                if (modelFd != null) { try { modelFd.close(); } catch (Exception ignored) {} modelFd = null; }
                int threads = Math.max(2, Math.min(4, Runtime.getRuntime().availableProcessors()));
                LlamaEngine loaded = new LlamaEngine(fdPath, CONTEXT_TOKENS, threads);
                engine = loaded; modelFd = pfd;
                runOnUiThread(() -> { setWorking(false, "جاهز — " + displayName); loadModelButton.setEnabled(true); });
            } catch (Exception ex) {
                if (pfd != null) try { pfd.close(); } catch (Exception ignored) {}
                runOnUiThread(() -> { setWorking(false, "تعذر تحميل النموذج: " + safeMessage(ex)); loadModelButton.setEnabled(true); });
            }
        });
    }

    private void onSendOrStopClicked() {
        if (generating) { if (engine != null) engine.cancel(); return; }
        String question=inputBox.getText().toString().trim(); if(question.isEmpty()||engine==null)return; inputBox.setText(""); setGenerating(true);
        executor.execute(() -> {
            long userId=historyStore.append(ChatMessage.ROLE_USER,question); long userTime=System.currentTimeMillis();
            runOnUiThread(() -> { adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,question,userTime)); scrollToEnd(); setWorking(true,"يفكر…"); });
            try {
                String answer=engine.generate(buildTurnsWithinBudget(),MAX_NEW_TOKENS,TEMPERATURE,TOP_K); String finalAnswer=answer.isEmpty()?"…":answer;
                long assistantId=historyStore.append(ChatMessage.ROLE_ASSISTANT,finalAnswer); long assistantTime=System.currentTimeMillis();
                runOnUiThread(() -> { adapter.add(new ChatMessage(assistantId,ChatMessage.ROLE_ASSISTANT,finalAnswer,assistantTime)); scrollToEnd(); setGenerating(false); setWorking(false,"جاهز"); });
            } catch(Exception ex) { runOnUiThread(() -> { setGenerating(false); setWorking(false,"خطأ أثناء التوليد: "+safeMessage(ex)); }); }
        });
    }

    private List<ChatMessage> buildTurnsWithinBudget() {
        List<ChatMessage> history=historyStore.loadAll(), kept=new ArrayList<>(); int budget=PROMPT_CHAR_BUDGET;
        for(int i=history.size()-1;i>=0;i--){ChatMessage m=history.get(i);if(budget-m.text.length()<0)break;budget-=m.text.length();kept.add(0,m);} return kept;
    }
    private void scrollToEnd(){int count=adapter.getCount();if(count>0)chatList.scrollToPosition(count-1);}
    private void clearChat(){historyStore.clear();adapter.setAll(java.util.Collections.emptyList());status.setText("تم مسح المحادثة");}
    private void setGenerating(boolean value){generating=value;sendButton.setText(value?"⏹":"➤");sendButton.setEnabled(engine!=null);}
    private void setWorking(boolean working,String message){status.setText(message);if(!generating)sendButton.setEnabled(!working&&engine!=null);}
    private static String safeMessage(Exception ex){String m=ex.getMessage();return TextUtils.isEmpty(m)?ex.getClass().getSimpleName():m;}
    @Override protected void onDestroy(){super.onDestroy();executor.shutdownNow();if(engine!=null)try{engine.close();}catch(Exception ignored){}if(modelFd!=null)try{modelFd.close();}catch(Exception ignored){}}
}
