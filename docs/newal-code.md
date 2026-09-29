# NewAl Code

NewAl Code is NewAl's coding agent. It works like Claude Code and Codex: the model answers, calls tools, sees what
they return, and keeps going until the task is done. It then checks the work with the project's own tests. It runs
**any model or set of models**: local GGUF files on llama.cpp sized to the computer's RAM (8 GB and up), any
OpenAI-compatible endpoint, or Anthropic's API. It has a **Codex-style interface**, as a desktop window, a web app
or a terminal UI.

Code: `desktop/newal_code/` (standard library only). Tests: `desktop/tests/test_newal_code.py`. Speed test:
`desktop/bench/`.

## Running it

| How | Command |
|---|---|
| Desktop window (Windows: `NewAl\code\NewAlCode.exe`, installed next to NewAl) | `python desktop/NewAlCode.py` |
| Web app in the browser | `python -m newal_code app` (from `desktop/`) |
| Terminal UI (Codex CLI style) | `python -m newal_code` in the project folder |
| One request, no questions (like `codex exec` / `claude -p`) | `python -m newal_code exec "fix the failing test" [--json] [--full-auto]` |
| Models for this computer | `python -m newal_code models [--download qwen3.5-4b]` |
| Check the setup | `python -m newal_code doctor` |
| Speed test on this computer | `python -m newal_code bench [--model ID]` |

It uses NewAl desktop's `llama-server` and the models NewAl already downloaded (`~/NewAl/models`). Elsewhere, set
`NEWAL_LLAMA_SERVER` or `"llama_server"` in `~/.newal-code/config.json`.

## How it works

```
 user ──► Service ──► Agent loop ──────────────► model (llama.cpp / OpenAI-compatible / Anthropic)
            │            │  ▲                          streamed; cancel closes the socket at once
            │            ▼  │ results
            │      permissions ─► hooks ─► approval (UI) ─► tools: read edit write apply_patch glob grep
            │                                                      bash job todo task web_search web_fetch
            │                                                      skill mcp__*
            │      checkpoints (undo) · verify (project tests) · Stop hooks · /goal check · compaction
            ▼
      web app (server.py + ui/) · terminal (tui.py) · exec
```

- **One fixed start per model.** The system prompt and the tool list do not depend on the project, the date or the
  mode. The model reads them once, the reading is saved to disk, and it is restored at the next start. A new thread
  in any project then starts reading at the user's first message. AGENTS.md, CLAUDE.md and the project facts go in
  that first message, the way Claude Code and Codex do it.
- **Append-only conversation.** Nothing earlier is rewritten, so llama.cpp continues from its cache even on hybrid
  models (Qwen3.5/3.6), which cannot step back. Each conversation has its own server slot, so a sub-agent does not
  evict the main agent's context.
- **The first message carries what the task needs.** The project's files, git state and test command, the files the
  request names, where the named functions are defined and used, and the project modules those files import, all
  within a small budget. The model can act in its first step instead of spending rounds exploring.
- **Few tokens written.** On a CPU, writing is the slowest part (~6 tokens/s for a 4B model). The model edits with
  exact replacements, calls independent tools together, and ends with one or two sentences. Thinking is off for
  local models unless asked for.
- **The computer checks the work.** After a turn changes files, the project's tests run; failures go back to the
  model, up to 2 rounds. If the model already ran the tests after its last change, nothing runs again.

## Features, next to Claude Code and Codex

