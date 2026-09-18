# Build and run

## Toolchain
Java 11 (`maven.compiler.source|target`), Gatling 3.10.5, dotenv-java 3.2.0, Maven
wrapper (`mvnw` / `mvnw.cmd`). Every artifact resolves from the tracked `local-repo/`,
so all Maven commands are offline.

## Canonical commands
Validate a change (fast, no endpoint needed):

```powershell
.\mvnw.cmd -o -Dmaven.repo.local="$PWD/local-repo" test-compile
```

```bash
sh ./mvnw -o -Dmaven.repo.local="$PWD/local-repo" test-compile
```

Run one execution (only with approval - see `00-guardrails.md`):

```powershell
.\mvnw.cmd -o -Dmaven.repo.local="$PWD/local-repo" gatling:test `
  -Dgatling.simulationClass=simulations.LLMWorkloadSimulation
```

```bash
sh ./mvnw -o -Dmaven.repo.local="$PWD/local-repo" gatling:test \
  -Dgatling.simulationClass=simulations.LLMWorkloadSimulation
```

`run-llm-workload.sh` wraps that same call, adds
`-Dgatling.core.outputDirectoryBaseName=<EXPERIMENT_NAME>`, raises `ulimit -n` to
`GATLING_NOFILE_LIMIT` (default 65535) and sets `MAVEN_OPTS`/`JAVA_OPTS` (default
`-Xmx4g`). Keep the memory and file-descriptor limits; the generator, not the LLM, is
usually the bottleneck above 10k users. Never lower them.

Reports land in `target/gatling/<EXPERIMENT_NAME>-<yyyyMMddHHmmssSSS>/` (queued runs:
`target/gatling/<queued run name>-<timestamp>/`), and the launcher or queue drops a
`used_config.txt` with the effective configuration of that run into the folder.
`gatling.runId` is not a Gatling 3.10.5 property - it silently does nothing; the report
folder name always comes from `gatling.core.outputDirectoryBaseName`.

Queue inspection (safe, non-executing):

```bash
python run_queue.py run <name> --dry-run   # prints the exact Maven command
python run_queue.py list [--json]          # pending / running / done / failed
python estimate_requests.py                # expected volume for the current .env
```

Queue on SLURM (needs approval): `sbatch submit_run_queue.sbatch` drains the queue in
`once` mode; `sbatch submit_run_queue.sbatch watch` keeps polling; the node is always
overridable with `--nodelist=` or `./submit_run_queue.sh c07`.

## Wall-clock budget
One run costs `USER_RAMP_MINUTES + SIMULATION_MINUTES` plus 10-15 minutes of overhead
(schedule generation, model resolution, report writing) - roughly 1.5 h with the default
`.env`. Never let a run start inside the last ~15 minutes of `#SBATCH --time`: the killed
job leaves its item in `queue/running/`, and the next `start` requeues it (existing
reports are kept).

## Do not
- Run `mvn clean` when Gatling reports or queue logs matter - it deletes `target/gatling`.
- Add `-U`, snapshot refreshes, or anything else that contacts Maven Central.
- Point `-Dmaven.repo.local` at `local-repo -DskipTests/`.
- Introduce a third way to run the simulation: the launcher and the queue plus
  `estimate_requests.py` are the documented paths.

## What "verified" means here
Compiles cleanly, queue `--dry-run` output still valid, and `estimate_requests.py`
consistent with the Java model. There is no unit-test suite.
