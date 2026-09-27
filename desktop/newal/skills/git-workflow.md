---
name: Git وGitHub
description: نسخ المشاريع ورفع التعديلات وفتح issues بطريقة آمنة
triggers: git, github, gitlab, جيتهب, جيت, مستودع, repo, commit, push, clone, كلون, ارفع مشروع, pull request, issue
---
- Clone into the workspace with git_clone; work inside that folder.
- Before committing: `git status` and `git diff --stat` so the user sees what changes.
- Commit messages: one clear line in English describing the change.
- Never force-push, never rewrite history, never commit secrets (.env, tokens, keys).
- New project on GitHub: github_create_repo, then `git init`, `git add -A`, `git commit -m ...`, `git remote add origin <url>`, git_push.
