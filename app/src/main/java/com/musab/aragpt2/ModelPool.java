package com.musab.aragpt2;

import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Keeps several GGUF models loaded at the same time as long as RAM allows, so switching between
 * the manager, coder, language and draft models costs nothing. When a new model does not fit,
 * the least recently used models not needed right now are freed first.
 */
final class ModelPool<E extends AutoCloseable> {
    interface Loader<E> { E load(String path) throws Exception; }
    interface Memory { long availableBytes(); }
    interface EvictListener<E> { void onEvict(String path, E engine); }

    /** Memory kept free for Android and the rest of the app. */
    static final long RESERVE_BYTES = 600L * 1024 * 1024;

    private final Loader<E> loader;
    private final Memory memory;
    private final LinkedHashMap<String, E> loaded = new LinkedHashMap<>(8, 0.75f, true);
    private EvictListener<E> evictListener;

    ModelPool(Loader<E> loader, Memory memory) { this.loader = loader; this.memory = memory; }

    void setEvictListener(EvictListener<E> l) { evictListener = l; }

    /** RAM a model needs once loaded: weights (mapped) plus context, cache and compute buffers. */
    static long estimateBytes(String path) {
        return (long) (new File(path).length() * 1.15) + 220L * 1024 * 1024;
    }

    /**
     * Returns the loaded model for {@code path}, loading it if needed. {@code keep} lists models
     * that must stay loaded (for example the draft paired with this model).
     */
    synchronized E get(String path, Collection<String> keep) throws Exception {
        E e = loaded.get(path);
        if (e != null) return e;
        long need = estimateBytes(path);
        Iterator<Map.Entry<String, E>> it = loaded.entrySet().iterator();   // least recently used first
        while (memory.availableBytes() - need < RESERVE_BYTES && it.hasNext()) {
            Map.Entry<String, E> old = it.next();
            if (keep.contains(old.getKey())) continue;
            it.remove();
            if (evictListener != null) evictListener.onEvict(old.getKey(), old.getValue());
            try { old.getValue().close(); } catch (Exception ignored) {}
        }
        e = loader.load(path);
        loaded.put(path, e);
        return e;
    }

    synchronized boolean isLoaded(String path) { return loaded.containsKey(path); }
    synchronized List<String> loadedPaths() { return new ArrayList<>(loaded.keySet()); }
    synchronized List<E> engines() { return new ArrayList<>(loaded.values()); }

    synchronized void closeAll() {
        for (E e : loaded.values()) {
            try { e.close(); } catch (Exception ignored) {}
        }
        loaded.clear();
    }
}
