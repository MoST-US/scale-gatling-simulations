# Project context

## What this measures
`simulations.LLMWorkloadSimulation` drives an OpenAI-compatible LLM endpoint with a
population of simulated subscribers and measures how the service behaves under load.

- Each virtual user has a tier (`basic`, `standard`, `pro`) and a usage profile
  (`low`, `high`). `TOTAL_USERS` are split by `BASIC_SHARE` / `STANDARD_SHARE` /
  `PRO_SHARE`, and within each tier by `HIGH_USAGE_USER_SHARE`.
- Units are **not** a lifetime quota: every user's budget refills continuously at the
  tier rate from the `USER_RATE_LIMITS` triplet (units per minute), and a request costs
  `UNITS_PER_REQUEST` units.
- An attempt made without enough units is recorded as a Gatling KO failure named
  `insufficient-units`; the user stays in the simulation and may succeed later. HTTP
  errors remain separate from quota failures.
- Time envelope: users arrive evenly during `USER_RAMP_MINUTES`, then measurement runs
  for `SIMULATION_MINUTES`. With the default `INTERACT_DURING_RAMP=false`, everyone waits
  at a rendezvous barrier until the ramp completes.
- Output: one report folder per execution,
  `target/gatling/<name>-<yyyyMMddHHmmssSSS>/`, holding the Gatling HTML report plus
  `used_config.txt`, the effective configuration of that run. Direct runs take `name`
  from `EXPERIMENT_NAME`; queued runs use the queued run name.

## Sources of truth
- `README.md` - authoritative behaviour, setup, queue and SLURM documentation.
- `.env.example` - the catalogue of every parameter, with inline comments.
- `pom.xml` - Java 11, Gatling 3.10.5, dotenv-java 3.2.0, offline `local-repo`.

## Layout (28 tracked files besides the two vendored Maven repositories)
- `src/main/java/simulations/LLMWorkloadSimulation.java` - entry point: config, model
  resolution, feeder, unit refill, request chain, custom `insufficient-units` action.
- `src/main/java/simulations/SimulationConfig.java` - reads every parameter and builds
  the `UserAssignment` list.
- `src/main/java/simulations/UserAssignment.java` - tier, usage profile and rate of one user.
- `src/main/java/simulations/schedule/` - `WorkloadScheduleGenerator`,
  `UserWorkloadSchedule`, `WorkloadTrendStrategy` (extension point) and
  `UniformWorkloadTrendStrategy`.
- `run-llm-workload.sh` - one execution: sources `.env`, validates `USER_RATE_LIMITS`,
  raises `ulimit -n`, then offline Maven (or the shaded jar with `USE_JAR=true`).
- `run_queue.py` - FIFO queue worker; one `.env`-style file per run, merged over `.env`
  and passed as `-D` properties (see `05-python-and-shell.md`).
- `estimate_requests.py` - analytic request-volume estimate, no simulation required.
- `submit_llm_workload.{sbatch,sh}`, `submit_run_queue.{sbatch,sh}` - SLURM entry points.
- `setup_tunnels.{bat,ps1}`, `offline-settings.xml`, `mvnw`, `mvnw.cmd`, `README.md`.

## Environment
- Source/target Java 11 while the local development JDK is 17: keep the code 11-compatible.
- Development happens on Windows; real runs happen on Linux (SLURM/WSL).
- `src/test/**` is empty and surefire is configured with `skipTests=true`, so there is no
  unit-test harness. "Verification" means compiling, dry-running the queue and reasoning
  about the generated schedule. Do not add JUnit unless the user asks (it is not in
  `local-repo/`).
