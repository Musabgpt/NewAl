# Termux execution loop

Agent mode (🛠 Termux button) turns a request into a closed loop:

```
model tokens ─► CodeStreamParser ─► ProjectWorkspace ─┬─► app mirror (files/projects/<id>)
   (JNI stream)                                        └─► TermuxBridge ─► newal_agent.py ─► ~/newal/projects/<id>
                                                                               │  bash -c <command>
AgentLoop ◄── exit code + stdout/stderr (streamed) ◄───────────────────────────┘
   └─ failure → minimal fix prompt → EDIT/FILE blocks → write → run again
```

## Pieces

| File | Role |
|---|---|
| `cpp/llama_bridge_v2.cpp` | Streams UTF-8 bytes to Java per token; reuses the KV-cache prefix between calls; holds back partial stop markers; grows the piece buffer instead of dropping long tokens; q8_0 KV cache with f16 / smaller-context fallback. |
| `LlamaEngine`, `Utf8StreamDecoder` | Incremental UTF-8 decoding, so a character split across tokens is never lost. |
| `CodeStreamParser` | Writes file content as it arrives. Only a partial line that could be the closing fence is held back. Handles `FILE:`/`EDIT:`/`RUN:`, paths in fence info, longer fences, and cut-off blocks. |
| `EditApplier` | SEARCH/REPLACE edits. Applies only on an exact match (or one that differs only in trailing whitespace), otherwise reports the mismatch to the model. |
| `ProjectWorkspace` | Write-through to the mirror and to Termux, with SHA-256 verified on both sides at close. A cut-off rewrite is rolled back to the previous version. Keeps a journal (`.newal/state.json`), a structured log (`.newal/log.jsonl`) and raw output (`.newal/runs/`). |
| `TermuxBridge` | One authenticated localhost socket with binary frames, request/response futures, streamed exec output, reconnection, and resync of stale files by hash. |
| `newal_agent.py` (asset) | Runs inside Termux. asyncio server that writes files, runs in-process syntax checks (`compile`, `json`), resolves commands with `which`, and launches processes. stdout goes through a pty (line-buffered in real time), stderr through a separate pipe. Handles timeout and process-group kill. |
| `TermuxLauncher` | Starts the agent through Termux's official `RUN_COMMAND` service as a background task. |
| `CommandPlanner` | Derives install, test and run commands from the project (Python, Node, shell, C/C++, Go, Rust, Java). |
| `SafetyPolicy` | Destructive or system-level commands need user confirmation; ordinary project runs don't. |
| `AgentLoop` | State machine, prompts, stuck detection, metrics, crash resume. |

## One-time setup (on the phone)

1. Install Termux from F-Droid or GitHub.
2. In the app, tap **🛠 Termux** and allow the "Run commands in Termux" permission.
3. The app shows one command. Copy it into Termux once. It sets `allow-external-apps=true` and installs Python.

After that the app starts the agent by itself whenever it is not running. On Android 12+, very aggressive
battery settings can kill Termux background tasks. The app then relaunches the agent and re-syncs files automatically.

## Success rule

Only Termux's exit code decides success. When a project has tests, the tests are the success criterion.
The loop stops with `WAITING_FOR_USER` in these cases:
- the same error repeats 3 times
- the model changes nothing twice in a row
- a required command is missing (for example clang)
- a risky operation is declined

## Metrics (per attempt, in `.newal/log.jsonl`)

`time_to_first_token_ms`, `time_to_first_file_write_ms`, `generation_ms`, `prompt_tokens`, `reused_prompt_tokens`,
`tokens_per_s`, `file_write_ms`, `validation_ms`, `exec_termux_start_latency_ms`, `exec_execution_ms`,
`analysis_ms`, `patch_apply_ms`, `iteration_ms`.

## Tests

`AgentLoopIntegrationTest` runs the real `newal_agent.py` with `python3` and a scripted model. It covers:
- runtime error → EDIT fix
- syntax error caught before execution
- failing unit tests never reported as success
- repeated failure → stop
- destructive command → confirmation
- agent killed → reconnect and resume

## Tools and plugins (all local and free)

The model can call tools with `TOOL: name {json}`, gets the real result back, and continues. Tool rounds don't
count as repair attempts. A plain answer is `SAY: text`. Tools come from three sources, all discovered by the
agent inside Termux, with nothing to rebuild in the app:

| Source | How to add |
|---|---|
| Termux:API | Install the Termux:API app (F-Droid), then `pkg install termux-api`. Adds `battery_status`, `notify`, `clipboard_get/set`, `vibrate`, `speak`, `location`, `wifi_info`, `torch`. |
| Script plugin | `~/newal/tools/<name>/tool.json`: `{"name","description","parameters","command"}`. The command runs in that folder, receives the arguments as JSON on stdin, and prints the result. |
| MCP server | `~/newal/mcp.json`: `{"servers": {"<name>": {"command": "...", "args": [...], "env": {}}}}`. Standard Model Context Protocol over stdio; tools appear as `<name>.<tool>`. |

