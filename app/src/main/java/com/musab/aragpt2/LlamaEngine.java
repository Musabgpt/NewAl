package com.musab.aragpt2;

import android.content.Context;
import java.util.List;

/** Thin Java wrapper around the native llama.cpp JNI bridge. */
public final class LlamaEngine implements AutoCloseable {
    private static final char FIELD_SEP = '\u001F';
    private static final char RECORD_SEP = '\u001E';
    private static final char RESULT_SEP = '\u001D';
    static final int FLAG_CODE_MODE = 1, FLAG_RAW = 2, FLAG_THINK = 4;

    static { System.loadLibrary("llama_bridge"); }
    private volatile long handle;
    /** Small model with the same tokenizer that drafts tokens for this one (speculative decoding). */
    private volatile LlamaEngine draft;
    private volatile int draftTokens = 6;

    public LlamaEngine(Context context, String modelPath, int contextTokens, int threads) {
        String nativeLibDir = context.getApplicationInfo().nativeLibraryDir;
        int effectiveThreads = ThreadingConfig.recommendedThreads(Runtime.getRuntime().availableProcessors());
        handle = nativeLoadModel(modelPath, nativeLibDir, contextTokens, effectiveThreads);
        if (handle == 0) throw new IllegalStateException("فشل تحميل النموذج");
    }

    public GenerationResult generate(List<ChatMessage> turns, int maxNewTokens,
                                    float temperature, int topK, boolean codeMode) {
        return generate(turns, maxNewTokens, temperature, topK, codeMode ? FLAG_CODE_MODE : 0, null);
    }

    /**
     * Streams the reply to {@code listener} while it is generated and returns the full text
     * plus metrics. {@code maxNewTokens <= 0} generates until end of turn or a full context.
     */
    public GenerationResult generate(List<ChatMessage> turns, int maxNewTokens, float temperature,
                                    int topK, int flags, TextListener listener) {
        return generate(turns, maxNewTokens, temperature, topK, flags, null, listener);
    }

    /** As above; {@code grammar} (GBNF, root rule "root") constrains what the model may output. */
    public GenerationResult generate(List<ChatMessage> turns, int maxNewTokens, float temperature,
                                    int topK, int flags, String grammar, TextListener listener) {
        if (handle == 0) throw new IllegalStateException("المحرك مغلق");
        StringBuilder encoded = new StringBuilder();
        for (ChatMessage m : turns) {
            final String role = m.role == ChatMessage.ROLE_USER ? "user"
                    : m.role == ChatMessage.ROLE_SYSTEM ? "system" : "assistant";
            encoded.append(role).append(FIELD_SEP).append(m.text).append(RECORD_SEP);
        }
        StreamCollector collector = new StreamCollector(listener);
        LlamaEngine d = draft;
        long draftHandle = d == null ? 0 : d.handle;
        String packed = nativeGenerate(handle, encoded.toString(), maxNewTokens, temperature, topK, flags, grammar,
                draftHandle, draftTokens, collector);
        collector.finish();
        if (collector.error != null) throw new IllegalStateException(collector.error.getMessage(), collector.error);
        return parseResult(collector.text.toString(), packed);
    }

    /** Called from native code; decodes UTF-8 incrementally so split characters are never lost. */
    static final class StreamCollector {
        private final TextListener listener;
        private final Utf8StreamDecoder decoder = new Utf8StreamDecoder();
        final StringBuilder text = new StringBuilder();
        Exception error;

        StreamCollector(TextListener listener) { this.listener = listener; }

        @SuppressWarnings("unused") // JNI
        void onBytes(byte[] bytes) {
            deliver(decoder.decode(bytes, 0, bytes.length));
        }

        void finish() { deliver(decoder.flush()); }

        private void deliver(String s) {
            if (s.isEmpty()) return;
            text.append(s);
            if (listener == null || error != null) return;
            try {
                listener.onText(s);
            } catch (Exception e) {
                // Stop generation: the native loop checks for a pending Java exception.
                error = e;
                throw new RuntimeException(e);
            }
        }
    }

    private GenerationResult parseResult(String text, String packed) {
        if (packed == null) return new GenerationResult(text, 0, 0, -1, 0.0, 0);
        String[] p = packed.split(String.valueOf(RESULT_SEP), -1);
        // p[0] is empty: the text itself travels through the stream, not the result string.
        if (p.length < 6) return new GenerationResult(text, 0, 0, -1, 0.0, 0);
        try {
            return new GenerationResult(
                    text, Integer.parseInt(p[1]), Integer.parseInt(p[2]),
                    Long.parseLong(p[3]), Double.parseDouble(p[4]), Long.parseLong(p[5]),
                    p.length > 6 ? Integer.parseInt(p[6]) : GenerationResult.STOP_UNKNOWN,
                    p.length > 7 ? Integer.parseInt(p[7]) : 0,
                    p.length > 8 ? Integer.parseInt(p[8]) : 0,
                    p.length > 9 ? Integer.parseInt(p[9]) : 0);
        } catch (NumberFormatException e) {
            return new GenerationResult(text, 0, 0, -1, 0.0, 0);
        }
    }

    /**
     * Uses {@code d} (a small model with the same tokenizer, kept loaded at the same time) to
     * draft {@code tokens} tokens per step for greedy generation. Output is unchanged; only faster
     * when the draft guesses well. Pass null to turn it off.
     */
    public void setDraft(LlamaEngine d, int tokens) {
        draft = d == this ? null : d;
        draftTokens = Math.max(1, Math.min(16, tokens));
    }

    public LlamaEngine draft() { return draft; }

    /** Actual context window (the native side may fall back to a smaller one on low RAM). */
    public int contextTokens() { long h = handle; return h != 0 ? nativeContextSize(h) : 0; }

    /** The model's Jinja chat template ("" when the GGUF has none). */
    public String chatTemplate() { long h = handle; return h != 0 ? nativeChatTemplate(h) : ""; }

    /** Qwen3.5 / Qwen3-Coder templates call tools in XML; Qwen2.5 / Qwen3 in JSON. */
    public boolean xmlToolCalls() { return chatTemplate().contains("<function="); }

    public void resetContext() { long h = handle; if (h != 0) nativeReset(h); }
    public void cancel() { long h = handle; if (h != 0) nativeCancel(h); }

    @Override public synchronized void close() {
        if (handle != 0) {
            nativeFree(handle);
            handle = 0;
        }
    }

    private native long nativeLoadModel(String modelPath, String nativeLibDir, int contextTokens, int threads);
    private native String nativeGenerate(long handle, String encodedTurns, int maxNewTokens, float temperature,
                                         int topK, int flags, String grammar, long draftHandle, int draftTokens,
                                         StreamCollector sink);
    private native int nativeContextSize(long handle);
    private native String nativeChatTemplate(long handle);
    private native void nativeCancel(long handle);
    private native void nativeReset(long handle);
    private native void nativeFree(long handle);
}