| Feature | Claude Code | Codex | NewAl Code |
|---|---|---|---|
| Agent loop with tool calls, streaming, interrupt (Esc) | ✓ | ✓ | ✓ (native tool calls: llama.cpp, OpenAI, Anthropic) |
| Read / Write / Edit / Glob / Grep / LS | ✓ | via shell | ✓ `read` `write` `edit` `glob` `grep` (a folder given to `read` lists it); edits tolerate indentation and line-number mistakes |
| apply_patch format | – | ✓ | ✓ (`apply_patch`, offered instead of `edit` to models trained on it) |
| Shell, background commands, output of a running job | ✓ Bash, BashOutput, KillShell | ✓ | ✓ `bash` (timeout, background) and `job` |
| Plan / checklist | ✓ TodoWrite | ✓ update_plan | ✓ `todo` (pinned above the composer) |
| Sub-agents | ✓ Task + `.claude/agents` | ✓ | ✓ `task` + `.newal/agents`, `.claude/agents`, NewAl desktop's agents; own model, tools, mode; built-in explore, worker, reviewer |
| Web search and fetch | ✓ | ✓ search | ✓ `web_search` (Bing RSS, DuckDuckGo, Wikipedia; no key) and `web_fetch` |
| Permission modes | default, acceptEdits, plan, bypass | read-only, auto, full access | read-only, ask, auto-edit, full-auto (their names are accepted too) |
| Allow / ask / deny rules | ✓ `Bash(npm test:*)`, `Edit(src/**)`… | – | ✓ same syntax; "always allow" is kept in `.newal/settings.json` |
| Catastrophic commands refused | – | sandbox | ✓ refused in every mode (`rm -rf /`, `mkfs`, fork bomb…); risky ones ask even in auto-edit |
| OS sandbox for commands | – | ✓ Seatbelt / Landlock | ✓ Linux (Landlock, no root needed): commands write only inside the project and temp folders; read-only mode writes nowhere; optional no-network; when the sandbox blocks a command, the user may let it run once without it (Codex's "on failure"). Windows/macOS: modes and rules only |
| Project instructions | CLAUDE.md, @imports | AGENTS.md | ✓ both, user-wide and from the repository root down, with @imports; `# note` adds to them |
| Custom slash commands | ✓ `.claude/commands` ($ARGUMENTS, !`cmd`, @file) | ✓ `~/.codex/prompts` | ✓ both locations, same syntax |
| Skills (SKILL.md, loaded on demand) | ✓ | ✓ | ✓ `.newal/skills`, `.claude/skills`, `~/.codex/skills` |
| Hooks | ✓ PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, SessionStart, PreCompact | – | ✓ same events, JSON protocol and exit code 2 |
| MCP servers | ✓ `.mcp.json` | ✓ config.toml | ✓ stdio and HTTP; `.mcp.json`, `~/.codex/config.toml`, NewAl desktop's add-ons |
| Sessions, resume | ✓ | ✓ | ✓ threads kept as JSONL; `/resume`; the web app lists them per project |
| Compaction | ✓ auto + `/compact` | ✓ | ✓ auto at 85% of the context + `/compact [focus]` |
| Checkpoints / undo | ✓ /rewind | ✓ /undo | ✓ every changed file saved first; `/undo`, "Undo last turn", revert one file |
| Diff / review | /review | ✓ /diff, /review, review pane | ✓ `/diff`, `/review` (reviewer sub-agent), review pane with per-file diffs |
| Git commit from the app | – | ✓ | ✓ Commit button (changed files only) |
| `/init` AGENTS.md | ✓ | ✓ | ✓ |
| `/goal` (keep working until a condition holds) | ✓ | – | ✓ checked after every answer |
| Plan first | plan mode | – | ✓ `/plan <task>` (read-only turn) → "Implement this plan" |
| Headless run, JSON events | `claude -p --output-format stream-json` | `codex exec --json` | ✓ `exec [--json]` |
| Reasoning effort | ✓ | ✓ | ✓ off / low / medium / high (thinking budget on llama.cpp, `reasoning_effort` on OpenAI, extended thinking on Anthropic) |
| Images in the prompt | ✓ | ✓ | ✓ paste or attach (vision models) |
| Context and speed shown | ✓ | ✓ | ✓ context used, tokens read (cached) and written, tokens/s |
| Terminal pane | – | ✓ | ✓ |
| Worktree threads | – | ✓ (app) | ✓ "Worktree" under the composer: the thread works in its own git worktree; Apply to project / Discard |
| Any model | Anthropic | OpenAI + providers | local GGUF (llama.cpp), any OpenAI-compatible API, Anthropic, Ollama/LM Studio found by themselves |
| Several models on one task | sub-agent `model:` | profiles | roles (main, fast, review, plan), sub-agents with their own model, local models kept within the RAM budget |
| Runs offline on 8–16 GB RAM | – | – | ✓ (below) |

Not implemented: cloud tasks, a GitHub app, a sandbox on Windows and macOS, plugins and marketplaces, Jupyter
notebook editing.

## RAM: 8 GB to 16 GB

The model and its context must fit next to the OS, a browser and an editor. `hardware.py` reads the RAM and keeps a
share for everything else (2.8 GB on 8 GB, 3.8 GB on 16 GB). `runtime.plan()` reads the GGUF file itself
(`gguf.py`: layers, attention layers, KV heads). It picks the longest context that fits, with an f16 KV cache, or
q8_0 when that buys a longer context. When nothing fits, the model does not start and the user is told why.

RESULTS_RAM

## Speed

RESULTS_SPEED

## Files

| Path | What |
|---|---|
| `newal_code/agent.py` | the loop: requests, tools, permissions, hooks, approvals, verify, goal, compaction, sub-agents |
| `newal_code/tools.py`, `patch.py` | the tools; Codex's patch format |
| `newal_code/permissions.py`, `hooks.py` | modes and rules; Claude Code's hook protocol |
| `newal_code/extensions.py`, `mcp.py` | AGENTS.md/CLAUDE.md, skills, commands, sub-agents; MCP client |
| `newal_code/context.py`, `prompts.py` | the first message's context; what the model is told |
| `newal_code/providers.py`, `models.py` | OpenAI-compatible, llama.cpp and Anthropic clients; the model registry, roles, teams |
| `newal_code/runtime.py`, `gguf.py`, `hardware.py`, `catalog.py` | llama-server, RAM planning, GGUF metadata, local models per tier |
| `newal_code/session.py` | threads (JSONL), checkpoints, diffs |
| `newal_code/service.py`, `server.py`, `ui/` | the app: sessions, slash commands, approvals, JSON API + events, the Codex-style web UI |
| `newal_code/tui.py`, `__main__.py`, `app.py` | terminal UI, command line, desktop window |
| `newal_code/benchmark.py`, `desktop/bench/` | the speed test; drivers for the current NewAl and NewAl Code |
