# experiment/memory-context-fix

Branched from `engine-v2-memory`.

## What changed
- **KV-cache reuse fixed** (`llama_bridge.cpp`): the engine now tracks every token in the KV cache
  (prompt + generated) and matches the next prompt against it. Before, reuse broke from the 3rd
  message on and the whole context was reprocessed every time.
- **Stable prompt prefix** (`MemoryManager.java`): the context window jumps in large steps instead of
  sliding one message per turn, and the summary is rebuilt only at those jumps.
- **Chunked prompt decode**: prompts longer than `n_batch` (512) no longer fail.
- **Context guard**: oldest turns are dropped and `max_new_tokens` is clamped so prompt + answer always
  fit `n_ctx`; generation stops before overflow (overflow was a source of garbage output).
- **Double BOS removed** when the chat template already emits one.
- **Mild repetition penalty** (1.05 code / 1.10 chat) against greedy loops.
- **UTF-8 safe output**: native side returns bytes, Java decodes them.
- `MAX_NEW_TOKENS` 256 -> 768.
- Fixed a compile error on the base branch (`buildTurns` signature mismatch).

## Build
The workflow file could not be edited from the connector, so run the build manually:
Actions -> "Build H33 GGUF Chat APK" -> Run workflow -> branch `experiment/memory-context-fix`.

## Test on the phone
1. Long chat (10+ messages): speed should stay flat; logcat `reused=` should be close to `prompt=`.
2. Paste a long code block: no "failed to run model" error.
3. Ask for a long script: answer should finish without degrading into repeated or random text.

## Tuning
- Code breaks from the penalty -> set it to 1.0 in `llama_bridge.cpp`.
- Enough RAM -> `CONTEXT_TOKENS = 3072`, `MAX_NEW_TOKENS = 1024` in `MainActivity.java`.
