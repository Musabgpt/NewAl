package com.musab.aragpt2;

/** CPU threading policy for the local GGUF runtime. */
final class ThreadingConfig {
    private ThreadingConfig() {}

    static int recommendedThreads(int availableProcessors) {
        return Math.max(1, availableProcessors);
    }
}
