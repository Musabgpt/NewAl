# Local Coder V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the legacy llama.cpp bridge with a current ARM64-optimized runtime while adding durable memory and measurable code-assistant behavior.

**Architecture:** Keep the existing Android UI and GGUF staging. Replace only the native runtime integration first, then harden persistence and context assembly. Keep Python execution isolated as a later tool layer so inference remains stable and offline.

**Tech Stack:** Android Java, NDK 27.2, CMake, llama.cpp b11058, GGML ARM/KleidiAI/OpenMP, SQLiteOpenHelper for the current lightweight local store.

**Spec:** `docs/superpowers/specs/2026-09-22-local-coder-v2.md`

## Global Constraints

- Runtime is offline after the model is present.
- ABI is `arm64-v8a`.
- llama.cpp baseline is pinned to commit `f072b103714dfa1eee531f80b24512faf38e3dd2` (`b11058`).
- CPU baseline uses KleidiAI + OpenMP; GPU backends remain off until benchmark evidence exists.
- No destructive database migration.

## Review Focus

- Old GGUF chat-template API calls must not survive the runtime upgrade — native compile test.
- A second message must reuse the live context rather than clearing KV state — integration behavior test.
- Existing v1 history must survive upgrade — database migration test.
- Empty/invalid model selection must remain safe — existing Android UI path test.
- Cancellation/close must not leak native resources — native lifecycle review.

### Task 1: Runtime and build foundation

**Files:**
- Modify: `app/src/main/cpp/CMakeLists.txt`
- Modify: `app/src/main/cpp/llama_bridge.cpp`

- [ ] Pin llama.cpp to b11058 and enable ARM/KleidiAI/OpenMP for arm64-v8a.
- [ ] Replace custom sampler with native sampler chain APIs.
- [ ] Use the model's native chat template API and current tokenizer/model APIs.
- [ ] Keep one context alive for multiple turns and reset only on explicit conversation reset.
- [ ] Add native timing counters and return them to Java through a structured result.
- [ ] Verify the native code has no v0.2.x-only API calls.

### Task 2: Durable conversation and memory

**Files:**
- Modify: `app/src/main/java/com/musab/aragpt2/ChatHistoryStore.java`
- Create: `app/src/main/java/com/musab/aragpt2/MemoryManager.java`
- Modify: `app/src/main/java/com/musab/aragpt2/MainActivity.java`

- [ ] Migrate DB version 1 to version 2 without dropping messages.
- [ ] Add a local summary record and memory-fact table.
- [ ] Build context from recent turns plus relevant local memories instead of a raw character cut.
- [ ] Preserve all messages after process restart.
- [ ] Make Clear Chat clear both visible conversation and derived memory for that conversation.

### Task 3: Code-mode behavior and telemetry UI

**Files:**
- Modify: `app/src/main/java/com/musab/aragpt2/MainActivity.java`
- Modify: `app/src/main/java/com/musab/aragpt2/LlamaEngine.java`
- Modify: `app/src/main/res/values/strings.xml`

- [ ] Add a code-oriented system instruction and deterministic sampling defaults.
- [ ] Show first-token latency and generation speed in the status line after a response.
- [ ] Keep chat mode and code mode configurable without changing the model file.

### Task 4: Verification

**Files:**
- Modify: `.github/workflows/build-apk.yml`

- [ ] Build debug APK in CI.
- [ ] Run Android emulator smoke tests where the environment permits.
- [ ] Run native compile/build verification.
- [ ] Verify DB migration with a v1 fixture.
- [ ] Verify a two-turn session preserves context and reopening preserves history.
- [ ] Do not claim a speed target until an actual device benchmark is captured.

### Task 5: Python verification tool (separate milestone)

**Files:**
- Create: `app/src/main/java/com/musab/aragpt2/PythonSandbox.java`
- Create: `app/src/test/...` for sandbox policy tests

- [ ] Add a local Python runtime only after inference baseline is stable.
- [ ] Enforce isolated working directory, timeout, output cap, and no network access.
- [ ] Feed execution errors back to the model for one bounded repair attempt.
- [ ] Never present unexecuted code as verified.
