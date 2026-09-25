package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.List;
import org.junit.Test;

public class CodeHighlighterTest {
    private static String kinds(String code) {
        StringBuilder b = new StringBuilder();
        for (CodeHighlighter.Span s : CodeHighlighter.highlight(code)) {
            b.append(s.type).append(':').append(code, s.start, s.end).append('|');
        }
        return b.toString();
    }

    @Test public void hashInsideStringIsNotAComment() {
        String k = kinds("x = '# not a comment'  # real comment");
        assertTrue(k, k.contains("1:'# not a comment'|"));
        assertTrue(k, k.contains("0:# real comment|"));
    }

    @Test public void keywordsFunctionsNumbersAndHeaders() {
        String k = kinds("FILE: main.py\ndef add(a, b):\n    return a + 42\n");
        assertTrue(k, k.contains("4:FILE: main.py|"));
        assertTrue(k, k.contains("2:def|"));
        assertTrue(k, k.contains("5:add|"));
        assertTrue(k, k.contains("2:return|"));
        assertTrue(k, k.contains("3:42|"));
    }

    @Test public void spansNeverOverlap() {
        String code = "print(\"if 1 then\")  # if 2\nwhile True: pass";
        List<CodeHighlighter.Span> spans = CodeHighlighter.highlight(code);
        boolean[] seen = new boolean[code.length()];
        for (CodeHighlighter.Span s : spans) {
            for (int i = s.start; i < s.end; i++) {
                assertTrue("overlap at " + i, !seen[i]);
                seen[i] = true;
            }
        }
        assertEquals(1, spans.stream().filter(s -> s.type == CodeHighlighter.COMMENT).count());
    }
}
