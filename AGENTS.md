# AGENTS.md

This repository is a **Gatling 3.10.5 (Java 11) load generator** for an
OpenAI-compatible LLM endpoint. Virtual users are subscribers (`basic`, `standard`,
`pro` x `low`/`high` usage) whose unit budget refills at the `USER_RATE_LIMITS`
triplet (units per minute); an attempt made without enough units is recorded as a
Gatling KO failure named `insufficient-units`, while HTTP errors stay separate.
Users arrive over `USER_RAMP_MINUTES`; measurement lasts `SIMULATION_MINUTES`.

The project is small: 7 Java sources (`src/main/java/simulations/**`), 2 Python
helpers, the shell/SLURM launchers, `README.md`, and two vendored Maven
repositories that are build inputs rather than source (`local-repo/` and the
accidental `local-repo -DskipTests/`).

## Authoritative rules

Project-specific rules live in `.clinerules/` (read the file that matches the work):

| File | Topic |
| --- | --- |
| `00-guardrails.md` | hard constraints - read first |
| `01-project-context.md` | what the simulation measures, layout, toolchain |
| `02-build-and-run.md` | canonical Maven/queue/SLURM commands |
| `03-configuration-contract.md` | `.env` precedence, units model, determinism |
| `04-java-conventions.md` | `src/main/java/**` |
| `05-python-and-shell.md` | `*.py`, `*.sh`, `*.sbatch`, `*.bat`, `*.ps1` |
| `06-docs-and-env.md` | `README.md`, `.env.example` |

## Non-negotiables

- Build and run **offline**: `sh ./mvnw -o -Dmaven.repo.local="$PWD/local-repo" ...`
  (Windows: `.\mvnw.cmd`). Never drop `-o`, never reach Maven Central, never use the
  generated `dependency-reduced-pom.xml` - `pom.xml` is the build file.
- `.env` is untracked, machine-specific runtime state (real host, prompt). Read
  `.env.example` for the schema; never edit, print or commit `.env`.
- Every run occupies the endpoint or a SLURM node for
  `USER_RAMP_MINUTES + SIMULATION_MINUTES` plus 10-15 minutes of overhead. Do not start
  `gatling:test`, `run-llm-workload.sh`, `python run_queue.py start` or `sbatch` without
  explicit approval; verify with `run_queue.py run <name> --dry-run`,
  `run_queue.py list` and `estimate_requests.py` instead.
- Runs must stay comparable: the seeded (`new Random(42)`) schedule generation and the
  meaning of existing `.env` keys are part of the experiment.
- Keep LF line endings in `*.sh`, `*.sbatch` and `*.py` (developed on Windows, executed
  on Linux; `.gitattributes` pins this).
- Never commit `queue/`, `results/`, `logs/`, `target/`, `.env`, `*.out`, `*.err`.

## Cline setup

- `.clineignore` keeps ~2,780 vendored Maven files out of context. It is a context
  filter, not access control: explicit `@` mentions and shell commands still reach
  ignored paths.
- `.clinerules/hooks/PreToolUse` is an optional guard that **enforces** `.clineignore`
  by cancelling matching `read_files` / `editor` / `apply_patch` / `run_commands` calls.
  It needs `jq` and `git` on `PATH`, the executable bit
  (`chmod +x .clinerules/hooks/PreToolUse` on Linux/WSL), and **Enable Hooks** in
  Cline's feature settings.
