package com.musab.aragpt2;

import java.util.List;

/**
 * Thin Java wrapper around the native llama.cpp JNI bridge (see
 * app/src/main/cpp/llama_bridge.cpp). One instance wraps exactly one loaded
 * GGUF model. All calls are blocking and are expected to run off the UI
 * thread (MainActivity does this via a single-thread executor).
 */
public final class LlamaEngine implements AutoCloseable {
    private static final char FIELD_SEP = '\u001F';
    private static final char RECORD_SEP = '\u001E';

    static { System.loadLibrary("llama_bridge"); }

    private volatile long handle;

    public LlamaEngine(String modelPath, int contextTokens, int threads) {
        handle = nativeLoadModel(modelPath, contextTokens, threads);
        if (handle == 0) throw new IllegalStateException("فشل تحميل النموذج");
    }

    public String generate(List<ChatMessage> turns, int maxNewTokens, float temperature, int topK) {
        if (handle == 0) throw new IllegalStateException("المحرك مغلق");
        StringBuilder encoded = new StringBuilder();
        for (ChatMessage m : turns) {
            encoded.append(m.role == ChatMessage.ROLE_USER ? "user" : "assistant")
                    .append(FIELD_SEP).append(m.text).append(RECORD_SEP);
        }
        String result = nativeGenerate(handle, encoded.toString(), maxNewTokens, temperature, topK);
        return result == null ? "" : result.trim();
    }

    public void cancel() { long h = handle; if (h != 0) nativeCancel(h); }

    @Override public synchronized void close() {
        if (handle != 0) { nativeFree(handle); handle = 0; }
    }

    private native long nativeLoadModel(String modelPath, int contextTokens, int threads);
    private native String nativeGenerate(long handle, String encodedTurns, int maxNewTokens, float temperature, int topK);
    private native void nativeCancel(long handle);
    private native void nativeFree(long handle);
}
