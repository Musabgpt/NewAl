package com.musab.aragpt2;

/** Immutable result returned by the native inference bridge. */
public final class GenerationResult {
    public final String text;
    public final int promptTokens;
    public final int generatedTokens;
    public final long firstTokenMs;
    public final double tokensPerSecond;
    public final long totalMs;

    public GenerationResult(String text, int promptTokens, int generatedTokens,
                            long firstTokenMs, double tokensPerSecond, long totalMs) {
        this.text = text == null ? "" : text;
        this.promptTokens = promptTokens;
        this.generatedTokens = generatedTokens;
        this.firstTokenMs = firstTokenMs;
        this.tokensPerSecond = tokensPerSecond;
        this.totalMs = totalMs;
    }
}
