# NewAl Code

NewAl Code is NewAl's coding agent. It works like Claude Code and Codex: the model answers, calls tools, sees what
they return, and keeps going until the task is done. It then checks the work with the project's own tests. It runs
**any model or set of models**: local GGUF files on llama.cpp sized to the computer's RAM (8 GB and up, and phones
with 2-4 GB), any OpenAI-compatible endpoint, or Anthropic's API. It has a **Codex-style interface**, as a desktop
window, a web app or a terminal UI, on **Windows, Linux and macOS**, and as **NewAl Code Lite on Android**. Commands
run in an **OS sandbox** on all three desktop systems; tasks can run **in the cloud** (GitHub Actions); and a
**GitHub app** answers `@newal` in issues and pull requests.

Code: `desktop/newal_code/` (standard library only). Tests: `desktop/tests/test_newal_code.py`. Speed test:
`desktop/bench/`.

![A thread in the web app: explored, edited (with the diff), ran the tests, answered](images/newal-code-thread.png)

![The review pane: the thread's changes, Undo last turn, Commit / Commit and push / Commit and create PR](images/newal-code-review.png)

(Screenshots of a scripted demo run: `desktop/tools/ui_demo.py` and `ui_shots.js`.)

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
| A task in the cloud (see "Cloud tasks") | `newal-code cloud "add a --json flag"`, then `status`, `show`, `apply`, `pr ID` |
| The GitHub app in a repository (see "GitHub app") | `newal-code github install --pr` |
| The one permission: full access for new threads, or back to asking | `newal-code access full` / `newal-code access ask` |
| `newal` in every terminal, Explorer's menu, a Windows Terminal profile | `newal-code install` (`uninstall` removes them) |

**The first start** shows a welcome, once: a model (the one this computer or phone fits, or an API in one tap), the
one permission, GitHub, and (on a computer) the terminal. Settings > "Set up again" shows it later.

**One permission for full access.** "Give full access" (the welcome, Settings > Access, `newal-code access full`) is
a single grant, kept: new threads then work without the sandbox and without asking, on any file and with any
command, and the phone tool acts without asking; commands that would wipe a drive, the home folder or Windows
(`rm -rf ~`, `Remove-Item C:\ -Recurse`, `rd /s /q C:\`, `Format-Volume`, `Clear-Disk`...) are still refused. A
"Full access" chip under the composer says it is on (a click opens Settings). On a phone the same tap then walks
through what Android grants itself, each on its own screen, skipping what is on already: notifications, the phone's
files (All files access), screen control (the accessibility service) and Termux's commands. "Ask me first again"
takes it back.

**The computer: PowerShell, the terminal, Explorer.** On Windows with Git Bash the agent has a `powershell` tool
beside `bash` (PowerShell 7 when installed, else Windows PowerShell; UTF-8 both ways, no progress bars; Windows
itself: cmdlets, the registry, services, winget), with its own rules (`PowerShell(winget install:*)`) and the same
refusals; without Git Bash, `bash` is PowerShell. The terminal pane runs Git Bash or PowerShell (a choice in its
header) and ⧉ opens a real terminal window there with NewAl Code (Windows Terminal, else PowerShell).
`newal-code install` (or Settings > This computer, or the welcome) puts `newal` and `newal-code` in every new
terminal (the user's PATH: PowerShell, cmd and Git Bash), "Open with NewAl Code" and "NewAl Code terminal here" in
Explorer's right-click menu on folders, and a "NewAl Code" profile in Windows Terminal; no administrator rights.
The app opens a folder it is given (`NewAlCode.exe C:\project`, `newal-code app DIR`) as a new thread there.

**GitHub on a computer, without a token.** Connect GitHub uses this computer's own login first: the GitHub CLI's
(`gh auth token`) or git's credential helper for github.com (Git Credential Manager, which Git for Windows brings
and which signs in through the browser when it has nothing yet); a token only when neither has one.

**Arabic.** The interface is in Arabic, right to left, when the system's language is Arabic (a phone set to Arabic)
or with Settings > Language > العربية (English, or the system's, likewise). The page's own words are translated as
it draws them (`ui/i18n.js`: a dictionary of its strings and patterns for those with a number or a name in them);
never what the model or the user wrote, code, commands or their output. Each message takes the direction of its
own text, in either language, and code, diffs and the terminal stay left to right. The agent answers in the user's
language already.

**The phone, more.** Long-press NewAl Code Lite's icon: "New thread" or "Speak a request" (a new thread and the
microphone at once). A thread that ends while the app is in the background posts a notification (a tap brings the
app back). "+" attaches images from the phone (Android's picker). NewAl Code in Termux, once linked, starts with the
app when the app may start it. The app's own texts (its notification, screen control's description, the
shortcuts) are in Arabic on a phone set to Arabic.

**Speaking and sharing (the phone).** The microphone beside Send types what you say (Android's speech recognition,
in the phone's language; on a computer, the browser's where it has one). "Share" in another app → NewAl Code
starts a new thread with the text and the files (copied into NewAl Code's `Shared` folder), waiting for what to do
with them.

**Downloads** (built and checked on each system by `.github/workflows/newal-code.yml`, see "Builds"): the
pre-releases `newal-code-b<N>` of this repository: Windows x64 (zip: `NewAlCode.exe` and `newal-code.exe`), Linux x64
(tar.gz), macOS Apple silicon and Intel (`NewAl Code.app`; not notarized: the first time, right-click it and choose
Open). llama.cpp is inside each. NewAl Code Lite for Android: the pre-releases `newal-code-lite-b<N>` (APK).

It uses NewAl desktop's `llama-server` and the models NewAl already downloaded (`~/NewAl/models`). Elsewhere, set
`NEWAL_LLAMA_SERVER` or `"llama_server"` in `~/.newal-code/config.json`.

## How it works

```
 user ──► Service ──► Agent loop ──────────────► model (llama.cpp / OpenAI-compatible / Anthropic)
            │            │  ▲                          streamed; cancel closes the socket at once
            │            ▼  │ results
            │      permissions ─► hooks ─► approval (UI) ─► tools: read edit write apply_patch glob grep
            │                                                      bash job todo task web_search web_fetch
            │                                                      skill notebook_edit mcp__*
            │      checkpoints (undo) · verify (project tests) · Stop hooks · /goal check · compaction
            ▼
      web app (server.py + ui/) · terminal (tui.py) · exec
```

- **One fixed start per model.** The system prompt and the tool list do not depend on the project, the date or the
  mode. The model reads them once, the reading is saved to disk and restored at the next start (0.1 s instead of
  ~45 s for a 4B model on 4 cores). AGENTS.md, CLAUDE.md and the project facts go in the thread's first message,
  the way Claude Code and Codex do it, and a new thread reads them as soon as it opens, while the user types: the
  first request then reads only the request.
- **Append-only conversation.** Nothing earlier is rewritten, so llama.cpp continues from its cache even on hybrid
  models (Qwen3.5/3.6), which cannot step back. Each conversation has its own server slot, so a sub-agent does not
  evict the main agent's context.
- **The first request brings what the task needs.** The project's files, git state and test command go in the message.
  The files the request names, where the named functions are defined and used, the project modules those files
  import, and their tests are added as reads already made (like Claude Code's @-mentions). The model then acts in
  its first step instead of spending rounds exploring, and does not read those files again.
- **Few tokens written.** On a CPU, writing is the slowest part (~6 tokens/s for a 4B model). The model edits with
  short exact replacements (files are shown as they are, so what it copies matches), calls independent tools
  together, and ends with one sentence. Files with multi-token-prediction heads draft several tokens per step
  (measured: +50% when writing a whole file, +33% on agent steps).
- **Thinking only where it pays.** A local model does not think before acting on a task: running the change checks it.
  It thinks briefly before answering a question (nothing will check the answer), and right after a change fails its
  check (longer the second time). When the same error comes back, the result says so.
- **Problems come back with the edit.** A syntax error or a name that is never imported is reported in the edit's
  result, so it is fixed in the next step instead of being found by a failing run later.
- **The computer checks the work, with each change.** After every step that changes files, the project's tests
  run by themselves (up to a minute; a slower suite is left to the model) and their result comes with that step,
  like an edit's problems. The model sees at once whether its change works, without spending a step (on a CPU, a
  model call) on running them. When the turn ends, the tests run once more unless they already passed after the
  last change; failures go back to the model, up to 2 rounds.

## Features, next to Claude Code and Codex

| Feature | Claude Code | Codex | NewAl Code |
|---|---|---|---|
| Agent loop with tool calls, streaming, interrupt (Esc) | ✓ | ✓ | ✓ (native tool calls: llama.cpp, OpenAI, Anthropic) |
| Read / Write / Edit / Glob / Grep / LS | ✓ | via shell | ✓ `read` `write` `edit` `glob` `grep` (a folder given to `read` lists it); edits tolerate indentation and line-number mistakes |
| apply_patch format | – | ✓ | ✓ (`apply_patch`, offered instead of `edit` to models trained on it) |
| Jupyter notebooks | ✓ NotebookEdit | – | ✓ `read` shows the cells and their output; `notebook_edit` replaces, inserts or deletes a cell (offered in projects that have notebooks) |
| Shell, background commands, output of a running job | ✓ Bash, BashOutput, KillShell | ✓ | ✓ `bash` (timeout, background) and `job`; on Windows `powershell` too |
| One permission for full access, kept | – (a flag per run) | ✓ (a setting) | ✓ "Give full access" once (welcome, Settings, `newal-code access full`); on a phone it walks through Android's grants too |
| Terminal and OS integration | ✓ `claude` in the terminal | ✓ `codex` | ✓ `newal` in PowerShell, cmd and Git Bash, Explorer's "Open with NewAl Code", a Windows Terminal profile (`newal-code install`) |
| Plan / checklist | ✓ TodoWrite | ✓ update_plan | ✓ `todo` (pinned above the composer) |
| Sub-agents | ✓ Task + `.claude/agents` | ✓ | ✓ `task` + `.newal/agents`, `.claude/agents`, NewAl desktop's agents; own model, tools, mode; built-in explore, worker, reviewer |
| Web search and fetch | ✓ | ✓ search | ✓ `web_search` (Bing RSS, DuckDuckGo, Wikipedia; no key) and `web_fetch` |
| Permission modes | default, acceptEdits, plan, bypass | read-only, auto, full access | read-only, ask, auto-edit, full-auto (their names are accepted too) |
| Allow / ask / deny rules | ✓ `Bash(npm test:*)`, `Edit(src/**)`… | – | ✓ same syntax; "always allow" is kept in `.newal/settings.json` |
| Catastrophic commands refused | – | sandbox | ✓ refused in every mode (`rm -rf /`, `mkfs`, fork bomb…); risky ones ask even in auto-edit |
| OS sandbox for commands | – | ✓ Seatbelt / Landlock (Windows: experimental) | ✓ Linux (Landlock), macOS (Seatbelt), Windows (low integrity), none needing admin rights: commands write only inside the project, the added folders and temp; read-only mode writes nowhere; no network on request (Linux, macOS); when the sandbox blocks a command, the user may let it run once without it (Codex's "on failure"). See "Command sandbox" |
| More folders than the project | ✓ `/add-dir`, `--add-dir` | ✓ `--add-dir` | ✓ `/add-dir`, `--add-dir` (edits there are treated like the project's; the sandbox lets commands write there) |
| Project instructions | CLAUDE.md, @imports | AGENTS.md | ✓ both, user-wide and from the repository root down, with @imports; `# note` adds to them |
| Custom slash commands | ✓ `.claude/commands` ($ARGUMENTS, !`cmd`, @file) | ✓ `~/.codex/prompts` | ✓ both locations, same syntax |
| Skills (SKILL.md, loaded on demand) | ✓ | ✓ | ✓ `.newal/skills`, `.claude/skills`, `~/.codex/skills` |
| Hooks | ✓ PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, SessionStart, SessionEnd, PreCompact, Notification | – | ✓ same events, JSON protocol and exit code 2 |
| MCP servers | ✓ `.mcp.json` | ✓ config.toml | ✓ stdio and HTTP; `.mcp.json`, `~/.codex/config.toml`, NewAl desktop's add-ons |
| Plugins and marketplaces | ✓ commands, agents, skills, hooks, MCP in one folder; `/plugin marketplace add` | – | ✓ Claude Code's plugin layout (`${CLAUDE_PLUGIN_ROOT}` included) in `.newal/plugins` or `~/.newal-code/plugins`; its marketplaces too: `/plugin marketplace add <owner/repo, git URL or folder>`, `/plugin install name@marketplace` (or a git URL or folder), `/plugin remove`; a built-in marketplace whose plugins are programs (`/sysinfo`, `/disk`, `/clean`, `/ports`, `/programs`, a guard, formatting), installed offline with one tap |
| Sessions, resume | ✓ | ✓ | ✓ threads kept as JSONL; `/resume`; the web app lists them per project |
| Compaction | ✓ auto + `/compact` | ✓ | ✓ auto at 85% of the context + `/compact [focus]` |
| Checkpoints / undo | ✓ /rewind | ✓ /undo | ✓ every changed file saved first; `/undo`, "Undo last turn", revert one file |
| Diff / review | /review | ✓ /diff, /review, review pane | ✓ `/diff`, `/review` (reviewer sub-agent), review pane with per-file diffs |
| Pull and push in one step | – | – | ✓ `/sync`: pull (rebase, or a merge with the phone's git), then push, with the connected GitHub account; conflicts explained with the way out |
| GitHub issues | – | – | ✓ `/issues` lists the repository's open issues (labels, age, comments; pull requests left out); `/issues 7` makes issue #7 (its text and comments) the task, to fix and to close with "Fixes #7" |
| Git commit, push, pull request from the app | – | ✓ | ✓ Commit / Commit and push / Commit and create PR (a branch of its own when on main; `gh` when installed, else GitHub's API with the connected token, else GitHub's PR page) |
| GitHub account in the app | – | ✓ (cloud) | ✓ Settings > GitHub: connect with a token, then clone any of your repositories (the ⬇ button beside Threads); cloud tasks and pull requests use it |
| `/init` AGENTS.md | ✓ | ✓ | ✓ |
| `/goal` (keep working until a condition holds) | ✓ | – | ✓ checked after every answer |
| Plan first | plan mode | – | ✓ `/plan <task>` (read-only turn) → "Implement this plan" |
| Headless run, JSON events | `claude -p --output-format stream-json` | `codex exec --json` | ✓ `exec [--json] [-o answer.txt] [--max-steps N] [--resume ID]` |
| Reasoning effort | ✓ | ✓ | ✓ off / low / medium / high (thinking budget on llama.cpp, `reasoning_effort` on OpenAI, extended thinking on Anthropic) |
| Images in the prompt | ✓ | ✓ | ✓ paste or attach (vision models) |
| Context and speed shown | ✓ | ✓ | ✓ context used, tokens read (cached) and written, tokens/s |
| Terminal pane | – | ✓ | ✓ |
| Worktree threads | – | ✓ (app) | ✓ "Worktree" under the composer: the thread works in its own git worktree; Apply to project / Discard |
| Cloud tasks | ✓ Claude Code on the web | ✓ Codex cloud | ✓ on GitHub Actions: `newal-code cloud "task"` or Cloud under the composer; the diff comes back to review, apply here or open as a pull request (see "Cloud tasks") |
| GitHub app | ✓ `@claude` | ✓ `@codex` | ✓ `@newal` in issues, pull requests and review comments: answers, reviews with inline comments, or makes the change (see "GitHub app") |
| Windows, Linux, macOS; phones | macOS, Linux, Windows | macOS, Linux, Windows | ✓ all three (built and checked by CI) and NewAl Code Lite for Android phones with 2-4 GB |
| Any model | Anthropic | OpenAI + providers | local GGUF (llama.cpp), any OpenAI-compatible API, Anthropic, Ollama/LM Studio found by themselves; Gemini, DeepSeek, OpenAI, OpenRouter, Anthropic and Groq in one tap (see "An API in one tap") |
| The phone itself | – | – | ✓ NewAl Code Lite: the agent opens apps and settings, sets alarms, reads the screen and taps, types and swipes (see "Phone features") |
| Several models on one task | sub-agent `model:` | profiles | roles (main, fast, review, plan), sub-agents with their own model, local models kept within the RAM budget |
| Runs offline on 8–16 GB RAM | – | – | ✓ (below) |

Differences that remain: a cloud task is one attempt (Codex can run several and let you pick; a follow-up is a new
task on the same branch); the GitHub app is a workflow in the repository rather than an installed app with its own
account (its commits are "NewAl Code" via Actions' token, which does not start other workflows); the Windows sandbox
does not block the network; the macOS app is not notarized.

## Any model, or a set of models

| Model | How |
|---|---|
| A local GGUF file | the catalog (`newal-code models --download ID`), any file in `~/.newal-code/models` or `~/NewAl/models`, or `--model path/to/file.gguf`. MTP heads are found in the file and used for drafting. |
| Ollama, LM Studio | found by themselves while they run (`ollama/<name>`, `lmstudio/<name>`) |
| An API | `--model provider/model` with the key in the environment: `openai/…` (`OPENAI_API_KEY`), `anthropic/…` (`ANTHROPIC_API_KEY`), `openrouter/…`, `deepseek/…`, `groq/…`, `gemini/…`, `mistral/…`, `together/…`, `xai/…`, `cerebras/…`, `fireworks/…`, `vllm/…`, `llamacpp/…` |
| Any other endpoint | Models → "Add an API or a server" (base URL, model name, key variable), or `"models"` in `~/.newal-code/config.json` |

Several models on one task: **roles** map a job to a model (`"roles": {"fast": "qwen3.5-2b", "review":
"qwen3.5-9b", "plan": "anthropic/claude-sonnet-4-5"}`, also in Models → Roles). The explore sub-agent uses
`fast`, the reviewer uses `review`, `/plan` uses `plan`, and any sub-agent file can name its own `model:`.
**Teams** are named sets of roles (`"teams": {"hybrid": {"main": "qwen3.5-9b", "review": "openrouter/..."}}`).
Local models share the RAM budget: a second one loads only when both fit, and otherwise the least recently used one
stops and starts again when it is needed (its fixed start comes back from disk; the conversation is read again).

Measured with a 16 GB computer's budget (12.0 GB): main model Qwen3.5 9B fixed a bug (172 s), then `/review` ran the
reviewer sub-agent on Qwen3.5 4B (the `review` role). Both need 12.7 GB, so the 4B took the 9B's place, and the 9B
came back for the main agent's answer (the review turn took 445 s in all, most of it the 9B reading the conversation
again). Where both fit, nothing stops: a 4B with a 2B helper needs about 7.4 GB, which even 12 GB computers have.

## RAM: 8 GB to 16 GB

The model and its context must fit next to the OS, a browser and an editor. `hardware.py` reads the RAM (or the
container's limit) and keeps a share for everything else (2.8 GB on 8 GB, 3.3 GB on 12 GB, 3.8 GB on 16 GB).
`runtime.plan()` reads the GGUF file itself (`gguf.py`: layers, attention layers, KV heads, the recurrent layers'
state, MTP heads) and counts everything llama-server will hold: the weights, the KV cache (the MTP head's too), each
conversation's recurrent state and the copies llama.cpp keeps of it to step back to (capped at 3 per conversation:
by default it keeps up to 32, about 1.6 GB per conversation on Qwen3.5-4B, growing with every request), the compute
buffers, and a RAM cache for other threads that only takes what is left. It picks the longest context that fits
(16k at least: less is too little for an agent), with an f16 KV cache, or q8_0 when that buys a longer context.
When nothing fits, the model does not start and the user is told why.

Measured on Linux (`desktop/bench/run_limited.py`): each computer is simulated by one memory limit the size of its
usable RAM, shared by NewAl Code, the model and a second process that holds the share kept for Windows and the other
apps. NewAl Code reads the limit as the computer's RAM, plans for it, and the whole speed test (below) runs inside:

| Computer (usable RAM) | Kept for Windows and apps | Model (the default there) | Plan | Speed test inside the limit |
|---|---|---|---|---|
| 8 GB (7.8 GB) | 2.8 GB | Qwen3.5 4B + MTP | 32k context, 4.95 GB | 6 of 6 in 495 s; model + NewAl Code used at most 4.91 GB; nothing killed |
| 12 GB (11.8 GB) | 3.3 GB | Qwen3.5 9B + MTP | 32k context, 7.8 GB | 6 of 6 in 592 s; nothing killed |
| 16 GB (15.8 GB) | 3.8 GB | Qwen3.5 9B + MTP | 32k context, 7.8 GB, and 2 GB of RAM cache for other threads | the 12 GB case with 3.5 GB to spare |

The kernel read back only 684 and 109 pages from disk (no thrashing). On 8 GB the limit was really reached: the OS
took back the pages of the model file that llama.cpp had copied into its faster layout, as expected, and the run was
not slower than the same model without a limit (495 s against 550 s). These two runs also read each new thread's
project context before the task was sent, as the app does while the user types (about 70 tokens, 2 s per task
here); the runs in the next section did not.

The current NewAl's brain, Qwen3.6-35B-A3B IQ3_S (15.35 GB), fits none of these computers. Its 2-bit version
(11.8 GB) needs about 12.7 GB with its buffers (measured), more than a 16 GB computer can spare, so NewAl Code offers
it from 18 GB.

## Phones: 2 GB to 4 GB (NewAl Code Lite)

A phone keeps more of its RAM for Android than a computer keeps for Windows, and reports less than its size (a 2 GB
phone: 1.8-1.9 GB). Below 2.5 GB of budget a model runs **lite**: one conversation slot, one state checkpoint,
256-token batches (smaller buffers), a context of 16k at most (8k-12k when that is what fits), no MTP on models under
1 GB (on Qwen3.5-0.8B it was slower: 17.6 against 21 tokens/s), and without llama.cpp's faster weight copy
("repack") when even that does not fit. Measured with Qwen3.5-0.8B Q4_K_M after a 7k-token prompt: 1402 MB held as
a desktop runs it, 926 MB lite, 737 MB lite without repack, at the same speed on this computer.

| Phone | Kept for Android | Model (the default there) | Plan |
|---|---|---|---|
| 2 GB | 1.1 GB | Qwen3.5 0.8B Q4_K_M (0.53 GB) | 16k context, no repack, ~0.8 GB |
| 3 GB | 1.6 GB | Qwen3.5 0.8B Q4_K_M | 16k context, ~1.06 GB |
| 4 GB | 2.0 GB | Qwen3.5 2B Q4_K_M (1.33 GB) | 16k context, no repack, no MTP, ~1.6 GB |
| 6 GB | 2.4 GB | Qwen3.5 2B with MTP | 32k context, ~2.4 GB |

Small models make mistakes big ones do not, and NewAl Code now absorbs the common ones (found with the 0.8B models on
these plans): a long absolute path copied wrong is mapped to the project (the longest tail of it that fits, only when
the path as written cannot be meant), a write never creates a folder tree outside the project, a call or command that
fails the same way is flagged the second time and ends the turn the fourth, and a model under 1.5 GB is given the
project folder by name only. With them, the task "Create hello.py that prints 'hello from the phone', then run it
with python3" took 3 steps with each phone plan on this computer: 17 s (3 GB), 21 s (4 GB, 2B), and with the 2 GB plan
14 s of requests (the 4-bit 0.8B without repack, after its model loaded from a cold disk; 19 s with the 3-bit one).

**NewAl Code Lite** (`android-lite/`) is the Android app: a WebView showing NewAl Code's own interface (it adapts
to a phone's width), started by a foreground service so Android does not stop it mid-answer. It carries python.org's
official Python 3.14 for Android with a small launcher compiled with the NDK (`libnewalpy.so`: it runs NewAl Code, and
is `python3` for the agent's commands), llama-server built for phones (an ARMv8 build that runs everywhere and a
dot-product build picked on CPUs that have it), and NewAl Code itself. Commands run in Android's `sh` with its tools
and `python3`; there is no git on the phone, and Android's app sandbox confines what commands can touch.
`.github/workflows/newal-code-android.yml` builds the APK and runs it in Android emulators the size of a 3 GB and a
2 GB phone, where the app downloads the model its RAM gets and the agent does the task above, and on Android 15,
where it checks that the page is laid out between the system bars (`android-lite/tests/phone_test.py`):

| Emulated phone | Model | Task | llama-server (peak) | NewAl Code's Python | The app | Least free RAM |
|---|---|---|---|---|---|---|
| 3 GB (2985 MB) | Qwen3.5 0.8B Q4_K_M (16k context) | done, 3 steps, 25 s | 1023 MB | 35 MB | 185 MB | 1381 MB |
| 2 GB (1980 MB) | Qwen3.5 0.8B Q4_K_M (16k context, no repack) | done, 3 steps, 29 s | 770 MB | 29 MB | 133 MB | 809 MB |

(NewAl Code Lite build 2; the first build ran the 3 GB phone the same way, 20 s. On the 2 GB phone it first got the
3-bit 0.8B, which fit with 703 MB to spare but printed "Hello, World!" instead of the text asked for, hence the 4-bit
one; the 3-bit file stays for phones where even that does not fit.)

### Phone features

**An API in one tap.** Models → "An API in one tap" (also at the bottom of the model menu): Gemini, DeepSeek, OpenAI,
OpenRouter, Anthropic, Groq. With the provider's key copied, a tap connects; without one, the provider's key page
opens (Google AI Studio for Gemini, DeepSeek's platform...), and NewAl Code takes the key from the clipboard when
the user comes back with it copied. The key is checked by listing the models it may use, and the provider's newest
fast model becomes the thread's model (for Gemini, the newest Flash; for DeepSeek, deepseek-chat), with the next
best beside it in the menu. Keys stay in NewAl Code's settings file, readable by this user only. On a phone an API
model is the way to a large model: the phone's own model is a 0.8B-2B one.

**Controlling the phone.** In the app the agent has a `phone` tool: open an app, a link or a settings page (Wi-Fi,
Bluetooth, display, battery...), set an alarm or a timer, the torch, the media volume, post a notification, read or
set the clipboard, share text, prepare a message or a call (the messages app or the dialer opens with it; the user
sends it), and any Android intent. With NewAl Code's accessibility service on (This phone > Screen control opens
Android's page for it; on Android 13 and up an app installed from an APK first needs App info > ⋮ > "Allow
restricted settings", and the page says so, with a button to App info), it also sees the screen, as numbered items with what they are ("[3] Network &
internet · tap"), and taps, types, swipes, scrolls and presses back, home or the notifications. It reads the screen
only when the agent asks, and acts only in a thread: looking (the screen, the apps, the battery) never asks; every
action asks first unless the thread runs in full-auto; a read-only thread only looks. What it reads goes to the
thread's model: the phone's own, or the API the user connected. The app does all this in a small server on
127.0.0.1:8793 that answers only requests with the app's key.

**Your own GGUF files.** Models > "Your GGUF files" lists the GGUF files NewAl Code finds, with a Use button: on a
computer the ones in its models folder, and on a phone the ones already in the phone's storage (Download, Documents
and the folders in them, a Telegram download; not among the photos, videos or music), once the app may read the
phone's files: "Let NewAl Code read the phone's files" opens Android's "All files access" page for the app (Android
8-10 ask for the storage permission instead). The model runs from where it is, not copied. "Pick a GGUF file…"
browses to any file, and "Copy a GGUF into the app…" takes one through Android's file picker (Download, Drive, a USB
stick) and copies it into the app, for phones that cannot give "All files access" (Android Go). A file larger than
the RAM models may use here is marked "needs more RAM" but can still be tried.

**The layout on a phone.** Android 15 draws apps under the status bar and the navigation bar; the app keeps NewAl
Code's page between them (and above the keyboard), in the page's own colours. On a phone the side menu is a drawer
(the button at the top left): New thread, Models, Skills/agents/MCP, Cloud tasks, GitHub, This phone (Termux, screen
control, the phone's files) and Settings; it closes once something in it is chosen. The folder picker opens beside
the current project and makes a new folder there.

**Small models on a phone.** A local model under 1.5 GB (the phone's) is offered only read, edit, write, bash and the
phone tool (bash lists and searches files well enough), and the phone tool's description is short: what a thread
starts with is about half as long, so there is half as much to read before the first answer. And a new thread opens
while its first message is typed, so the model loads and reads that start meanwhile (on a phone, most of the wait);
threads are listed from their first message on. With the phone tool there, the prompt tells the model it runs on an
Android phone and uses the phone tool to act on it (an API model had "opened WhatsApp" by fetching whatsapp.com).

**Plugins without typing.** Plugins & skills lists the plugins with Remove, installs one from a git URL, GitHub's
owner/repo or name@marketplace, adds a plugin marketplace (Claude Code's format) and lists its plugins with Install:
what `/plugin` does from a thread, with buttons (on a phone, git is the app's own). `newal-code plugin list | install
NAME@MARKETPLACE | remove NAME | marketplace add|remove|list` does it from a terminal (`--project` for this project).

**NewAl's plugins (built in).** A marketplace comes with NewAl Code (`newal_code/market`, named `newal`): it is listed
first, its plugins install with one tap and nothing to download, and an update of NewAl Code brings theirs. Each does
its work with a program, not with instructions to a model, so it works the same with a phone's small model, an API
model or no model at all:

| Plugin | What it does |
|---|---|
| `system` | `/sysinfo`: the system, processor, memory, storage, graphics, battery, network, uptime, the developer tools found, and which GGUF sizes fit in the free memory now. `/disk [folder]`: the drives' free space and the biggest folders and files (disk use as `du` counts it: sparse files, hard links once). `/clean [--yes]`: pip, npm, yarn, Go, Gradle and Termux's package caches, temporary files untouched for a day, unfinished model downloads and the project's `__pycache__`-style caches, listed with their sizes; `--yes` deletes them, nothing else. `/ports [port]`: what listens on which port, reachable from the network or this device only, with its process. `/programs search\|install\|update\|remove\|list`: winget on Windows, pkg in Termux, Homebrew, apt/dnf/pacman/zypper (printing the `sudo` command where rights are missing). `/serve [folder] [--lan]`: a folder's pages at an address, served in the background and never cached (`--lan`: from the phone on the same Wi-Fi too); `/serve stop` asks the server to stop itself (it knows a token: no process is killed by its number). On Windows, macOS, Linux, the phone app and Termux; in Arabic when the app is. |
| `data` | `/csv <file> [column] [value column]`: a CSV/TSV (separator and encoding found by themselves, Arabic Windows files and Arabic digits included), Excel `.xlsx` (its first sheet) or JSON list at a glance: rows, each column's type, empty cells, distinct values, minimum, maximum, mean, median and total, the most common values; with a column, a chart as an SVG file beside the data (a text column's most common values, a number column's spread, or with a value column its total for each value: "sales by region"). |
| `arabic` | `/rtl [file or folder] [--apply]`: a site right-to-left for Arabic: `dir="rtl"` (and `lang="ar"` where none is set) on the page, and CSS's left and right in their logical forms (margin/padding/border/scroll `-inline-start`/`-end`, `inset-inline`, logical corner radii, `text-align: start`, `float`/`clear: inline-start`, a 4-value margin or padding whose sides differ split into `-block` + `-inline`) in CSS files, `<style>` blocks, `style=""` and JSX style objects; selectors and class names are left alone. It lists the changes; `--apply` writes them, and running it again finds nothing left. |
| `starters` | `/create <python\|web\|node\|flask\|fastapi> <name> [--ar]`: a new project made by a program (a Python package with a command line, an offline web page, a Node module, a Flask site, a FastAPI service), each with its README, `.gitignore` and a test that `/create` then runs; `--ar` makes it Arabic and right-to-left. |
| `guard` | With full access too: asks before an edit to a file that holds secrets (`.env`, keys, credential files) and before a force push, a hard reset, `git clean`, deleting a branch, discarding all changes, deleting the home folder or `.git`, dropping a database or publishing a package, and before a `git commit` or `git push` that would publish a token or a private key (also in `git add -A && git commit`: the changes the add would stage count). `/secrets [--staged\|--outgoing]` finds them by their exact formats (GitHub, AWS, Google/Gemini, OpenAI/DeepSeek, Anthropic, Hugging Face, Slack, Stripe, Telegram, private keys) and long passwords written into code, shown masked. |
| `format-on-edit` | After each edit, formats the file: gofmt, rustfmt and `dart format` always; ruff or black, prettier and clang-format where the project is set up for them (so a project that uses none is never reformatted); it tells the model when a file changed. |

A command whose front matter has `script: scripts/x.py` (relative to the plugin, or to the folder holding
`commands/`) is such a program: it runs at once in the project folder, without the model, and what it prints is the
reply (⚡ in the / menu; `newal-code exec "/sysinfo"` prints it too). The packaged app has no python of its own:
`newal-code --newal-python script.py` runs a plugin's Python program with the app's, and `${NEWAL_PYTHON}` in a
plugin's hooks is that command (`${NEWAL_PYTHON:-python3}` also works in Claude Code).

**GitHub and git on the phone.** Settings > GitHub (or GitHub in the side menu) connects an account with a token (GitHub's page for one opens
with the scopes NewAl Code needs; copied, it connects like an API key), and the ⬇ button beside Threads lists your
repositories to clone one as a project. The phone has no git, so NewAl Code Lite brings its own: `git` in the
agent's commands and in the review panel's Commit, Push and Create PR is NewAl Code's `minigit`, git's commands and
output over dulwich (git written in Python; the app's launcher answers to the name `git`). On the steps it is tested
with (init, status, add, commit, log, diff, branch, checkout, remote, push, show, rev-parse...) it prints what git
prints and makes the same commits, hash for hash; pushes to and clones from github.com use the connected token.
Pull requests go through GitHub's API. When both sides have new commits, `git pull` merges as git does (dulwich
alone only fast-forwards): a merge commit, or on a conflict git's markers in the files, `MERGE_HEAD` and the
`CONFLICT` lines; a commit is refused while a conflicted file still has a marker, `git merge --continue` finishes
the merge and `git merge --abort` puts things back (merge3, bundled, does the three-way merge).

**/sync.** One command for "get my branch and GitHub in step": `git pull` (your commits rebased on top where git
can; the phone's git merges), then `git push`, with the GitHub account connected in NewAl Code (the token for that
command only, never written to the repository's config; the phone's git uses it by itself). A branch GitHub does
not have yet is pushed and followed; one it has is followed, then pulled. It says what came in and what went out
("Pulled 1 new commit from origin/main. Pushed 2 commits to origin/main."), or what stops it in words, with the
way out: a conflict (the files, then `git add` and `git rebase --continue` / `git merge --continue`, or `--abort`),
work not committed on the phone, no remote, a refused sign-in, no connection. Tested against real remotes with the
computer's git and the phone's, conflicts included, and in the Android emulator (a commit from the computer and
one from the phone, merged and pushed by /sync on the phone).

**Termux.** Termux has a whole Linux: git, compilers, Node, any package, and your projects in its home. This phone (in
the side menu, or in Settings) > Connect Termux copies one command and opens Termux: pasted there, it installs Python and git from Termux's
packages when they are missing, puts NewAl Code in Termux, keeps the app's key in Termux's own files, adds "The
phone's model" (the GGUF the app runs, through the app's OpenAI-compatible `/v1`), allows the app to start it later
(Termux's `allow-external-apps`), and starts NewAl Code in Termux on 127.0.0.1:8791. The app then shows it ("Open the
Termux workspace"): the same interface, working in Termux's home with Termux's commands, with the phone's model or
an API, and the phone tool. The command's token works once, for 15 minutes; `newal-termux start | stop | update`
and `newal` work in Termux afterwards. With Termux's RUN_COMMAND permission allowed to the app ("Let this app start
it"), the app starts NewAl Code in Termux without opening Termux.

**The key.** NewAl Code's server answers only requests that carry its key, on a computer as on a phone: other programs
and web pages can reach 127.0.0.1 too (on a phone, any other app). The address the app opens carries the key (kept as
a cookie); scripts send it as a header (`Authorization: Bearer`, or `X-NewAl-Key`); requests that name another host
(a web page that rebinds its name to 127.0.0.1) are refused. The key is `~/.newal-code/server-key` (the app keeps
its own), and `newal-code app` prints the address with it.

## Speed

The speed test (`desktop/bench/`): six tasks in small Python projects, each checked afterwards by tests the model
never sees: fix a bug, add a command-line option, implement a function from its docstring, rename a function
everywhere, answer a question about the code, fix a crash from its traceback. Both programs run the same llama.cpp
build, the same model file and 4 threads on the same computer (4-core Xeon at 2.8 GHz, no GPU). Timed: from sending
the task to the final answer, including the automatic test run. Not timed: loading the model and reading the fixed
start of every request, which both programs do before the user types. A task gets at most 20 minutes.

**Now**, with the tests running with each change (see "How it works"):

| Model file (both programs use the same) | Current NewAl (project mode) | NewAl Code |
|---|---|---|
| Qwen3.5-4B Q4_K_M | 2865 s, 4 of 6 right | 488 s, 6 of 6: **5.9 times as fast** (83% less time) |
| Qwen3.5-4B Q4_K_M with MTP heads (both draft with them) | 2227 s, 6 of 6 | 341 s and 390 s, 6 of 6: **6.1 times as fast** (84% less time) |

| Task (MTP file) | Current NewAl | NewAl Code, run 1 | NewAl Code, run 2 |
|---|---|---|---|
| Fix a bug | 188 s | 29 s | 26 s |
| Add a command-line option | 839 s | 137 s | 182 s |
| Implement a function | 225 s | 62 s | 63 s |
| Rename across files | 524 s | 65 s | 64 s |
| Answer a question about the code | 67 s | 15 s | 16 s |
| Fix a crash from its traceback | 384 s | 32 s | 39 s |

The model calls for the six tasks went from 25-27 to 21-23 (the current NewAl: 76-80), and the tokens written
from 3.2k to 2.6-3.1k (11.6k). Drafting 5 tokens ahead instead of 3 was slower (632 s: more drafts rejected), so
it stays at 3; MTP makes writing 56% faster on these steps (6.2 to 9.6 tokens/s).

Drafting with n-grams from the conversation as well as MTP (the "both" setting) was slower too: 398 s and 413 s,
8.0-8.3 tokens/s, because on short agent steps most n-gram drafts are rejected. It stays off by default.

The goal was "at least 90% faster": 1.9 times the speed. NewAl Code is 5.9 to 6.1 times as fast on the same model
file (4.0 to 4.6 times before the tests ran with each change), and on 8-16 GB computers the current NewAl cannot run
its own default model at all. In time saved that is 83-84%. Read as "90% less time" (10 times as fast), it is not
there yet: that would be about 223 s with MTP, and most of what is left is the model writing (about 80% of the
time, at 9-10 tokens a second on this CPU).

**Before that change** (the first measurement), same model file, Qwen3.5-4B Q4_K_M (what the current NewAl runs on
an 8 GB computer):

| Task | Current NewAl (project mode) | NewAl Code, run 1 | NewAl Code, run 2 |
|---|---|---|---|
| Fix a bug | 297 s ✓ | 52 s ✓ | 47 s ✓ |
| Add a command-line option | 1200 s ✗ (time limit) | 304 s ✓ | 256 s ✓ |
| Implement a function | 302 s ✓ | 96 s ✓ | 87 s ✓ |
| Rename across files | 451 s ✗ | 118 s ✓ | 117 s ✓ |
| Answer a question about the code | 82 s ✓ | 27 s ✓ | 27 s ✓ |
| Fix a crash from its traceback | 534 s ✓ | 56 s ✓ | 58 s ✓ |
| **Total** | **2865 s, 4 of 6 right** | **652 s, 6 of 6** | **591 s, 6 of 6** |

**NewAl Code is 4.6 times as fast** (621 s on average against 2865 s: +361% speed, 78% less time), and it got all
six right where the current NewAl got four. It made 25 model calls instead of 80 and wrote 2.6-2.9k tokens instead
of 11.3k: on a CPU the time goes into writing, so writing less is what counts.

With the same model's MTP file (what NewAl Code downloads for 8 GB; the current NewAl drafts with the MTP heads too
when its file has them, so both draft here):

| Task | Current NewAl + MTP | NewAl Code + MTP | NewAl Code + MTP, 8 GB simulated |
|---|---|---|---|
| Fix a bug | 188 s ✓ | 43 s ✓ | 37 s ✓ |
| Add a command-line option | 839 s ✓ | 259 s ✓ | 233 s ✓ |
| Implement a function | 225 s ✓ | 85 s ✓ | 78 s ✓ |
| Rename across files | 524 s ✓ | 87 s ✓ | 80 s ✓ |
| Answer a question about the code | 67 s ✓ | 31 s ✓ | 20 s ✓ |
| Fix a crash from its traceback | 384 s ✓ | 45 s ✓ | 47 s ✓ |
| **Total** | **2227 s, 6 of 6 right** | **550 s, 6 of 6** | **495 s, 6 of 6** |

**4.0 times as fast** with MTP on both sides (+305% speed, 75% less time), and 4.5 times in the 8 GB simulation
(which also read each thread's project context ahead, see above). MTP made the current NewAl 1.29 times faster and
NewAl Code 1.13 times: NewAl Code writes fewer tokens (3.2k against 11.6k), so there is less for drafting to speed
up.

Where the time went (first measurement, the six tasks, same model file):

| | Current NewAl | NewAl Code |
|---|---|---|
| Model calls | 80 | 25 |
| Tokens read (not already cached) | 12,235 | 3,120-3,250 |
| Time reading them | 488 s | 105-110 s |
| Tokens written | 11,320 | 2,633-2,875 |
| Time writing them | 2,341 s | 474-529 s |

NewAl Code's first step acts instead of exploring, because the files the task names, their definitions, imports
and tests come with the task. It edits a few exact lines, the computer checks the work with the project's tests
instead of more model calls, and it thinks only before answering a question and after a failed check (see "How it
works").

## Command sandbox

In the read-only and auto-edit modes, commands run in the operating system's sandbox (`sandbox.py`): they can read
and run anything, but write only inside the project, the folders added to the thread and the temp folders (only
temp in read-only mode, where commands are then allowed without asking). Full-auto runs them as they are.

| System | How | Network off (`"sandbox_network": false`) |
|---|---|---|
| Linux | Landlock (kernel 5.13+), set in the command's process by a small launcher | ✓ (Landlock ABI 4+) |
| macOS | Seatbelt: `sandbox-exec` with a profile that denies writes outside those folders (as Codex does) | ✓ |
| Windows | The command runs at low integrity (the level browsers use for their sandboxes), in a job object that ends with it. Windows lets such a process write only where the mandatory label is low: the first sandboxed command in a folder labels it low once, and the temp folder is under `AppData\LocalLow`. | – |

Git Bash on Windows needed one more step: MSYS2 keeps its programs' shared state in kernel objects named after a hash
of the real folder of `msys-2.0.dll` (junctions and symbolic links are resolved first), and those made by a Git Bash
at normal integrity (a terminal left open) cannot be opened at low integrity (seen on the CI runner:
`NtCreateDirectoryObject ... 0xC0000022`); they should not be shared with a sandboxed command anyway. So sandboxed
commands run from NewAl Code's own copy of Git's `usr\bin` in `%LOCALAPPDATA%\NewAlCode\msys\<tag>`: hard links to
the same files (a hard link is a name of its own, and takes no space), or copies where Windows makes no link (Git in
`Program Files` for a user who cannot write there, another drive: for Git 2.55, 365 files and 96 MB, copied once),
with junctions to the rest of the Git folder.
It is made once for each set of Git files (a Git update makes a new one and removes the old, never following its
junctions). If it cannot be made, the command runs from Git's folder, and an MSYS2 access error offers to run it
outside the sandbox. The packaged app is its own launcher (`--newal-sandbox`).

The same tests run on real Windows, macOS and Linux machines in CI: a command writes in the project and not next to
it, programs from PATH work (on Windows with a Git Bash open alongside), the temp folder and files already there can
be changed, read-only mode writes nothing, cancelling stops everything a command started, and (Linux, macOS) no
connection gets out with the network off.

## Builds

`.github/workflows/newal-code.yml` runs on Windows x64, Linux x64, macOS Apple silicon and macOS Intel: the tests
(sandbox included), then the app (`newal_code.spec`: the window and the `newal-code` program side by side;
`NewAl Code.app` on macOS) with llama.cpp's release build for that system (`tools/fetch_llama.py`), then
`tools/smoke_newal_code.py`, which checks the built app the way a user runs it: it starts, finds its llama-server, runs
a command in the sandbox from inside the packaged app (driven by a scripted model), and answers with a real GGUF
through the bundled llama-server. Each build that passes is published in a pre-release `newal-code-b<N>`.
Build 8 was the first with all four: Windows x64 (37 MB zip), Linux x64 (52 MB), macOS Apple silicon and Intel
(38 MB each). Windows' check found that the packaged `newal-code.exe` wrote a pipe in the ANSI code page (the
packaged Python ignores `PYTHONIOENCODING`), so its JSON reached readers as broken UTF-8 and a box character could
stop it; standard output and error are UTF-8 now.

## Cloud tasks

Like Codex cloud and Claude Code on the web, a task can run on a GitHub Actions runner with the repository instead of
on this computer, and comes back as a diff (`cloud.py`):

- `newal-code cloud "task"` (or **Cloud** under the message box, then the **Cloud tasks** window) makes a commit on
  top of the checkout's HEAD (`--with-changes`: with its uncommitted changes) that adds `.newal/cloud/task.json`, and
  the workflow `.github/workflows/newal-code-cloud.yml` when the repository lacks it. It is pushed to a branch
  `newal-cloud/<id>`, without touching the checkout, and the push starts the workflow.
- On the runner, `newal-code cloud run` does the task in full-auto with the task's model: an API model whose key is a
  repository secret, or a local GGUF on the runner's CPU (Qwen3.5 4B by default, cached after the first task). The
  answer, the diff (without the task's own files) and the log are the run's artifact; unless `--no-push`, the result
  is also committed on the task branch.
- `newal-code cloud list | status ID | show ID | apply ID | pr ID | delete ID`, and the app's window, show the state,
  the answer and the diff, apply it here (three-way when files moved on) or open a pull request.

Measured on this repository: the task "Add a file docs/cloud-hello.md with one line: Hello from a NewAl Code cloud
task." ran on an ubuntu-latest runner; it downloaded NewAl Code and Qwen3.5 4B (2.8 GB) and made exactly that file,
in 78.8 s in all. The diff applies to the checkout here. The tests (`CloudTest`) run the whole round trip without
GitHub: a bare repository for git, a fake REST API (including the artifact link that refuses GitHub's token), a
scripted model.

## GitHub app

Like the Claude and Codex GitHub apps (`github_app.py`): `newal-code github install --pr` opens a pull request that
adds `.github/workflows/newal-code-github.yml`. Once it is merged, `@newal` in an issue, a pull request or a review
comment starts NewAl Code on a runner:

- **a question** gets an answer;
- **`@newal review`** on a pull request reviews its diff: findings on changed lines become inline comments, the rest go
  in the review's text (the repository variable `NEWAL_AUTO_REVIEW=true` reviews each new pull request);
- **a change**: on a pull request it is pushed to the pull request's branch (from a fork, the change is posted as a
  patch); on an issue it goes to a new branch with a pull request that closes the issue.

A comment on the issue shows that it is working (with a link to the run), then the answer. Only the repository's
owner, members and collaborators can start it (checked by the workflow and again by NewAl Code). The model is
`NEWAL_MODEL` or, as for cloud tasks, an API key in the secrets or a local model on the runner. The tests
(`GitHubAppTest`) drive it with simulated GitHub events against a fake API: a pull request made from an issue, a
review with an inline comment on the right line, a change pushed to a pull request's branch, and nothing at all for
people who may not start it or comments that do not mention it.

## Files

| Path | What |
|---|---|
| `newal_code/agent.py` | the loop: requests, tools, permissions, hooks, approvals, verify, goal, compaction, sub-agents |
| `newal_code/tools.py`, `patch.py` | the tools; Codex's patch format |
| `newal_code/permissions.py`, `hooks.py` | modes and rules; Claude Code's hook protocol |
| `newal_code/extensions.py`, `plugins.py`, `mcp.py` | AGENTS.md/CLAUDE.md, skills, commands, sub-agents; plugins; MCP client |
| `newal_code/context.py`, `prompts.py` | the first message's context; what the model is told |
| `newal_code/providers.py`, `models.py` | OpenAI-compatible, llama.cpp and Anthropic clients; the model registry, roles, teams |
| `newal_code/runtime.py`, `gguf.py`, `hardware.py`, `catalog.py` | llama-server, RAM planning, GGUF metadata, local models per tier |
| `newal_code/session.py` | threads (JSONL), checkpoints, diffs |
| `newal_code/service.py`, `server.py`, `ui/` | the app: sessions, slash commands, approvals, JSON API + events, the Codex-style web UI |
| `newal_code/tui.py`, `__main__.py`, `app.py` | terminal UI, command line, desktop window |
| `newal_code/benchmark.py`, `desktop/bench/` | the speed test; drivers for the current NewAl and NewAl Code |
| `newal_code/sandbox.py` | the command sandbox: Landlock, Seatbelt, low integrity |
| `newal_code/cloud.py`, `github_app.py` | cloud tasks; the GitHub app |
| `newal_code/connect.py`, `github.py` | an API in one tap; the GitHub account (token, repositories, clone, pull requests) |
| `newal_code/phone.py`, `minigit.py`, `termux.py` | the phone tool; git on the phone (dulwich); NewAl Code in Termux |
| `newal_code.spec`, `tools/fetch_llama.py`, `tools/smoke_newal_code.py` | the builds for Windows, Linux and macOS, and their check |
| `android-lite/` | NewAl Code Lite for Android: the app (`Setup` unpacks and starts NewAl Code; `PhoneServer`, `Phone`, `PhoneControlService`: the phone tool; `Termux`; `WebBridge`: what the interface may ask of the phone), `native/prepare.sh` (Python, llama-server, dulwich), `tests/phone_test.py` |
