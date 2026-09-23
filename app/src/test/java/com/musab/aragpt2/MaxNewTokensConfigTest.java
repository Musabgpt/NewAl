package com.musab.aragpt2;

import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class MaxNewTokensConfigTest {
    @Test
    public void longCodeHasAtLeast1024TokenBudget() {
        assertTrue(MainActivity.MAX_NEW_TOKENS >= 1024);
    }
}
