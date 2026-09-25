package com.musab.aragpt2;

/** Immutable result returned by the native inference bridge. */
public final class GenerationResult {
    // Why generation stopped (mirrors StopReason in llama_bridge_v2.cpp).
    public static final int STOP_EOG = 0, STOP_MAX_TOKENS = 1, STOP_CONTEXT_FULL = 2, STOP_CANCELLED = 3,
            STOP_STRING = 4, STOP_DECODE_ERROR = 5, STOP_CALLBACK = 6, STOP_UNKNOWN = -1;

    public final String text;
    public final int promptTokens;
    public final int generatedTokens;
    public final long firstTokenMs;
    public final double tokensPerSecond;
    public final long totalMs;
    public final int stopReason;
    public final int reusedPromptTokens;

    public GenerationResult(String text, int promptTokens, int generatedTokens,
                            long firstTokenMs, double tokensPerSecond, long totalMs) {
        this(text, promptTokens, generatedTokens, firstTokenMs, tokensPerSecond, totalMs, STOP_UNKNOWN, 0);
    }

    public GenerationResult(String text, int promptTokens, int generatedTokens, long firstTokenMs,
                            double tokensPerSecond, long totalMs, int stopReason, int reusedPromptTokens) {
        this.text = text == null ? "" : text;
        this.promptTokens = promptTokens;
        this.generatedTokens = generatedTokens;
        this.firstTokenMs = firstTokenMs;
        this.tokensPerSecond = tokensPerSecond;
        this.totalMs = totalMs;
        this.stopReason = stopReason;
        this.reusedPromptTokens = reusedPromptTokens;
    }

    /** True when the model finished its answer rather than being cut off. */
    public boolean finishedNaturally() { return stopReason == STOP_EOG || stopReason == STOP_STRING; }
}
