package com.musab.aragpt2;

import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.view.Window;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "h33_prefs";
    private static final String PREF_MODEL_URI = "model_uri";
    private static final String PREF_MODEL_NAME = "model_name";
    private static final String PREF_MODEL_LOCAL_PATH = "model_local_path";
    private static final String PREF_MODEL_SIZE = "model_local_size";

    private static final int CONTEXT_TOKENS = 2048;
    // Room for ~100-150 lines of code. The native side clamps this further if
    // the prompt is long, so it can never overflow the context.
    private static final int MAX_NEW_TOKENS = 768;
    private static final int TOP_K = 40;
    private static final float TEMPERATURE = 0.20f;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile LlamaEngine engine;
    private volatile boolean generating = false;

    private ChatHistoryStore historyStore;
    private MemoryManager memoryManager;
    private ChatAdapter adapter;
    private SharedPreferences prefs;

    private TextView status;
    private RecyclerView chatList;
    private EditText inputBox;
    private View rootView;
    private View headerView;
    private View inputBar;
    private Button loadModelButton;
    private Button sendButton;
    private Button clearButton;
    private int headerBasePaddingTop;
    private ActivityResultLauncher<String[]> pickModelLauncher;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        historyStore = new ChatHistoryStore(this);
        memoryManager = new MemoryManager(historyStore);

        rootView = findViewById(R.id.rootView);
        headerView = findViewById(R.id.headerView);
        inputBar = findViewById(R.id.inputBar);
        status = findViewById(R.id.status);
        chatList = findViewById(R.id.chatList);
        inputBox = findViewById(R.id.inputBox);
        loadModelButton = findViewById(R.id.loadModelButton);
        sendButton = findViewById(R.id.sendButton);
        clearButton = findViewById(R.id.clearButton);

        adapter = new ChatAdapter();
        chatList.setLayoutManager(new LinearLayoutManager(this));
        chatList.setAdapter(adapter);
        adapter.setAll(historyStore.loadAll());
        scrollToEnd();
        installKeyboardInsetsFix();

        pickModelLauncher = registerForActivityResult(
                new ActivityResultContracts.OpenDocument(), this::onModelPicked);
        loadModelButton.setOnClickListener(v -> pickModelLauncher.launch(new String[]{"*/*"}));
        sendButton.setOnClickListener(v -> onSendOrStopClicked());
        clearButton.setOnClickListener(v -> clearChat());
        restoreSavedModel();
    }

    private void installKeyboardInsetsFix() {
        Window window = getWindow();
        WindowCompat.setDecorFitsSystemWindows(window, false);
        headerBasePaddingTop = headerView.getPaddingTop();
        ViewCompat.setOnApplyWindowInsetsListener(rootView, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars());
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            headerView.setPadding(headerView.getPaddingLeft(), headerBasePaddingTop + bars.top,
                    headerView.getPaddingRight(), headerView.getPaddingBottom());
            int bottom = Math.max(bars.bottom, ime.bottom);
            android.widget.LinearLayout.LayoutParams lp =
                    (android.widget.LinearLayout.LayoutParams) inputBar.getLayoutParams();
            lp.bottomMargin = bottom;
            inputBar.setLayoutParams(lp);
            if (ime.bottom > 0) inputBar.post(this::scrollToEnd);
            return insets;
        });
        ViewCompat.requestApplyInsets(rootView);
    }

    private void restoreSavedModel() {
        String savedName = prefs.getString(PREF_MODEL_NAME, "النموذج");
        String localPath = prefs.getString(PREF_MODEL_LOCAL_PATH, null);
        if (localPath != null) {
            File local = new File(localPath);
            long expected = prefs.getLong(PREF_MODEL_SIZE, -1L);
            if (isValidGgufFile(local) && (expected <= 0 || local.length() == expected)) {
                loadModelFromLocalFile(local, savedName);
                return;
            }
        }
        String savedUri = prefs.getString(PREF_MODEL_URI, null);
        if (savedUri != null) prepareAndLoadFromUri(Uri.parse(savedUri), savedName);
        else setWorking(false, "اضغط «تحميل نموذج» واختر ملف GGUF من الهاتف");
    }

    private void onModelPicked(Uri uri) {
        if (uri == null) return;
        String name = queryDisplayName(uri);
        if (!name.toLowerCase(java.util.Locale.ROOT).endsWith(".gguf")) {
            setWorking(false, "الملف المختار ليس GGUF");
            return;
        }
        long size = querySize(uri);
        prefs.edit().putString(PREF_MODEL_URI, uri.toString()).putString(PREF_MODEL_NAME, name).apply();
        try {
            getContentResolver().takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException ignored) {}
        prepareAndLoadFromUri(uri, name, size);
    }

    private void prepareAndLoadFromUri(Uri uri, String displayName) {
        prepareAndLoadFromUri(uri, displayName, querySize(uri));
    }

    private void prepareAndLoadFromUri(Uri uri, String displayName, long expectedSize) {
        setWorking(true, "جاري تجهيز ملف GGUF: " + displayName);
        loadModelButton.setEnabled(false);
        executor.execute(() -> {
            try {
                File staged = stageModelToAppStorage(uri, expectedSize);
                runOnUiThread(() -> setWorking(true, "تم تجهيز النموذج — جاري فتحه: " + displayName));
                loadModelFromLocalFileInternal(staged, displayName, uri.toString(), expectedSize);
            } catch (Exception ex) {
                runOnUiThread(() -> {
                    loadModelButton.setEnabled(true);
                    setWorking(false, "تعذر تجهيز GGUF: " + safeMessage(ex));
                });
            }
        });
    }

    private File stageModelToAppStorage(Uri uri, long expectedSize) throws Exception {
        File dir = getExternalFilesDir("models");
        if (dir == null) throw new IllegalStateException("تخزين التطبيق غير متاح");
        if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("تعذر إنشاء مجلد النماذج");
        File tmp = new File(dir, "model.gguf.part");
        File target = new File(dir, "model.gguf");
        if (tmp.exists() && !tmp.delete()) throw new IllegalStateException("تعذر حذف ملف مؤقت قديم");
        try (InputStream raw = getContentResolver().openInputStream(uri)) {
            if (raw == null) throw new IllegalStateException("تعذر فتح ملف GGUF");
            try (BufferedInputStream in = new BufferedInputStream(raw, 1024 * 1024);
                 BufferedOutputStream out = new BufferedOutputStream(new FileOutputStream(tmp), 1024 * 1024)) {
                byte[] buffer = new byte[1024 * 1024];
                long copied = 0, lastUi = 0;
                int read;
                while ((read = in.read(buffer)) != -1) {
                    out.write(buffer, 0, read);
                    copied += read;
                    long now = System.currentTimeMillis();
                    if (now - lastUi >= 400) {
                        final long done = copied;
                        runOnUiThread(() -> {
                            if (expectedSize > 0) {
                                int pct = (int) Math.min(99L, (done * 100L) / expectedSize);
                                setWorking(true, "جاري تجهيز GGUF… " + pct + "%");
                            } else setWorking(true, "جاري تجهيز GGUF… " + (done / (1024 * 1024)) + " MB");
                        });
                        lastUi = now;
                    }
                }
            }
        }
        if (!isValidGgufFile(tmp)) { tmp.delete(); throw new IllegalStateException("ملف GGUF غير صالح أو تالف"); }
        if (target.exists() && !target.delete()) { tmp.delete(); throw new IllegalStateException("تعذر استبدال النموذج السابق"); }
        if (!tmp.renameTo(target)) { tmp.delete(); throw new IllegalStateException("تعذر حفظ نسخة النموذج داخل التطبيق"); }
        prefs.edit().putString(PREF_MODEL_LOCAL_PATH, target.getAbsolutePath())
                .putLong(PREF_MODEL_SIZE, target.length()).apply();
        return target;
    }

    private void loadModelFromLocalFile(File file, String displayName) {
        setWorking(true, "جاري فتح النموذج: " + displayName);
        loadModelButton.setEnabled(false);
        executor.execute(() -> loadModelFromLocalFileInternal(
                file, displayName, prefs.getString(PREF_MODEL_URI, ""), file.length()));
    }

    private void loadModelFromLocalFileInternal(File file, String displayName,
                                                 String sourceUri, long expectedSize) {
        try {
            if (!isValidGgufFile(file)) throw new IllegalStateException("ملف GGUF غير صالح أو تالف");
            if (engine != null) { try { engine.close(); } catch (Exception ignored) {} engine = null; }

            int threads = Math.max(2, Math.min(6, Runtime.getRuntime().availableProcessors()));
            LlamaEngine loaded = new LlamaEngine(file.getAbsolutePath(), CONTEXT_TOKENS, threads);
            engine = loaded;
            prefs.edit().putString(PREF_MODEL_LOCAL_PATH, file.getAbsolutePath())
                    .putString(PREF_MODEL_NAME, displayName).putString(PREF_MODEL_URI, sourceUri)
                    .putLong(PREF_MODEL_SIZE, file.length() > 0 ? file.length() : expectedSize).apply();
            runOnUiThread(() -> { loadModelButton.setEnabled(true); setWorking(false, "جاهز — " + displayName); });
        } catch (Exception ex) {
            runOnUiThread(() -> { loadModelButton.setEnabled(true); setWorking(false, "تعذر تحميل النموذج: " + safeMessage(ex)); });
        }
    }

    private long querySize(Uri uri) {
        try (android.database.Cursor c = getContentResolver().query(
                uri, new String[]{android.provider.OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(android.provider.OpenableColumns.SIZE);
                if (idx >= 0 && !c.isNull(idx)) return c.getLong(idx);
            }
        } catch (Exception ignored) {}
        return -1L;
    }

    private String queryDisplayName(Uri uri) {
        try (android.database.Cursor c = getContentResolver().query(uri, null, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) {
                    String name = c.getString(idx);
                    if (!TextUtils.isEmpty(name)) return name;
                }
            }
        } catch (Exception ignored) {}
        return "النموذج.gguf";
    }

    private boolean isValidGgufFile(File file) {
        if (file == null || !file.isFile() || file.length() < 4) return false;
        try (FileInputStream in = new FileInputStream(file)) {
            byte[] magic = new byte[4];
            int n = in.read(magic);
            return n == 4 && magic[0] == 'G' && magic[1] == 'G' && magic[2] == 'U' && magic[3] == 'F';
        } catch (Exception ignored) { return false; }
    }

    private void onSendOrStopClicked() {
        if (generating) { if (engine != null) engine.cancel(); return; }
        String question = inputBox.getText().toString().trim();
        if (question.isEmpty() || engine == null) return;
        inputBox.setText("");
        setGenerating(true);

        executor.execute(() -> {
            long userId = historyStore.append(ChatMessage.ROLE_USER, question);
            long userTime = System.currentTimeMillis();
            memoryManager.rememberExplicit(question);
            List<ChatMessage> turns = memoryManager.buildTurns(
                    historyStore.loadAll(), CONTEXT_TOKENS, MAX_NEW_TOKENS);
            runOnUiThread(() -> {
                adapter.add(new ChatMessage(userId, ChatMessage.ROLE_USER, question, userTime));
                scrollToEnd();
                setWorking(true, "يفكر…");
            });
            try {
                GenerationResult result = engine.generate(turns, MAX_NEW_TOKENS, TEMPERATURE, TOP_K, true);
                String finalAnswer = result.text.isEmpty() ? "…" : result.text;
                long assistantId = historyStore.append(ChatMessage.ROLE_ASSISTANT, finalAnswer);
                long assistantTime = System.currentTimeMillis();
                String metric = formatMetrics(result);
                runOnUiThread(() -> {
                    adapter.add(new ChatMessage(assistantId, ChatMessage.ROLE_ASSISTANT, finalAnswer, assistantTime));
                    scrollToEnd();
                    setGenerating(false);
                    setWorking(false, metric);
                });
            } catch (Exception ex) {
                runOnUiThread(() -> { setGenerating(false); setWorking(false, "خطأ أثناء التوليد: " + safeMessage(ex)); });
            }
        });
    }

    private String formatMetrics(GenerationResult r) {
        if (r.generatedTokens <= 0) return "جاهز";
        String first = r.firstTokenMs >= 0 ? r.firstTokenMs + "ms" : "—";
        return String.format(java.util.Locale.US, "جاهز • %d tok • %s أول token • %.1f tok/s",
                r.generatedTokens, first, r.tokensPerSecond);
    }

    private void scrollToEnd() {
        int count = adapter.getCount();
        if (count > 0) chatList.scrollToPosition(count - 1);
    }

    private void clearChat() {
        historyStore.clear();
        memoryManager.resetWindow();
        if (engine != null) engine.resetContext();
        adapter.setAll(java.util.Collections.emptyList());
        status.setText("تم مسح المحادثة والذاكرة");
    }

    private void setGenerating(boolean value) {
        generating = value;
        sendButton.setText(value ? "⏹" : "➤");
        sendButton.setEnabled(engine != null);
        loadModelButton.setEnabled(!value && !isWorkingStatus());
    }

    private boolean isWorkingStatus() {
        String s = status.getText() == null ? "" : status.getText().toString();
        return s.startsWith("جاري") || s.startsWith("تم تجهيز النموذج");
    }

    private void setWorking(boolean working, String message) {
        status.setText(message);
        if (!generating) sendButton.setEnabled(!working && engine != null);
    }

    private static String safeMessage(Exception ex) {
        String m = ex.getMessage();
        return TextUtils.isEmpty(m) ? ex.getClass().getSimpleName() : m;
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        executor.shutdownNow();
        if (engine != null) { try { engine.close(); } catch (Exception ignored) {} }
    }
}
