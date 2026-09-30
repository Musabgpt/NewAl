---
description: Find leaked secrets (tokens, API keys, private keys) in the project, or in what a commit or push would publish
argument-hint: "[--staged | --outgoing]"
script: hooks/secretscan.py
---
Run `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/secretscan.py" $ARGUMENTS` and show its output as it is.
