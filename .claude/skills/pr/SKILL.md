---
name: pr
description: Open a merge request for the current branch on the GitLab remote with .gitlab/merge_request_templates/Default.md filled in from the real diff and the suites that were actually run. User-triggered only.
disable-model-invocation: true
---

# /pr — open the merge request

Arguments: `$ARGUMENTS` — optional target branch. Default `claude/jarvis-ai-assistant-nbqf1p`, this checkout's integration branch (`dev` is the upstream model the template describes).

The remote is GitLab (`git remote -v` — `https://gitlab.home.example/acme/jarvis.git`, project id 3). There is no `glab` on this host; the API does the work with a personal or project access token that has the `api` scope in `GITLAB_TOKEN` (ask the operator for it if unset — never invent one, never put it in a file). `docs/ci.md` says what the pipeline will run on the merge request.

## 1. Preconditions

- Working tree clean; branch pushed (`git push -u origin HEAD` if not — the push needs the same token or a credential helper).
- Know what was run on this exact HEAD. If `/gate` has not been run since the last commit, run it first — the template's checkboxes are ticked only for suites that actually ran here.

## 2. Title

Sentence-style, no conventional-commit prefix, the behaviour change from the user's point of view, ≤ 72 characters — the same register as this repo's commit subjects (`Close three ways a coding job could reach the host`).

## 3. Description — the template, section by section, no placeholders left

- **What changed, and why** — the behaviour, not the diff: what was wrong or missing before.
- **How it was checked** — tick `make test` / `make test-web` only if they ran; name what could not be run here (hardware, Docker daemon, Playwright browsers) and why.
- **Anything a reviewer should look at twice** — a security boundary, a wire contract another client parses (console, Android, desktop), a default that changes for existing installs, a claim only real hardware can settle. If none, say so.
- **Docs** — whether `docs/verification.md` / `DEVIATIONS.md` moved; run `/claims` if unsure.

End the description with `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and the session link if there is one.

## 4. Create

```bash
API=https://gitlab.home.example/api/v4/projects/3
python3 - "<title>" <description-file> "<base>" <<'PY'
import json, os, sys, urllib.request
title, path, base = sys.argv[1:4]
head = os.popen("git branch --show-current").read().strip()
body = json.dumps({"source_branch": head, "target_branch": base, "title": title,
                   "description": open(path).read(), "remove_source_branch": False}).encode()
req = urllib.request.Request(os.environ["API"] + "/merge_requests", data=body, method="POST",
                             headers={"PRIVATE-TOKEN": os.environ["GITLAB_TOKEN"], "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req))["web_url"])
PY
```

Print the URL. An existing open merge request for the branch answers 409 — then update it instead (`PUT /merge_requests/<iid>` with the same fields).

A push to a branch whose merge request is open starts its pipeline. On the deploy host `make ci-watch` follows it without a token (`docs/ci.md`, "Whether a push deployed, from the deploy host"): it ends when the stack runs the pushed commit, when a job failed on this host's runner, or at its limit. Do not write a wait by hand.
