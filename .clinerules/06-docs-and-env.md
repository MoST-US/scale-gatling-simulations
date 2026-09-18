---
paths:
  - "**/*.md"
  - ".env.example"
---

# Documentation and .env.example

## README.md is the contract
- Keep the numbered sections and their anchors: the document links to itself (for example
  `[Section 4](#4-run-a-queue-of-executions-run_queuepy)`), so renaming a heading means
  fixing the links.
- Match the existing voice: concise, second person, no emoji, one idea per bullet, fenced
  blocks labelled `bash`, `powershell`, `dotenv` or `text`.
- Keep the "one queued run is one Gatling execution" and "queue one run per
  `USER_RATE_LIMITS` profile" framing; the multi-case sweep no longer exists.
- Facts documented today must stay true: the wall-clock budget
  (`USER_RAMP_MINUTES + SIMULATION_MINUTES` plus 10-15 minutes), `once` vs `watch`,
  requeueing of interrupted items, why a SLURM job array is wrong, `queue/` and `.env`
  being git-ignored runtime state, and the offline Maven notes (`local-repo` with `-o`,
  never `dependency-reduced-pom.xml`).
- When a parameter changes, update the README settings block, `.env.example` and
  `SimulationConfig` in the same change (see `03-configuration-contract.md`).
- The "Workload Timing (Legacy)" heading has no content; tidy it only when the task is
  about documentation.

## .env.example
- It is the tracked schema for `.env`: every key
  `SimulationConfig`/`LLMWorkloadSimulation` reads appears there with a short inline
  comment, and nothing else.
- Values must be paste-safe and secret-free - no real credentials, no `user@host` from
  `setup_tunnels.*`. Generic placeholders only.
- Keep the example values consistent with the Java defaults in `SimulationConfig.load()`.

## AGENTS.md and .clinerules
- `AGENTS.md` is the thin cross-tool entry point; `.clinerules/*.md` holds the detail.
  When a rule changes materially, keep `AGENTS.md`'s non-negotiables and the file table
  accurate, but do not duplicate rule bodies there: every rule file is injected into
  every task and duplication is paid for twice.
- Update this file set when `.env.example`, the queue contract or the SLURM workflow
  changes, so the rules never contradict the README.
