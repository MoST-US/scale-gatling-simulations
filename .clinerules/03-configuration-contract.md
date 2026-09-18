# Configuration contract

All behaviour is configuration-driven; nothing should be compiled in.

## Precedence (preserve it)
`-D` JVM property -> `.env` -> process environment -> Java default (`SimulationConfig.read`).
`.env` is located by walking up from `user.dir` until a directory that contains both
`.env` and `pom.xml` (`findDotenvDirectory`). That is what lets `run_queue.py` merge a
run file over `.env` and pass the result as `-D` properties, so the `-D` values win.

## Adding or changing a parameter
One change touches all of these, together:
1. `.env.example` - the key plus an inline comment giving meaning and unit.
2. `SimulationConfig.load()` - the default used when the key is absent.
3. `SimulationConfig` - field, constructor argument and getter (`getX()` / `isX()`).
4. `README.md` - the settings block for the parameter, and any failure semantics.

Use the existing `read` / `readInt` / `readDouble` / `readBoolean` / `readTriplet`
helpers instead of reading properties ad hoc. Key names are UPPER_SNAKE and identical
in `.env.example`, the README, the shell launcher and the Java code.

## USER_RATE_LIMITS (the entitlement knob)
`USER_RATE_LIMITS=basic,standard,pro` in **units per minute**: exactly three
non-negative integers. It is validated twice - keep both behaviours:
- `SimulationConfig.readTriplet` silently falls back to `{10, 20, 40}` when malformed;
- `run-llm-workload.sh::validate_rate_limits` fails fast with a clear error before Java
  starts. That pre-flight check is deliberate: a typo must not cost an hour of wall time.

## Units model
Units refill continuously (`unitsPerMinute * elapsedMs / 60000`) and are capped at
`UNITS_PER_REQUEST * MAX_ACCUMULATED_REQUESTS` per user. The example sets
`UNITS_PER_REQUEST=100` so an integer budget can represent fractions of a request per
minute. A user short of units records `insufficient-units` and stays in the simulation.

## Timing semantics
- Run envelope: `USER_RAMP_MINUTES + SIMULATION_MINUTES`.
- `INTERACT_DURING_RAMP=false` (default): users wait at `rendezVous(TOTAL_USERS)`, so no
  request is sent before the ramp completes and the full load appears at once.
- `INTERACT_DURING_RAMP=true`: the barrier is skipped, each user starts its own schedule
  on arrival, and load grows linearly during the ramp. Unit accounting applies from the
  first arrivals; nothing else changes.

## Determinism
`LLMWorkloadSimulation.generateWorkloadSchedules` uses `new Random(42)`, and
`UniformWorkloadTrendStrategy` is constructed with that same seeded generator, so the
demand trace is reproducible and runs differing only in `USER_RATE_LIMITS` stay
comparable. If the schedule model changes:
- keep the seed and the per-user generation order unchanged, or state explicitly why not;
- mirror the change in `estimate_requests.py`, which re-implements the model;
- update the README explanation of workload generation and looseness.

## Model resolution
`MODEL_ID` overrides discovery. When it is unset, the simulation performs a synchronous
`GET MODELS_ENDPOINT` at startup and fails fast (`IllegalStateException`) if the host is
unreachable or answers non-2xx. Keep those messages actionable: the usual cause is a
missing SSH tunnel (`setup_tunnels.ps1` / `setup_tunnels.bat`).

## Removed concepts
Multi-case "sweep" runs were replaced by enqueueing one run per profile: one queued run
is one Gatling execution. Do not reintroduce sweep keys or per-tier rate-limit variables
- the `USER_RATE_LIMITS` triplet replaced them, and the queue is the way to compare
provisioning profiles.
