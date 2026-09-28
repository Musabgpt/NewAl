# NewAl as a coding-agent platform

The user's direction (2026-09-28): NewAl becomes a platform **for writing and building code only**, working the way
Claude Code and Codex work, that runs **any model** — one or many, big or small, local or over an API — where every
model is given, by the user, **a name, a role, a specialty, a way of working and its relation to the other models**.
Tools, add-ons, models and APIs plug in; the system keeps learning from what it verifiably did.

This document is the plan. Every step ships with tests and, where it claims speed or quality, a measurement on the
laptop (docs/musabai.md, rule 1).

## What the open-source agents already proved (ideas taken, not code)

| Project | Idea | What NewAl takes |
|---|---|---|
| Claude Code | Subagents are Markdown files (name, description, tools, model, prompt); the main agent delegates with a Task tool and gets a report back; permission modes; hooks; CLAUDE.md; todo list; compaction; plan mode | Agent files, `delegate`, permission modes, hooks, project memory file, todo (done), compaction |
| Codex (open source) | Model providers by `base_url` + key; profiles; `AGENTS.md`; sandbox modes `read-only` / `workspace-write` / `danger-full-access`; MCP servers with allow/deny tool lists; subagents with their own config | Provider entries, the three permission levels, AGENTS.md (done), MCP allow/deny |
| OpenCode | `primary` and `subagent` agents in JSON or `.opencode/agents/*.md`; `model` as `provider/model`; `permission` allow/ask/deny; `steps`; which subagents an agent may call (`permission.task`) | Agent modes, per-agent steps, the "may call" relation |
| Roo Code | Modes with `roleDefinition`, `whenToUse`, tool groups `read/edit/command/mcp` with `fileRegex`; a model remembered per mode; Orchestrator delegates with `new_task` using `whenToUse` | `when_to_use` drives delegation; tool groups; file limits |
| Aider | Architect model plans, editor model writes the edits; repo map; edit formats per model; auto-commit, lint and test after edits | Planner → coder pairs; the repo map (done); per-model edit settings |
| Cline | Plan and Act modes with a different model each; checkpoints before acting | Plan-first (done) with its own model; checkpoints (done) |
| Goose | Lead/worker switching by turn count was removed in favour of a planning mode with a dedicated planner model | Keep relations simple: explicit roles, not automatic switching |
| OpenHands | Event stream of actions and observations; condensers that shrink old history; keyword-triggered micro-agents | A step log per session; history condensing; skills (done) |

## What NewAl already has

Project mode works like Claude Code (evidence first, checklist, notes, exact edits, run → fix → run, background
commands, honest report); MCP add-ons; skills; lessons and verified procedures; checkpoints and undo; an
OpenAI-compatible API; the quality test; llama.cpp tuned for a CPU (shared prompt start read in advance, KV cache on
disk, helpers never push the brain out of RAM). What is missing is the platform layer (models and agents as data),
relations between agents, history condensing, and a coding-only UI.

## The data model

```
Provider ── serves ──> Model ── used by ──> Agent ── may call / reviewed by ──> Agent
                                              │
                                  tools · permissions · way of working · skills
```

### Models (`~/NewAl/data/models.json`)

Any model, any number. A local file runs on NewAl's llama.cpp; anything else is an OpenAI-compatible endpoint
(Ollama, LM Studio, vLLM, llama.cpp elsewhere on the network, OpenRouter, OpenAI, DeepSeek...) or Anthropic.

```json
{"id": "qwen36", "name": "Qwen3.6-35B-A3B", "provider": "local",
 "file": "Qwen3.6-35B-A3B-MTP-UD-IQ3_S.gguf", "context": 32768, "threads": 3, "mtp": true, "vision": true,
 "temperature": 0.3, "max_tokens": 4096, "thinking": "off"}
{"id": "gpu-box", "name": "Qwen3-Coder on the desktop", "provider": "openai",
 "base_url": "http://192.168.1.20:8080/v1", "model": "qwen3-coder", "api_key_env": ""}
```

Shown per model: size and RAM, whether it fits next to what is loaded, and its measured reading/writing speed and
quality-test score on this computer (tools/bench_brain.py, the quality test). The current catalog becomes the
default entries.

### Agents (`~/NewAl/agents/*.md`, and `<project>/.newal/agents/*.md` for one project)

An agent is a model plus how it is used. Two agents may share one model with different roles.

```markdown
---
name: مختبِر
when_to_use: after code changes, to write and run tests and report what fails
role: tester
specialty: python, pytest
model: qwen36
tools: read, run, edit:tests/**
permission: workspace-write
may_call:
reviewed_by:
steps: 30
---
Write tests for what changed, run them, and report the failures with the exact output. Never change the code
under test: say what is wrong and where.
```

