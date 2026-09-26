package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class ModelPoolTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    static final class FakeEngine implements AutoCloseable {
        final String path;
        boolean closed;
        FakeEngine(String path) { this.path = path; }
        @Override public void close() { closed = true; }
    }

    private String model(String name, long mb) throws Exception {
        File f = tmp.newFile(name);
        try (RandomAccessFile r = new RandomAccessFile(f, "rw")) { r.setLength(mb * 1024 * 1024); }
        return f.getPath();
    }

    @Test public void keepsModelsTogetherWhileRamAllowsThenEvictsLeastRecentlyUsed() throws Exception {
        String manager = model("manager.gguf", 500), coder = model("coder.gguf", 1000), lang = model("lang.gguf", 1000);
        long[] free = {6L * 1024 * 1024 * 1024};
        List<String> evicted = new ArrayList<>();
        ModelPool<FakeEngine> pool = new ModelPool<>(p -> {
            free[0] -= ModelPool.estimateBytes(p);
            return new FakeEngine(p);
        }, () -> free[0]);
        pool.setEvictListener((p, e) -> { evicted.add(new File(p).getName()); free[0] += ModelPool.estimateBytes(p); });

        FakeEngine m = pool.get(manager, Collections.emptyList());
        FakeEngine c = pool.get(coder, Collections.emptyList());
        FakeEngine l = pool.get(lang, Collections.emptyList());
        assertEquals(3, pool.loadedPaths().size());          // all three resident together
        assertSame(m, pool.get(manager, Collections.emptyList())); // no reload

        // Little RAM left: loading another model frees only as much as needed, least recently
        // used first (coder), never a model in `keep`, and leaves the rest loaded.
        free[0] = ModelPool.RESERVE_BYTES + 300L * 1024 * 1024;
        String big = model("big.gguf", 1200);
        pool.get(big, Collections.singletonList(manager));
        assertEquals(List.of("coder.gguf"), evicted);
        assertTrue(c.closed);
        assertFalse(m.closed || l.closed);
        assertTrue(pool.isLoaded(manager) && pool.isLoaded(lang) && pool.isLoaded(big));

        // Even less RAM: the next load evicts lang but keeps the pinned manager.
        free[0] = ModelPool.RESERVE_BYTES;
        pool.get(coder, Collections.singletonList(manager));
        assertTrue(evicted.contains("lang.gguf"));
        assertFalse(m.closed);
        pool.closeAll();
        assertTrue(m.closed);
    }
}
