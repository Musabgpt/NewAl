package com.musab.aragpt2;

import static org.junit.Assert.*;

import java.util.List;

import org.junit.Test;

public class MarkdownTest {
    @Test public void blocks() {
        List<Markdown.Block> b = Markdown.parse("# عنوان\n\nفقرة **مهمة** هنا\nسطر ثانٍ\n\n- أول\n- ثاني\n  تكملة\n1. واحد\n2) اثنين\n\n```python\nprint(1)\n\nx = 2\n```\n> اقتباس\n---\n| a | b |\n|---|:-:|\n| 1 | 2 |\n");
        assertEquals("[HEADING1:عنوان, PARAGRAPH:فقرة **مهمة** هنا\nسطر ثانٍ, BULLET:أول, BULLET:ثاني تكملة, NUMBERED#1:واحد, NUMBERED#2:اثنين, "
                + "CODE(python):print(1)\n\nx = 2, QUOTE:اقتباس, RULE:, TABLE[[a, b], [1, 2]]]", b.toString());
        assertFalse(b.get(6).open);
    }

    @Test public void streamingCodeBlockIsStillCode() {
        List<Markdown.Block> b = Markdown.parse("Here:\n```js\nconst a = 1;");
        assertEquals(Markdown.Type.CODE, b.get(1).type);
        assertTrue(b.get(1).open);
        assertEquals("const a = 1;", b.get(1).text);
    }

    @Test public void mathBlocks() {
        List<Markdown.Block> b = Markdown.parse("$$ 2340 \\times 0.175 = 409.5 $$");
        assertEquals("[CODE(math):2340 \\times 0.175 = 409.5]", b.toString());
    }

    @Test public void inlineStyles() {
        Markdown.Inline in = Markdown.inline("**bold** and *it* and `code` and [link](https://a.b) and ~~x~~ see [2] https://c.d/e");
        assertEquals("bold and it and code and link and x see [2] https://c.d/e", in.text);
        assertEquals("[BOLD[0,4), ITALIC[9,11), CODE[16,20), LINK[25,29)https://a.b, STRIKE[34,35), CITE[40,43)2, LINK[44,57)https://c.d/e]", in.spans.toString());
        assertEquals("2*3*4 stays", Markdown.inline("2*3*4 stays").text);
        Markdown.Inline nested = Markdown.inline("**a `b` c**");
        assertEquals("a b c", nested.text);
    }
}