Long-press the 🛠 Termux button to see the installed tools.

## Other behaviour

- **Output grammar:** in agent mode, a GBNF grammar allows only `FILE` / `EDIT` / `STDIN` / `RUN` / `TOOL` / `SAY`
  blocks.
- **Interactive programs:** a `STDIN:` block gives sample keyboard input, so interactive programs are tested
  automatically.
- **Missing modules:** a missing Python/Node module is installed (`pip` / `npm`) and the program re-run, without
  spending a model attempt.
- **Background running:** a foreground service with a progress notification keeps a task running while you use
  other apps.

## Agents and skills

Agents (chips in agent mode, or a `/command` at the start of the message). All of them share the same model and
loop:

| Agent | Command | What it does |
|---|---|---|
| 🤖 Auto | | Picks one from the request |
| 💻 Coder | `/code` | Writes and runs code |
| 🏗 Architect | `/plan` | Plans the files first, then codes them |
| 🧪 Tester | `/test` | Writes and runs tests |
| 🔧 Fixer | `/fix` | Takes pasted code and an error, and fixes it |
| 📖 Explainer | `/explain` | Answers only, reading project files with tools |
| 📱 Phone | `/phone` | Personal assistant: device control tools |

Skills are focused recipes added to the prompt: `/interactive`, `/scraper`, `/api`, `/data`, `/sqlite`, `/bash`,
`/files`, `/tests`, `/phone`, `/algo`, `/telegram`, `/cli`. They are also picked automatically by keywords. You
can add your own as `~/newal/skills/<id>.md` in Termux, with the first line `# Title | keyword, keyword`.

Built-in tools that need nothing extra: `list_files`, `read_file`, `search`, `http_get`, `now`, `system_info`,
`calc`.

A result card offers ▶ run again, 📂 files, ⌨ open in Termux and ↩ undo (reverts the last attempt). ⚙ Settings
has: max attempts, timeout, output grammar, and an automatic tester pass after success.

Models: 📂 model button → download Qwen2.5-Coder 0.5B / 1.5B / 3B / 7B GGUF from Hugging Face (free). Models too
large for the phone's RAM are marked.

## Personal assistant (device control)

In agent mode, device requests go to the 📱 assistant, for example "افتح يوتيوب", "اضبط منبه 7 الصبح",
"شغّل الكشاف" or "ارفع الصوت". Its tools are real Android actions: open_app, list_apps, open_url, web_search,
navigate, dial, compose_sms, set_alarm, set_timer, open_settings, flashlight, volume, media, share_text,
open_camera and device_status. Calls and messages only open the dialer or composer; you press send.

Screen control (🖐 chip): enable "NewAl screen control" in Accessibility settings once. The assistant can then
read_screen, tap (by visible text), type_text (asks first), scroll and press (back / home / recents /
notifications / quick_settings / lock / screenshot). On Android 13+ a sideloaded app first needs
App info → ⋮ → Allow restricted settings.

## Model team (🧠)

Each role (🧭 manager, 💻 coder, 🗣 language) can use its own GGUF. The models stay loaded together as long as the
phone's free RAM allows (`ModelPool`). When memory is short, the least recently used model is unloaded, but never
the one in use. Switching roles then costs no reload. The models run one after another, not in parallel.

**Speculative decoding (⚡):** a small draft model (for example Qwen2.5-Coder 0.5B) proposes a few tokens, and the
main model checks them all in one pass. The output is identical to the main model alone, because greedy
verification keeps only tokens the main model would have picked. It is used only when:
- sampling is greedy,
- both models share the same vocabulary, and
- the main model is at least 3× larger than the draft.

Measured on CPU:
| Main model | Draft | Speed |
|---|---|---|
| 3B | 0.5B, k=3 | about +22% |
| 1.5B | 0.5B | no gain, so it is switched off automatically |

The status line shows the acceptance rate.

In team mode:
1. The manager routes the request (`ROUTE: code|phone|explain|chat` plus up to 8 `PLAN:` lines).
2. The specialist does the work.
3. The language model answers or summarises in your language.

## Learning (📈)

- **Reward:** every task is scored by its real result (reward 1.0 for success on the first attempt, less for
  more attempts, 0 for failure).
- **What it learns:** the app remembers solved tasks and recalls them as worked examples for similar requests,
  remembers which fix resolved each error, and picks the build strategy with the highest reward per task type
  (UCB1 bandit).
- **What it does not do:** none of this changes the model's weights on the phone.
- **Training the weights:** export the data from 📈 and run `training/finetune_colab.py` on a free Colab GPU.
  It produces a GGUF that you load back into the app.
