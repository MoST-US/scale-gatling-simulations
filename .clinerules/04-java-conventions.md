---
paths:
  - "src/main/java/**"
---

# Java conventions

## Language and dependencies
- Java 11 source level while the local JDK is 17: avoid post-11 APIs - no `var`, records,
  switch expressions, `Random.nextLong(long)` or `Stream.toList()`. `List.of` and
  `java.net.http.HttpClient` are fine.
- No new dependencies or plugins: there is no network during the build, so anything not
  already in `local-repo/` cannot be resolved. If a task seems to need a library, say so
  instead of editing `pom.xml`.

## Layout
- `simulations` holds configuration and the Gatling entry point; `simulations.schedule`
  holds the pure schedule model and must not import Gatling.
- New distribution strategies implement `WorkloadTrendStrategy` and are wired in
  `LLMWorkloadSimulation.generateWorkloadSchedules`. That extension point is an explicit
  design goal - keep it clean and documented.
- Indentation: `simulations/*.java` use 2 spaces, `simulations/schedule/*.java` use 4
  spaces. Match the file you edit and never reformat an unrelated region.

## Style
- Reuse `value` / `intValue` / `doubleValue` for property lookups; parameters are read in
  `SimulationConfig`, not scattered through the simulation.
- Getters: `getX()` for values, `isX()` for booleans. Keep existing public names stable.
- Keep the javadoc the schedule package already has (it documents the extension point).
  Elsewhere, do not add comments that restate the code - comment the why, not the what.
- English only, no `System.out` (use the existing Gatling logger pattern), and keep the
  exception types in use: `IllegalArgumentException` for invalid configuration,
  `IllegalStateException` for an unreachable or misbehaving endpoint.
- Keep sources ASCII (no smart quotes, no emoji), matching the current files.

## Gatling specifics
- The Java DSL relies on the `CoreDsl` / `HttpDsl` static imports; scenarios, chains and
  the protocol are `private final` fields built once, as in the current code.
- Custom actions need Scala interop glue: implement
  `io.gatling.javaapi.core.ActionBuilder` and return an
  `io.gatling.core.action.builder.ActionBuilder` from `asScala()`, exactly as
  `InsufficientUnitsAction` / `InsufficientUnitsActionBuilder` do. `Logger$`,
  `Option.apply` and `KO$.MODULE$` are required to satisfy the Scala traits, and
  `com$typesafe$scalalogging$StrictLogging$_setter_$logger_$eq` is mandated by the trait
  name - do not "clean it up".

## Behavioural invariants
- Reproducibility: the seeded schedule generation stays as described in
  `03-configuration-contract.md`.
- Quota shortfalls are recorded through the stats engine as a KO named
  `insufficient-units`. They must not abort the scenario, must not look like HTTP
  failures, and HTTP failures must not be folded into the quota counter.
- Session keys are a cross-file contract. The feeder sets `userIndex`, `requestUrl`,
  `targetRequests`, `requestIndex`, `unitsPerMinute`, `accumulatedUnits`,
  `lastRefillTimeMs` and `workloadSchedule`; renaming one requires updating every reader
  (`refillQuota`, the request chain, schedule lookups).

## Verification
`.\mvnw.cmd -o -Dmaven.repo.local="$PWD/local-repo" test-compile` (Windows) or
`sh ./mvnw -o -Dmaven.repo.local="$PWD/local-repo" test-compile` (Linux). There is no
unit-test harness and surefire skips tests - do not add JUnit unless the user asks. A real
`gatling:test` run needs explicit approval; verify scheduling logic through
`estimate_requests.py` and reasoning instead.
