# Orchestrator Mode Rules

When `.orchestrator/current-feature.json` exists with a non-null `feature_id`, you are in **orchestrated mode**.

## Rules
1. Read `.orchestrator/current-feature.json` to get the feature context
2. Implement the feature as described in the prompt
3. **Keep changes scoped** — only modify files relevant to the current feature
4. **No side quests** — don't fix unrelated bugs or refactor other code
5. After completion, use the `orchestrator-signal` skill to write a signal file
6. **Always signal** — even on errors, so the orchestrator doesn't timeout
7. **Test locally** when possible before signaling `done`

## Context
- An external orchestrator sends you prompts and waits for your signal
- After signaling, the orchestrator runs tests to verify
- If tests fail, you'll get a follow-up fix prompt
