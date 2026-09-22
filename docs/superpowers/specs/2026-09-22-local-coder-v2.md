# Local Coder V2 Specification

**Goal:** Turn NewAl into an offline Android Python/code assistant with a modern llama.cpp runtime, persistent conversation memory, prompt reuse, deterministic code mode, and measurable performance.

## Requirements

- Keep runtime fully offline after the GGUF is on the device.
- Preserve GGUF picker/staging and the existing chat UI.
- Target arm64-v8a and Galaxy A16-class ARM64 hardware.
- Replace llama.cpp v0.2.0 with a pinned modern upstream build (b11058 / commit f072b103714dfa1eee531f80b24512faf38e3dd2 for the first implementation baseline).
- Enable ARM KleidiAI and OpenMP for arm64-v8a; keep GPU backends disabled until CPU baseline is measured.
- Use llama.cpp's native chat-template and sampler APIs; do not implement vocabulary-wide sorting in the app bridge.
- Keep the loaded model/context alive between turns and avoid unconditional KV-cache clearing.
- Persist chat history without destructive schema upgrades.
- Add a local memory summary and memory facts store; retrieved memory must be injected only when relevant and must never be presented as model truth without provenance.
- Record generation telemetry: load time, prompt tokens, generation tokens, first-token latency, generation tokens/sec.
- Code mode must use low-temperature deterministic sampling and a programming-oriented system prompt.
- Do not claim code correctness without execution; a Python sandbox is a separate follow-up milestone after the runtime baseline.

## Acceptance Criteria

1. `./gradlew :app:assembleDebug` succeeds in CI.
2. Native bridge compiles against the pinned llama.cpp commit without v0.2.x API calls.
3. `Hi` produces a response without the current multi-minute stall on the same device; actual latency is reported rather than guessed.
4. Closing/reopening the app preserves the conversation.
5. Database upgrade from v1 to v2 preserves existing messages.
6. The native layer exposes measurable prompt/generation statistics.
7. No network request is required at runtime.
