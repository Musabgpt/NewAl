package com.musab.aragpt2;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class ThreadingConfigTest {
    @Test
    public void recommendedThreadsUsesAllAvailableProcessors() {
        assertEquals(8, ThreadingConfig.recommendedThreads(8));
    }

    @Test
    public void recommendedThreadsNeverReturnsLessThanOne() {
        assertEquals(1, ThreadingConfig.recommendedThreads(0));
    }
}
