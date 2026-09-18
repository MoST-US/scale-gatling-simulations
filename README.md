# LLM Workload Simulation

## Purpose

This project measures the behavior of an OpenAI-compatible LLM under a
controlled population of simulated users. It gradually introduces the target
number of users, waits until they are all present, and then measures workload
for `SIMULATION_MINUTES`.

Each user has a subscription tier (`basic`, `standard`, or `pro`) and a usage
profile (`low` or `high`). Requests consume units. Once a user has exhausted
their quota, the next attempted request is recorded as a Gatling failure named
`insufficient-units`, and that user stops. HTTP errors remain separate from
quota failures.

Each execution applies one per-user entitlement profile: `USER_RATE_LIMITS`, a
`basic,standard,pro` triplet of units per minute read from `.env` and passed to
the simulation as a JVM property.

To compare several provisioning profiles, enqueue one run per profile; queueing
runs with different `USER_RATE_LIMITS` replaces the former multi-case "sweep"
(see [Section 4](#4-run-a-queue-of-executions-run_queuepy)).

## Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd scale-gatling-simulations
```

### 2. Configure the environment

Copy `.env.example` to `.env` and edit the values:

```bash
cp .env.example .env
```

Important settings include:

```dotenv
LLM_URL=gpu06:9000
ENDPOINT_PATH=/v1/completions
MODELS_ENDPOINT=/v1/models
EXPERIMENT_NAME=llm-workload
SIMULATION_MINUTES=60
USER_RAMP_MINUTES=30
FIRST_REQUEST_BATCH_SIZE=500
FIRST_REQUEST_TURN_INTERVAL_SECONDS=2
```

`LLM_URL` may be a host and port or a complete URL. The launcher normalizes a
bare host and port to `http://...`. The model is discovered automatically from
`MODELS_ENDPOINT`.

The per-user entitlement is a single triplet:

```dotenv
USER_RATE_LIMITS=4,8,10
```

It is `basic,standard,pro` units per minute.
Units are not a lifetime quota. In the simulation they refill continuously for
each user at the configured rate. A request consumes `UNITS_PER_REQUEST` units, and
`MAX_ACCUMULATED_REQUESTS` bounds the per-user burst capacity. The example uses
`UNITS_PER_REQUEST=100` so integer unit budgets can represent fractions of a
request per minute. A user that lacks units records `insufficient-units` for
that attempt but remains in the simulation for later requests.

Each execution writes its Gatling report to
`target/gatling/<EXPERIMENT_NAME>-<timestamp>/`, and that folder also receives a
`used_config.txt` with the configuration the run actually used. `EXPERIMENT_NAME`
(default `llm-workload`) is used by `run-llm-workload.sh` and
`submit_llm_workload.sbatch`; queued runs use the queued run name instead (see
[Section 4](#4-run-a-queue-of-executions-run_queuepy)). Keep `EXPERIMENT_NAME` free
of whitespace (`[A-Za-z0-9][A-Za-z0-9._-]*`).

The same reproducible stochastic demand schedule is generated for every run, so
only `USER_RATE_LIMITS` (and any other parameter you override) changes between
them. `estimate_requests.py` reports the expected request volume for a given
`USER_RATE_LIMITS` (text or `--json` report) without running the simulation.

### 3. Run the simulation

On Linux, macOS, or WSL:

```bash
chmod u+x run-llm-workload.sh
./run-llm-workload.sh
```

On Windows PowerShell, use Maven directly if the dependencies are available:

```powershell
./mvnw.cmd gatling:test `
	"-Dgatling.simulationClass=simulations.LLMWorkloadSimulation"
```

On an isolated Rocky Linux machine, the launcher uses the bundled offline
repository:

```bash
sh ./mvnw -o \
	-Dmaven.repo.local="$PWD/local-repo" \
	gatling:test \
	-Dgatling.simulationClass=simulations.LLMWorkloadSimulation
```

Each execution writes its report to `target/gatling/<EXPERIMENT_NAME>-<timestamp>/`,
next to a `used_config.txt` recording the effective configuration of that run.

For a background SLURM job, submit from the repository directory:

```bash
chmod u+x run-llm-workload.sh submit_llm_workload.sbatch submit_llm_workload.sh
sbatch submit_llm_workload.sbatch
```

The default SLURM node is configured in `submit_llm_workload.sbatch`. Override
it for one submission with:

```bash
sbatch --nodelist=c07 submit_llm_workload.sbatch
```

or use the helper:

```bash
./submit_llm_workload.sh c07
```

SLURM writes logs to `gatling-llm-workload-<job-id>.out` and
`gatling-llm-workload-<job-id>.err`.

> For running arbitrary experiments as a queue of simulations (each with its own
> parameters), see [Section 4](#4-run-a-queue-of-executions-run_queuepy) — that
> is the intended way to run queued workloads on SLURM.

### 4. Run a queue of executions (`run_queue.py`)

`run_queue.py` runs several executions back-to-back (strictly one at a time,
FIFO) so you never have to watch for one execution to end before starting the
next — enqueue as many runs as you want, each with different parameters, and the
worker picks them up automatically.

**One queued run is one Gatling execution.** A queued "run" is a small
`.env`-style file in `queue/pending/<name>.env` holding only the parameters that
differ from the base `.env`. The worker merges the item's overrides over `.env`
and passes every merged key as a `-D` JVM property (which takes precedence), so
no Java code changes are needed and *any* parameter (`TOTAL_USERS`,
`SIMULATION_MINUTES`, `USER_RATE_LIMITS`, `LLM_URL`, `LLM_PROMPT`, ...) can differ
per run.

To compare provisioning profiles, enqueue one run per `USER_RATE_LIMITS` value.
This is how the former multi-case "sweep" is reproduced — for example, the three
under/over/fine-tuned profiles become three queue items:

```bash
python run_queue.py add under --set USER_RATE_LIMITS=25,50,75
python run_queue.py add over  --set USER_RATE_LIMITS=10000,10000,10000
python run_queue.py add tuned --set USER_RATE_LIMITS=50,100,150
```

A run that exits non-zero is moved to `queue/failed/`, and the worker continues
with the next item (mirroring the `set -e` behavior of `run-llm-workload.sh` for
that single run).

Commands (run from the repository directory):

```bash
python run_queue.py add <name> [--set KEY=VALUE ...] [--env-file PATH]
    Enqueue a run named <name> (one execution).
    --set overrides a parameter (repeatable); --env-file imports overrides from
    an .env-style file.
python run_queue.py start [--once]
    Process the queue. --once drains what is pending now, then exits with
    code 0 (or 1 if any run failed). Without --once it keeps polling for
    newly added runs until Ctrl-C.
python run_queue.py run <name> --dry-run
    Print the exact Maven command without executing it.
python run_queue.py list [--json]
    Show pending / running / done / failed items and recent activity.
python run_queue.py clear [--done] [--failed] [--all]
    Remove processed run files (done and failed by default).
```

Directory layout (created on demand, git-ignored):

```
queue/pending/<name>.env    enqueued, waiting
queue/running/<name>.env    claimed; the execution is running
queue/done/<name>.env       finished OK
queue/failed/<name>.env     finished with an error
results/runs.jsonl          append-only audit log (item events + runId, params, status,
                            exit code, report directory)
logs/<runId>.log            Gatling console output
target/gatling/<name>-<timestamp>/
                            Gatling report of that run (created by Gatling, not the queue)
```

The queued run name is the experiment name of that execution: the report lands in
its own `target/gatling/<name>-<yyyyMMddHHmmssSSS>/` directory, together with a
`used_config.txt` holding the configuration the run actually used (the base `.env`
merged with the item's overrides). `run_queue.py` works on Windows (`mvnw.cmd`) and
Linux/SLURM (`./mvnw`).

#### 4.1 Quick start (local)

```bash
# Enqueue two runs with different parameters:
python run_queue.py add baseline  --set TOTAL_USERS=500   --set SIMULATION_MINUTES=30
python run_queue.py add full-load --set TOTAL_USERS=20000 --set SIMULATION_MINUTES=60

# Enqueue a different rate-limit profile:
python run_queue.py add spot-check --set USER_RATE_LIMITS=10000,10000,10000

# Inspect the command before committing hours of wall time:
python run_queue.py run full-load --dry-run

# Drain the queue (items run one at a time in enqueue order):
python run_queue.py start --once        # exit code 1 if any run failed

# Or keep running until Ctrl-C, picking up runs as they are added:
python run_queue.py start
```

#### 4.2 Running the queue on SLURM (recommended)

SLURM is the intended deployment for long experiments. A batch job is allocated a
dedicated compute node, loads Java 11, and drains `queue/pending/` one run at a
time. Enqueue everything first, submit the job, and collect the results when it
finishes — no need to watch anything.

> `queue/` and `.env` are git-ignored runtime state, so **enqueue directly on the
> cluster** in the repository directory (on the shared filesystem), or copy the
> files over with `scp`/`rsync`. Do not rely on git to transfer queued runs.

**Step 1 — prepare (on the cluster login node).** Make sure the repository
contains a valid `.env`, and that Java 11 and Python 3 are available:

```bash
cd ~/scale-gatling-simulations
module load openjdk/11          # same module used by submit_llm_workload.sbatch
python3 --version               # run_queue.py needs only the Python 3 standard library
```

**Step 2 — enqueue your runs.** `run_queue.py` must run in the same directory
that SLURM will process (the submit directory), because that is where it reads
`.env` and `queue/`. Each enqueued run is one execution, so queue one item per
parameter set you want to compare:

```bash
python3 run_queue.py add experiment-1 --set TOTAL_USERS=10000
python3 run_queue.py add experiment-2 --set TOTAL_USERS=20000 --set SIMULATION_MINUTES=90
python3 run_queue.py run experiment-1 --dry-run   # optional: show the command
python3 run_queue.py list                         # sanity-check the pending queue
```

To change the per-user entitlement for one run, override `USER_RATE_LIMITS` with a
`basic,standard,pro` triplet of units per minute:

```bash
python3 run_queue.py add generous --set USER_RATE_LIMITS=75,150,225
```

**Step 3 — submit the batch job:**

```bash
chmod u+x run_queue.py submit_run_queue.sbatch submit_run_queue.sh
sbatch submit_run_queue.sbatch            # default node (c06), 'once' mode
sbatch --nodelist=c07 submit_run_queue.sbatch
./submit_run_queue.sh c07                 # helper, same as above
sbatch --time=06:00:00 submit_run_queue.sbatch   # override the wall-clock limit
```

The job runs `python3 run_queue.py start --once`: it drains the queue, then exits
with code 0 (drained, or nothing pending) or 1 (at least one run failed), so
SLURM reports the job as `COMPLETED` or `FAILED` accordingly.

**Step 4 — monitor.** The worker prints one line per run start/end to the job
output file; the queue state and audit log are updated on disk:

```bash
squeue -u "$USER"
tail -f run-queue-<job-id>.out            # live worker progress (<job-id> from sbatch/squeue)
python3 run_queue.py list                 # queue state (run from the submit directory)
```

**Step 5 — collect results** once the job is `COMPLETED`/`FAILED`
(check with `sacct -j <job-id>`):

```
target/gatling/<name>-<timestamp>/
                           Gatling reports plus used_config.txt, one directory per run
results/runs.jsonl         audit log (item events plus params, runId, status, exit code,
                           report directory and used_config path)
logs/<runId>.log           console output (for debugging failed runs)
queue/done/                run files that finished OK
queue/failed/              run files that failed (the worker keeps going)
```

##### The batch script (`submit_run_queue.sbatch`)

```bash
#!/usr/bin/env bash
#SBATCH --job-name=gatling-run-queue
#SBATCH --output=run-queue-%j.out
#SBATCH --error=run-queue-%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --nodelist=c06

# Drain or watch the run queue on a dedicated compute node. Items from
# queue/pending/ are executed strictly one at a time (FIFO) by run_queue.py;
# each item is one Gatling execution.
#
# Usage:
#   sbatch submit_run_queue.sbatch            # once mode (default): drain queue, then exit
#   sbatch submit_run_queue.sbatch watch      # watch mode: keep polling for new runs
#   sbatch --nodelist=c07 submit_run_queue.sbatch
#   sbatch --time=12:00:00 submit_run_queue.sbatch   # override the wall-clock limit

set -euo pipefail
ROOT_DIR="${SLURM_SUBMIT_DIR}"
cd "$ROOT_DIR"

# Java 11 (same module as submit_llm_workload.sbatch):
if command -v module >/dev/null 2>&1; then
  module load openjdk/11
fi
command -v java >/dev/null 2>&1 || { echo "ERROR: Java is not available." >&2; exit 1; }

# Python 3 (standard library only):
if command -v python3 >/dev/null 2>&1; then PYTHON=python3
elif command -v python >/dev/null 2>&1; then PYTHON=python
else echo "ERROR: python3 is not available." >&2; exit 1; fi

[[ -f .env ]] || { echo "ERROR: .env was not found in $ROOT_DIR." >&2; exit 1; }

mode="${1:-once}"               # 'once' (default) or 'watch'
case "$mode" in
  once|watch) ;;
  *) echo "ERROR: unknown mode '$mode'." >&2; exit 1 ;;
esac

echo "SLURM job: ${SLURM_JOB_ID:-unknown}  Node: ${SLURM_JOB_NODELIST:-unknown}"
if [[ "$mode" == once ]]; then
  exec "$PYTHON" run_queue.py start --once   # exits 1 if any run failed
else
  exec "$PYTHON" run_queue.py start          # keep polling until --time expires
fi
```

What matters in it:

- `#SBATCH --time=...` is the **total** allowed wall time for the whole queue.
  Budget `USER_RAMP_MINUTES + SIMULATION_MINUTES` per queued run, plus 10–15
  minutes of overhead (schedule generation, model resolution, and report
  writing). With the default `.env` (`USER_RAMP_MINUTES=30`,
  `SIMULATION_MINUTES=60`) that is roughly 1.5 hours per item, so the
  `--time=24:00:00` default fits many items. If a run starts too close to the
  limit, the job is killed mid-run — the interrupted item is left in
  `queue/running/` and automatically requeued by the next `start` (reports already
  written are kept).
- `#SBATCH --nodelist=` is overridable per submission with
  `sbatch --nodelist=c07 ...` or via the `./submit_run_queue.sh c07` helper.
- `once` mode is the recommended pattern: enqueue everything up front, bound the
  job time, and let the worker drain the queue. Use the `watch` argument instead
  if you want to keep adding runs while the job is alive; give `--time` enough
  headroom to cover all the runs you plan to add.

Design notes:

- Runs execute **strictly sequentially in enqueue order**, which is the whole
  point of the queue. Do **not** convert this to a SLURM job array — array tasks
  run concurrently and would defeat the sequential FIFO behavior.
- A failed run does not stop the worker; the job only exits non-zero at the end
  (`--once` mode). Check `logs/<runId>.log`, the `finished` record in
  `results/runs.jsonl` (it carries `run_id`, status, exit code and the report
  directory), or `run_queue.py list`.
- `run-llm-workload.sh` plus `submit_llm_workload.sbatch` remain a direct,
  non-queued way to run one execution; that path names the report folder after
  `EXPERIMENT_NAME`. Use the queue when several runs should run unattended.

## Workload Timing & Generation

Before the simulation starts, a modular workload schedule generator creates a precise request schedule for each user. This approach offers several advantages:

**Request Timing Distribution**
Users receive randomized request schedules distributed uniformly throughout the simulation duration. This prevents large gaps while maintaining consistent overall workload.

**Looseness Parameter**
Each user's request count can vary around their assigned profile using the `LOOSENESS` environment variable:
- Defined as a percentage (0-100)
- Example: If a user's low profile is 10 requests/hour and `LOOSENESS=20`, they might send 8, 9, 10, 11, or 12 requests
- Default: 0 (no variation)

**First-Request Behavior**
After the ramp-up phase completes, users begin sending requests at random intervals instead of in rigid batches. This creates a more realistic initial load pattern.

**User Ramp-up Phase**
By default, users arrive evenly during `USER_RAMP_MINUTES` and send no requests during that phase. After all users arrive, they begin sending requests according to their generated schedules.

For example, with 50,000 users and a 30-minute ramp:
- ~27.8 users per second join the simulation
- All requests are paused until the ramp completes
- After ramp, requests begin according to each user's schedule

**Interacting During the Ramp**
Set `INTERACT_DURING_RAMP=true` to let users start sending requests as soon as they
join the simulation instead of waiting for the full ramp to complete:

```dotenv
INTERACT_DURING_RAMP=true
```

With this option:
- The rendezvous barrier is skipped, so each user's schedule starts when that user is
  injected rather than when the last user arrives
- Load grows linearly during the ramp (each arriving user immediately begins their
  pause → request loop)
- Unit limits, refills, and `insufficient-units` accounting are active from the very
  first arrivals; no other setting changes
- The total run envelope stays `USER_RAMP_MINUTES + SIMULATION_MINUTES`

Leave it unset or set to `false` to keep the default behavior of waiting until all
users have arrived.

**Extensible Design**
The workload generation system is modular, supporting different distribution strategies through the `WorkloadTrendStrategy` interface. Currently, the uniform distribution strategy ensures consistent workload throughout the experiment. Future strategies could implement peak hours, circadian patterns, or other realistic trends.

The same reproducible stochastic demand schedule is generated for every run.
Only `USER_RATE_LIMITS` (and any other overridden parameter) changes, so runs
with different entitlements stay directly comparable. This models statistical
multiplexing: the sum of subscription entitlements may be slightly above service
capacity while users are independently active only part of the time.

## Workload Timing (Legacy)

## Offline Maven notes

The repository includes a local Maven repository under `local-repo`. Do not use
`dependency-reduced-pom.xml` as the project build file; use `pom.xml`. The
`-o` option prevents Maven from contacting Maven Central.

Only `local-repo` is used by the build. The sibling directory
`local-repo -DskipTests` is an accidental duplicate of that repository (1,146 files
committed by a mis-quoted Maven invocation): nothing references it, and it is slated for
removal. To drop it from the repository:

```bash
git rm -r --cached "local-repo -DskipTests"
rm -rf "local-repo -DskipTests"
```