Fields: `name`, `when_to_use` (what the lead reads to choose it), `role` (lead, planner, coder, tester, reviewer,
debugger, explorer or any word), `specialty`, `model` (a model id, or `default`), `tools` (groups `read`, `edit`,
`run`, `web`, `git`, `mcp:<server>`, with path limits like `edit:tests/**`), `permission` (`read-only`,
`workspace-write`, `full`), `may_call` (agents it can delegate to), `reviewed_by` (an agent that checks its work
before it counts as done), `steps`, and the body: its way of working. Shipped agents: lead (works like Claude Code
and calls the others), planner, coder, tester, reviewer, explorer (read-only, fast, a small model when one scores
well enough).

### Relations

- **Delegation:** an agent with `may_call` gets a `delegate(agent, task)` tool. The sub-agent works in its own
  context with its own model, prompt and tools, and returns a short report (Claude Code's Task, Roo's `new_task`).
  This also keeps the lead's context small, which matters with 32k tokens.
- **Review:** `reviewed_by` sends the diff and the report to the reviewer; its findings go back to the author.
- **Pipelines** (a team file): ordered stages, e.g. planner → coder → tester → reviewer, each stage's report handed to
  the next (Aider's architect/editor, Cline's plan/act, Goose's planner).
- **Scheduling on this hardware:** local models run one at a time within the RAM budget (the brain stays loaded when
  it can); agents on API or network models may run in parallel. Several local models "together" on one laptop CPU
  are slower, not faster (docs/musabai.md #14).

### Tools, add-ons, APIs

Built-in coding tools (project mode's set), MCP servers with per-agent allow/deny lists, API tools (an OpenAPI
document or a URL template becomes a tool), and hooks: commands run before or after a tool (format after an edit,
refuse a command, notify when done).

### Permissions

Codex's three levels, per agent and per session: `read-only` (look and search), `workspace-write` (edit inside the
project; build/test commands run, anything else asks), `full` (nothing asks). Destructive and outward actions
(delete, push, publish, install system-wide) always show what they will do.

### Context

Repo map, AGENTS.md/CLAUDE.md, skills by keyword, sub-agents with their own context, and condensing: when a session
nears the model's context, older steps are summarised (by the model itself or a small one) and the summary replaces
them, the way Claude Code compacts. The prompt start of each agent is read in advance and saved to disk (instant
start instead of 3-6 minutes on the laptop).

### Learning from experience

Kept only when verified: lessons from a failure that tests later passed, procedures of verified tasks, a map per
project, per-model scores per role. A model or adapter replaces another only when the coding quality test says it
is not worse (evals.accept). Fine-tuning (LoRA) runs on Kaggle, gated the same way.

## The UI (Claude Code desktop / Codex)

- **Left:** sessions grouped by project; new session; open folder.
- **Middle:** the transcript: the request, the agent's notes, tool cards (read, edit with an inline diff, run with
  live output), sub-agents as collapsible sections, the checklist pinned while it runs, approvals inline (once /
  always), the final report.
- **Right panes:** all changes of the session (diff, keep/revert per file), terminal (runs and background jobs),
  files.
- **Composer:** agent and model pickers, permission level, plan first, attachments, slash commands (`/compact`,
  `/review`, `/test`, `/agents`, `/models`, `/undo`).
- **Settings:** Models, Agents, Teams, Tools and MCP, Skills, Hooks, Permissions.
- Chat, deep research, pictures, the phone page and Kaggle School leave the main UI (coding only).

## Phases

1. **Engine:** models registry (local files and OpenAI-compatible endpoints, per-model settings); agent files with
   roles, tools and permissions; `delegate` between agents; permission levels; history condensing; saved prompt
   starts. Checked with unit tests and a two-agent task on the laptop (lead + tester) against one agent.
   *Done so far:* the models registry (`models.py`, `engine.Remote`), agent files with tools, path limits and the
   three permission levels (`agents.py`, six shipped agents), `delegate` (`agent.py`), the caller's reading parked on
   disk while another agent works, the Models/Agents screen and the agent picker. On the laptop the lead fixed the
   checkout bug, gave the test to the tester (8 steps, its first step 52 s thanks to the shared prompt start) and
   checked the report itself. Parking the lead's reading while the tester worked cut the lead's next step from 4388
   tokens re-read (215 s) to 292 (38 s); the whole task went from 1184 s to 872 s.
   *Next:* history condensing, prompt starts saved to disk, review gates.
2. **UI:** the coding UI above, beside the current one until it does everything, then instead of it; the non-coding
   features removed.
3. **Teams and extension:** pipelines, review gates, hooks, API tools, a project's own agents.
4. **Learning:** a coding quality test built from real tasks (like the checkout bug), scores per model and role,
   automatic suggestions of which model fits which role on this computer.
