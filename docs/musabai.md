# MusabAI: the vision, and where NewAl stands

MusabAI is not one model. It is a personal local/cloud AI system: models + agents + memory + tools + skills + real
computer execution + verification + continuous evaluation + an evidence-based learning loop. **The model can change;
MusabAI remains.** NewAl (desktop in `desktop/`, Android + Termux in `app/`) is its first body.

Rules every change follows:

1. **Evidence over claims.** A task is done when the computer shows it (files exist, outputs show the result), never
   because the model says so. The same goes for our own work: every speed or quality claim is measured, and says
   what was measured and what was not.
2. **Speed is a feature of the whole system.** On the user's laptop (i7-8650U, 4 cores, 24 GB) the brain reads ~24
   tokens/s and writes 5–6 (`desktop/tools/bench_brain.py`), so the biggest wins come from not re-reading (stable
   prompt prefixes, KV cache on disk, no helper model that pushes the brain out of RAM), fewer model rounds and
   shorter tool results, then from the model file itself.
3. **Keep what the user owns outside the model.** Memory, procedures, lessons, skills, logs and datasets are plain
   files in NewAl's data folder and survive any model change.

Status: ✅ in place · 🟡 partly · ❌ not yet. Paths are under `desktop/newal/` unless noted.

| # | Vision item | Status | Where it lives now | Next step |
|---|---|---|---|---|
| 1 | Agentic execution (plan → act → check → adapt) | 🟡 | Goal mode (`agent.py` `_goal`: plan, tool steps, evidence check, continue until done), project mode (`workspace.py`, like Codex), background tasks (`tasks.py`) | Re-plan explicitly after two failed steps; ask the user only when a step needs a decision or a secret |
| 2 | Reasoning that proves and computes | 🟡 | Code is run and judged (`_code`), asserts replace the judge, chat keeps its tools for calculations | A math/science skill that always checks a number by running it |
| 3 | Real-time streaming | 🟡 | Model tokens stream; command output streams into the chat while it runs; Android writes files while they are generated (`docs/termux-agent.md`) | Desktop: write code into files while it is generated |
| 4 | Self-verification | ✅ | Claim check (no "done" without a tool), evidence-carrying goal check, code run + judge, project review | Independent second answer (self-consistency) for 💭 math/analysis only |
| 5 | Deep computer integration | 🟡 | PowerShell/cmd/WSL, files, processes, Git, VS Code, Windows tools, phone + Termux (`app/`) | Permission levels Read → Write → Execute → Admin |
| 6 | Tool layer, automatic tool choice | ✅ | `tools.py` (closed-world resolution: a call maps to a real tool or is refused), GitHub/GitLab/Kaggle/Drive (`connectors.py`), MCP client (`mcp.py`), headless browser | Databases and Google Cloud through MCP servers |
| 7 | Dynamic skills | 🟡 | `skills.py` (built-in and user playbooks matched to the request) | Offer to turn a procedure used several times into a skill |
| 8 | Layered long-term memory | 🟡 | User (about me, memories), project (projects + file index), semantic (RAG in `memory.py`), lessons (`lessons.py`), **procedural** (`procedures.py`: steps of verified goals), tool log (`audit.py`) | Episodic record per goal (step, error, fix, test) built from the tool log |
| 9 | Learning only from verified execution | 🟡 | `training.py` logs every answer with verified/run/judge; Kaggle School results come back as verified examples and lessons | Dataset export that keeps only verified, deduplicated, reproducible trajectories |
| 10 | Kaggle as training/experiment space | 🟡 | `school.py`: hard tasks solved weekly on Kaggle's free GPUs | Fine-tuning run on Kaggle, gated by #11 before use |
| 11 | Continuous evaluation | 🟡 | `evals.py` («🧪 اختبار الجودة» in settings): 12 real tasks (Arabic, dialect, math, files/tools, code, honesty) run end to end and scored by evidence, each run kept with the brain's file name; `accept()` rejects a change that makes any category worse. `diagnose.py` checks the system and engine | More cases per category; run it automatically before switching the brain's file or using a trained adapter |
| 12 | Hierarchical context | ✅ | `_context`: fixed system prompt → project instructions → relevant memories/files/chats → one worked example → skills → attachments | — |
| 13 | Full project understanding | 🟡 | Project mode explores with list/read/search every time | A project map (entry points, dependencies, tests) saved per project and refreshed on change |
| 14 | Multi-agent | 🟡 | Planner, executor, checker and reviewer roles run on the one brain; read-only tool calls run in parallel | Parallel model agents make one CPU slower, not faster: only with a second machine or a GPU |
| 15 | Compute allocation | 🟡 | Thinking off by default, budgets per step, 💭 for full thinking; embedding router picks the route | Measure a small model for simple chat against the brain before using it (#11) |
| 16 | Model routing | 🟡 | `router.py` (examples by embedding) + `catalog.py` roles | Routes per model once #11 can show each model's scores |
| 17 | Local first, cloud when needed | ✅ | Everything runs locally; Kaggle for heavy work | — |
| 18 | Security and permissions | 🟡 | Approval for risky tools (allow always per turn), Windows Sandbox for risky code (`sandbox.py`), secrets masked in settings and logs, tool log (`audit.py`) | Permission levels, network permission per tool |
| 19 | Rollback and checkpoints | 🟡 | Project mode backs up every file before the first change and can undo the whole turn | Git checkpoint before goal-mode changes inside a repository |
| 20 | Observability | 🟡 | Live status, tool boxes with live output, speed report, tool log in «🗂 الذاكرة» | Task view: steps done/left, RAM and CPU while a goal runs |
| 21 | Controlled self-improvement | 🟡 | Lessons from fixed mistakes, verified procedures, Kaggle School | Every improvement is kept only if #11 confirms it |
| 22 | Dry run | 🟡 | Project «plan first», risky code tried in the sandbox first | Preview of bulk changes (which files, what changes, how to roll back) before running |
| 23 | Goal-oriented | ✅ | Goal mode | — |
| 24 | Arabic and English first-class | 🟡 | Arabic UI and answers, dialect-aware search queries, Arabic in tests | Arabic section in the benchmark (#11), so a faster model file is never adopted if Arabic gets worse |
| 25 | Knowledge outside the model | ✅ | Memory, index, lessons, procedures, skills, logs: files in NewAl's data folder | — |

## Order of work

Each step is shipped only with tests, and with a measurement where it claims speed or quality.

1. **Fewer and cheaper model rounds** (done): chat without needless tool rounds, the goal plan and checks inside the
   same conversation (llama.cpp continues instead of re-reading), shorter tool results.
2. **Procedural memory, tool log, live command output** (done).
3. **Built-in benchmark (#11)** (first version done): the gate for everything below, and the way to pick the brain's
   file on the user's own laptop by speed *and* Arabic/tool accuracy. On the laptop (2026-09-27), four rounds of
   test → fix → test took it from 10/12 in 1391 s to 12/12 in 327 s: a refused delete that «run without asking» let
   through, helper models pushing the brain out of RAM, goals re-reading their own prompt, a project skill that turned
   a function into a package, and made-up package names sent to PyPI (details in the commit log).
4. **The brain's file**: CPU speed depends on the quantization types inside the file, not its label (Unsloth's
   UD-IQ3_S keeps most experts as IQ2_S + IQ4_XS; UD-Q3_K_M uses IQ3_XXS + IQ4_XS). On a small model of the same
   architecture (4 threads, AVX2 only, not the user's laptop) K-quant expert mixes read and wrote faster than the IQ
   mixes, but the spread between runs was large. Candidates are compared with #3 on the laptop before switching.
   Measured on the laptop itself (UD-IQ3_S with MTP, `tools/bench_brain.py`): reading 23.6 tokens/s with 8 threads
   (21.1 with 4); writing code 5.7–5.9 and Arabic 4.9–5.6 with MTP, 5.1–5.4 without. 3, 4 or 6 writing threads
   differ less than two runs of the same setting, so the file (not the threads) is where writing speed can come from.
5. Permission levels (#5, #18), git checkpoints (#19), project map (#13), streaming code into files (#3).
