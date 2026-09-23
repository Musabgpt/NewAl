package com.musab.aragpt2;

import android.content.Context;
import java.util.List;

/** Thin Java wrapper around the native llama.cpp JNI bridge. */
public final class LlamaEngine implements AutoCloseable {
    private static final char FIELD_SEP = '\u001F';
    private static final char RECORD_SEP = '\u001E';
    private static final char RESULT_SEP = '\u001D';

    static { System.loadLibrary("llama_bridge"); }
    private volatile long handle;

    public LlamaEngine(Context context, String modelPath, int contextTokens, int threads) {
        String nativeLibDir = context.getApplicationInfo().nativeLibraryDir;
        int effectiveThreads = ThreadingConfig.recommendedThreads(Runtime.getRuntime().availableProcessors());
        handle = nativeLoadModel(modelPath, nativeLibDir, contextTokens, effectiveThreads);
        if (handle == 0) throw new IllegalStateException("فشل تحميل النموذج");
    }

    public GenerationResult generate(List<ChatMessage> turns, int maxNewTokens,
                                    float temperature, int topK, boolean codeMode) {
        if (handle == 0) throw new IllegalStateException("المحرك مغلق");
        StringBuilder encoded = new StringBuilder();
        for (ChatMessage m : turns) {
            final String role = m.role == ChatMessage.ROLE_USER ? "user"
                    : m.role == ChatMessage.ROLE_SYSTEM ? "system" : "assistant";
            encoded.append(role).append(FIELD_SEP).append(m.text).append(RECORD_SEP);
        }
        return parseResult(nativeGenerate(handle, encoded.toString(), maxNewTokens,
                temperature, topK, codeMode));
    }

    private GenerationResult parseResult(String packed) {
        if (packed == null) return new GenerationResult("", 0, 0, -1, 0.0, 0);
        String[] p = packed.split(String.valueOf(RESULT_SEP), -1);
        if (p.length < 6) return new GenerationResult(packed, 0, 0, -1, 0.0, 0);
        try {
            return new GenerationResult(
                    p[0], Integer.parseInt(p[1]), Integer.parseInt(p[2]),
                    Long.parseLong(p[3]), Double.parseDouble(p[4]), Long.parseLong(p[5]));
        } catch (NumberFormatException e) {
            return new GenerationResult(p[0], 0, 0, -1, 0.0, 0);
        }
    }

    public void resetContext() { long h = handle; if (h != 0) nativeReset(h); }
    public void cancel() { long h = handle; if (h != 0) nativeCancel(h); }

    @Override public synchronized void close() {
        if (handle != 0) {
            nativeFree(handle);
            handle = 0;
        }
    }

    private native long nativeLoadModel(String modelPath, String nativeLibDir, int contextTokens, int threads);
    private native String nativeGenerate(long handle, String encodedTurns, int maxNewTokens, float temperature, int topK, boolean codeMode);
    private native void nativeCancel(long handle);
    private native void nativeReset(long handle);
    private native void nativeFree(long handle);
}
