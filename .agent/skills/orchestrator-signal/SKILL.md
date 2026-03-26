---
name: orchestrator-signal
description: Signal implementation status to the agents-orchestration system via JSON files
---

# Orchestrator Signal Skill

When a `.orchestrator/current-feature.json` file exists in this project, you're in **orchestrated mode** — an external orchestrator sent you this task. Use this skill to signal back when done.

## When to Use

After completing a feature implementation that was prompted by the orchestrator.

## How to Signal

Create a JSON file in `.orchestrator/signals/`:

```bash
# 1. Read what feature you're working on
cat .orchestrator/current-feature.json

# 2. Do your implementation work...

# 3. Signal completion
mkdir -p .orchestrator/signals
cat > .orchestrator/signals/FEATURE_ID_$(date +%Y%m%d_%H%M%S).json << 'EOF'
{
  "feature_id": "FEATURE_ID_HERE",
  "status": "done",
  "timestamp": "ISO_TIMESTAMP",
  "files_changed": ["path/to/changed/file.py"],
  "summary": "What was implemented",
  "errors": []
}
EOF
```

## Status Values
- `done` — Ready for testing
- `error` — Failed, include details in `errors[]`
- `needs-review` — Done but wants human review

## Rules
- **Always signal**, even on errors (prevents orchestrator timeout)
- **One signal per round** — only write one file per implementation cycle
- **List all changed files** in `files_changed`
- **Be specific in errors** — the orchestrator uses them to compose fix prompts
