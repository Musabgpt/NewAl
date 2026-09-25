package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import org.junit.Test;

public class CodeStreamParserTest {
    /** Records what the parser would write. */
    static final class Recorder implements CodeStreamParser.Sink {
        final Map<String, StringBuilder> files = new LinkedHashMap<>();
        final Map<String, Boolean> complete = new LinkedHashMap<>();
        final List<String> edits = new ArrayList<>();
        final List<String> runs = new ArrayList<>();
        String current;

        @Override public void onFileStart(String path) { current = path; files.put(path, new StringBuilder()); }
        @Override public void onFileData(String text) { files.get(current).append(text); }
        @Override public void onFileEnd(boolean ok) { complete.put(current, ok); current = null; }
        @Override public void onEdit(String path, String body, boolean ok) { edits.add(path + (ok ? "" : "!") + "|" + body); }
        @Override public void onRunCommand(String command) { runs.add(command); }
    }

    static Recorder parse(String text, int[] cuts) throws Exception {
        Recorder r = new Recorder();
        CodeStreamParser p = new CodeStreamParser(r, lang -> lang.equals("python") ? "main.py" : null);
        int prev = 0;
        for (int cut : cuts) { p.feed(text.substring(prev, cut)); prev = cut; }
        p.feed(text.substring(prev));
        p.finish();
        return r;
    }

    static Recorder parse(String text) throws Exception { return parse(text, new int[0]); }

    static final String TWO_FILES =
            "Here is the project.\n"
            + "FILE: main.py\n```python\nimport util\nprint(util.twice(21))\n```\n"
            + "Some prose that must not reach a file.\n"
            + "### FILE: `util.py`\n```python\ndef twice(x):\n    return x * 2\n```\n"
            + "RUN: python main.py\n";

    @Test public void writesExactFileContentWithoutFencesOrProse() throws Exception {
        Recorder r = parse(TWO_FILES);
        assertEquals("import util\nprint(util.twice(21))\n", r.files.get("main.py").toString());
        assertEquals("def twice(x):\n    return x * 2\n", r.files.get("util.py").toString());
        assertTrue(r.complete.get("main.py"));
        assertTrue(r.complete.get("util.py"));
        assertEquals(List.of("python main.py"), r.runs);
    }

    @Test public void anyChunkingGivesIdenticalFiles() throws Exception {
        Recorder whole = parse(TWO_FILES);
        Random rnd = new Random(42);
        for (int round = 0; round < 500; round++) {
            int n = 1 + rnd.nextInt(30);
            int[] cuts = rnd.ints(n, 0, TWO_FILES.length()).sorted().toArray();
            Recorder r = parse(TWO_FILES, cuts);
            assertEquals(whole.files.toString(), r.files.toString());
            assertEquals(whole.runs, r.runs);
        }
        // One character at a time.
        int[] every = new int[TWO_FILES.length() - 1];
        for (int i = 0; i < every.length; i++) every[i] = i + 1;
        assertEquals(whole.files.toString(), parse(TWO_FILES, every).files.toString());
    }

    @Test public void streamsContentBeforeTheLineEnds() throws Exception {
        Recorder r = new Recorder();
        CodeStreamParser p = new CodeStreamParser(r, null);
        p.feed("FILE: a.py\n```python\nfirst\nprint(\"he");
        assertEquals("first\nprint(\"he", r.files.get("a.py").toString());
        p.feed("llo\")\n``");
        // A line that may be the closing fence is held back, nothing else is.
        assertEquals("first\nprint(\"hello\")\n", r.files.get("a.py").toString());
        p.feed("`\n");
        assertTrue(r.complete.get("a.py"));
    }

    @Test public void backticksInsideCodeAreKept() throws Exception {
        Recorder r = parse("FILE: s.sh\n```bash\necho `date`\n``x\n```\n");
        assertEquals("echo `date`\n``x\n", r.files.get("s.sh").toString());
    }

    @Test public void longerFenceAllowsInnerFences() throws Exception {
        Recorder r = parse("FILE: README.md\n````markdown\n# T\n```python\nx=1\n```\n````\n");
        assertEquals("# T\n```python\nx=1\n```\n", r.files.get("README.md").toString());
    }

    @Test public void pathInFenceInfoIsAccepted() throws Exception {
        Recorder r = parse("```python app/server.py\nx = 1\n```\n");
        assertEquals("x = 1\n", r.files.get("app/server.py").toString());
    }

    @Test public void unterminatedBlockIsReportedIncomplete() throws Exception {
        Recorder r = parse("FILE: a.py\n```python\nx = 1\ny = ");
        assertEquals("x = 1\ny = ", r.files.get("a.py").toString());
        assertFalse(r.complete.get("a.py"));
    }

    @Test public void fencedEditBlock() throws Exception {
        String edit = "EDIT: main.py\n```python\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n```\n";
        Recorder r = parse(edit);
        assertTrue(r.files.isEmpty());
        assertEquals(List.of("main.py|<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"), r.edits);
    }

    @Test public void rawEditBlockWithoutFence() throws Exception {
        Recorder r = parse("EDIT: main.py\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\nDone.\n");
        assertEquals(List.of("main.py|<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n"), r.edits);
    }

    @Test public void fileBlockStartingWithSearchIsAnEditAndNeverTruncatesTheFile() throws Exception {
        Recorder r = parse("FILE: main.py\n```python\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n```\n");
        assertTrue("file must not be opened", r.files.isEmpty());
        assertEquals(1, r.edits.size());
    }

    @Test public void unnamedBlocks() throws Exception {
        Recorder r = parse("```python\nprint(1)\n```\nRun it with:\n```bash\npython main.py\n```\n");
        assertEquals("print(1)\n", r.files.get("main.py").toString());
        assertEquals(1, r.files.size());
    }

    /** Regression: models end with "```" and then the end-of-turn token, with no newline. */
    @Test public void closingFenceAtEndOfStreamWithoutNewline() throws Exception {
        for (boolean natural : new boolean[]{true, false}) {
            Recorder r = new Recorder();
            CodeStreamParser p = new CodeStreamParser(r, null);
            p.feed("FILE: calc.py\n```python\nprint(1 + 1)\n```");
            p.finish(natural);
            assertEquals("print(1 + 1)\n", r.files.get("calc.py").toString());
            assertTrue(r.complete.get("calc.py"));
        }
    }

    @Test public void unclosedLastBlockIsCompleteOnlyWhenTheModelEndedItself() throws Exception {
        Recorder r = new Recorder();
        CodeStreamParser p = new CodeStreamParser(r, null);
        p.feed("FILE: a.py\n```python\nx = 1\n");
        p.finish(true);
        assertEquals("x = 1\n", r.files.get("a.py").toString());
        assertTrue(r.complete.get("a.py"));
    }

    @Test public void cutOffBeforeFirstLineDoesNotOpenFile() throws Exception {
        Recorder r = parse("FILE: a.py\n```python\n");
        assertTrue(r.files.isEmpty());
    }
}
