package com.musab.aragpt2;

import static org.junit.Assert.assertFalse;

import org.junit.Test;

public class GenerationModeTest {
    @Test
    public void normalChatDoesNotEnableProgrammingMode() {
        assertFalse(GenerationMode.DEFAULT_CODE_MODE);
    }
}
