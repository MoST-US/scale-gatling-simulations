---
paths:
  - "**/*.py"
  - "**/*.sh"
  - "**/*.sbatch"
  - "**/*.bat"
  - "**/*.ps1"
---

# Python and shell conventions

## Python (3.x, standard library only)
- No third-party imports and no `pip install`. The helpers must run on any Python 3:
  the Windows dev box and the Rocky Linux compute nodes.
- `run_queue.py` is a documented contract - keep all of it working:
  - one queued item is one Gatling execution, and runs are strictly sequential (FIFO);
  - an item is an `.env`-style file `queue/pending/<name>.env` holding only overrides,
    merged over `.env` and passed as `-D` JVM properties so `-D` wins;
  - state directories `queue/{pending,running,done,failed}`, append-only
    `results/runs.jsonl`, console output in `logs/<runId>.log`;
  - the queued run name is the experiment name: its report lands in
    `target/gatling/<name>-<yyyyMMddHHmmssSSS>/`, produced by
    `-Dgatling.core.outputDirectoryBaseName=<name>` (`gatling.runId` does not exist in
    Gatling 3.10.5); the resolved folder is what `results/runs.jsonl` records;
  - after every run the worker writes `used_config.txt` (header + the effective
    `KEY=VALUE` set, i.e. base `.env` merged with the item overrides) into that folder; a
    missing report folder is a warning, never a failed run;
  - a failed run moves to `queue/failed/` and the worker keeps going; the process only
    exits non-zero at the end (`--once`);
  - `mvnw.cmd` on Windows vs `sh ./mvnw` on POSIX, offline mode when `local-repo/`
    exists, `MAVEN_OPTS` default `-Xmx4g`;
  - keep the `RUN_QUEUE_CMD` environment override: it substitutes a fake launcher and is
    how queue state transitions get verified without a live endpoint. Use it when
    "testing" the queue and never remove it.
- Keep the CLI (`add`, `start [--once]`, `run <name> --dry-run`, `list [--json]`,
  `clear`) and its `--help` text in sync with README section 4.
- Never convert the queue into a SLURM job array and never run items concurrently.

## Shell / SLURM
- `set -euo pipefail` at the top of every script; resolve the root from
  `${BASH_SOURCE[0]}` (or `${SLURM_SUBMIT_DIR}` in batch scripts) and `cd` there; quote
  every path (`"$ROOT_DIR"`, not `$ROOT_DIR`).
- These scripts execute with `bash`/`sh` on the cluster: POSIX-compatible constructs, no
  Windows paths, no CRLF.
- Keep the `#SBATCH` headers, default node names and inline usage comments consistent
  with README section 4.2. Node selection stays overridable (`sbatch --nodelist=c07`,
  `./submit_run_queue.sh c07`) - never hardcode a new node inside the logic.
- Keep the `.env` presence checks and the `USER_RATE_LIMITS` pre-flight validation in
  `run-llm-workload.sh`: they exist so a typo fails before hours of wall time are spent.
  `EXPERIMENT_NAME` gets the same pre-flight treatment (whitespace-free token, because it
  travels as a system property to a forked JVM).
- Never launch `sbatch`, `run-llm-workload.sh` or `python run_queue.py start` as part of
  a task without explicit approval. `--dry-run`, `list` and `estimate_requests.py` are
  the safe verifications.

## Line endings
Every `.sh`, `.sbatch` and `.py` file must stay **LF**. The repository is developed on
Windows (`core.autocrlf=true`) and executed on Linux/SLURM, where a CRLF script fails with
`bad interpreter` / `\r: command not found`. `.gitattributes` pins `eol=lf` for these
paths: write LF when creating a file, and check with `git ls-files --eol -- <path>`
(`i/lf` is expected; a `w/crlf` on a script means the working copy needs renormalising).
