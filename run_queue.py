#!/usr/bin/env python3
"""
run_queue.py - queue and sequentially execute Gatling LLM simulations.

The Gatling simulation (simulations.LLMWorkloadSimulation) reads every parameter
from .env via SimulationConfig.load() with priority: -D JVM properties, then
.env, then environment variables. run_queue.py exploits that priority to run an
arbitrary list of executions back-to-back, each with its own parameters, without
touching any Java code.

A queued "run" is a small .env-style file (queue/pending/<name>.env) holding only
the parameters that differ from the base .env. One queued item is one Gatling
execution: the worker merges the item's overrides over the base .env and passes
every merged key as a -D JVM property to `mvnw gatling:test`, so the -D values
win (mirroring run-llm-workload.sh).

The per-user entitlement is the comma-separated USER_RATE_LIMITS triplet
(basic,standard,pro units per minute). To reproduce the old "sweep" behavior,
enqueue one item per triplet, for example:

    python run_queue.py add under  --set USER_RATE_LIMITS=25,50,75
    python run_queue.py add over   --set USER_RATE_LIMITS=10000,10000,10000
    python run_queue.py add tuned  --set USER_RATE_LIMITS=50,100,150

Runs are executed strictly one at a time (FIFO): the next item only starts after
the current one has finished, so you can enqueue several runs and walk away. An
item that exits non-zero is moved to queue/failed/ and the worker continues with
the next item.

The queued run name is the experiment name of that execution. The worker passes
-Dgatling.core.outputDirectoryBaseName=<name> (the property Gatling 3.10.5 uses for
the report folder, which is the only one that works: "gatling.runId" does not
exist), so the report lands in target/gatling/<name>-<yyyyMMddHHmmssSSS>/ and that
folder receives a used_config.txt holding the effective configuration of the run
(the base .env merged with the item's overrides). run-llm-workload.sh does the same
with its EXPERIMENT_NAME.

Directory layout (all created on demand):
    queue/pending/<name>.env    enqueued, waiting
    queue/running/<name>.env    claimed and currently executing
    queue/done/<name>.env       finished successfully
    queue/failed/<name>.env     finished with an error
    results/runs.jsonl          append-only per-run audit log
    logs/<runId>.log            Gatling console output
    target/gatling/<name>-<timestamp>/                  Gatling report folder of that run
    target/gatling/<name>-<timestamp>/used_config.txt   effective configuration of that run

Usage:
    python run_queue.py add <name> [--set KEY=VALUE ...] [--env-file PATH]
        Enqueue a new run. --set is repeatable; values override the base .env.
        --env-file imports overrides from an existing .env-style file.
    python run_queue.py start [--once] [--interval SECONDS]
        Process the queue sequentially. With --once, drain what is pending now
        and exit; by default keep polling for newly added runs until Ctrl-C.
    python run_queue.py run <name> [--dry-run]
        Run a single named pending item now. --dry-run prints the exact command
        that would be used without executing it or touching the queue.
    python run_queue.py list [--json]
        Show pending / running / done / failed items and recent activity.
    python run_queue.py clear [--done] [--failed] [--all]
        Remove processed run files (done and failed by default).

Environment / command building mirrors run-llm-workload.sh:
    * Windows uses mvnw.cmd (via cmd.exe /c), POSIX uses `sh ./mvnw`.
    * Offline mode (-o -Dmaven.repo.local=...) is used when local-repo exists.
    * MAVEN_OPTS defaults to -Xmx4g unless one is already set.

Test hook: set the environment variable RUN_QUEUE_CMD to an alternate launcher
(e.g. "python -c ...") to substitute the Maven invocation; it is called once per
run, which makes it easy to verify queue state transitions without a live LLM. A
fake launcher that creates target/gatling/<name>-<timestamp>/ also exercises the
report-folder discovery and used_config.txt writing.
The value is split with shlex, so on Windows use forward slashes in file paths
(shlex treats backslashes as escape characters).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

QUEUE_DIR = os.path.join(ROOT_DIR, "queue")
PENDING_DIR = os.path.join(QUEUE_DIR, "pending")
RUNNING_DIR = os.path.join(QUEUE_DIR, "running")
DONE_DIR = os.path.join(QUEUE_DIR, "done")
FAILED_DIR = os.path.join(QUEUE_DIR, "failed")

RESULTS_DIR = os.path.join(ROOT_DIR, "results")
RUNS_LOG = os.path.join(RESULTS_DIR, "runs.jsonl")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")

# Gatling writes one report folder per run (<name>-<yyyyMMddHHmmssSSS>) here.
GATLING_REPORTS_DIR = os.path.join(ROOT_DIR, "target", "gatling")

LOCAL_REPO = os.path.join(ROOT_DIR, "local-repo")

BASE_ENV = (
    os.path.join(ROOT_DIR, ".env")
    if os.path.isfile(os.path.join(ROOT_DIR, ".env"))
    else os.path.join(ROOT_DIR, ".env.example")
)

# A run name becomes part of a filesystem path and the Gatling runId, so keep it
# to a safe, portable token.
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# USER_RATE_LIMITS is a non-negative "basic,standard,pro" triplet of integers.
UNIT_VALUE_RE = re.compile(r"^[0-9]+$")
DEFAULT_USER_RATE_LIMITS = "10,20,40"


def ensure_dirs() -> None:
    for d in (QUEUE_DIR, PENDING_DIR, RUNNING_DIR, DONE_DIR, FAILED_DIR, RESULTS_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------- #
# .env parsing (mirrors estimate_requests.py)
# --------------------------------------------------------------------------- #
def parse_env_value(raw: str) -> str:
    """Strip a trailing comment (outside quotes) and surrounding quotes."""
    out = []
    in_single = in_double = False
    for i, ch in enumerate(raw):
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            if i == 0 or raw[i - 1].isspace():
                break
        out.append(ch)
    value = "".join(out).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def load_env_file(path: str) -> dict:
    settings = {}
    if not os.path.isfile(path):
        raise FileNotFoundError("environment file not found: %s" % path)
    try:
        text = open(path, "r", encoding="utf-8-sig").read()
    except UnicodeDecodeError:
        text = open(path, "r", encoding="latin-1").read()
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if match:
            settings[match.group(1)] = parse_env_value(match.group(2))
    return settings


def format_env_value(value: str) -> str:
    """Quote a value for the .env-style item file when it needs it."""
    if re.search(r"[\s#]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    return value


def write_item_file(path: str, overrides: dict) -> None:
    lines = ["%s=%s" % (k, format_env_value(overrides[k])) for k in sorted(overrides)]
    text = "\n".join(lines) + ("\n" if lines else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def shell_join(args) -> str:
    return " ".join(shlex.quote(a) for a in args)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def run_id_for(name: str) -> str:
    stamp = time.strftime("%Y%m%d%H%M%S") + "%03d" % (int(time.time() * 1000) % 1000)
    return "%s-%s" % (name, stamp)


def parse_rate_limits(raw: str):
    """Parse a 'basic,standard,pro' USER_RATE_LIMITS triplet (mirrors run-llm-workload.sh)."""
    values = [value.strip() for value in str(raw).split(",")]
    if len(values) != 3 or any(value == "" for value in values):
        sys.exit("error: USER_RATE_LIMITS must contain exactly three comma-separated "
                 "unit values: %r" % raw)
    for value in values:
        if not UNIT_VALUE_RE.match(value):
            sys.exit("error: USER_RATE_LIMITS contains an invalid unit value: %r" % value)
    return [int(value) for value in values]


def log_event(record: dict) -> None:
    ensure_dirs()
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------- #
# Command construction & invocation
# --------------------------------------------------------------------------- #
def build_command(report_base: str, merged: dict):
    if os.path.isdir(LOCAL_REPO):
        offline = ["-o", "-Dmaven.repo.local=" + LOCAL_REPO]
    else:
        offline = []

    launcher = os.environ.get("RUN_QUEUE_CMD", "")
    if launcher:
        base = shlex.split(launcher)
    elif os.name == "nt":
        # .cmd files cannot be launched directly by CreateProcess; route via cmd.
        base = ["cmd.exe", "/c", "mvnw.cmd"]
    else:
        base = ["sh", "./mvnw"]

    args = list(base) + [
        "gatling:test",
        "-Dgatling.simulationClass=simulations.LLMWorkloadSimulation",
        # Gatling names the report folder <base>-<yyyyMMddHHmmssSSS> under
        # target/gatling. "gatling.runId" is not a Gatling 3.10.5 property.
        "-Dgatling.core.outputDirectoryBaseName=" + report_base,
        "-Dgatling.core.checkVersion=false",
    ] + offline
    for k in sorted(merged):
        args.append("-D%s=%s" % (k, merged[k]))
    return args


def newest_report_dir(report_base: str):
    """Return the report folder Gatling just created, or None.

    Gatling appends a zero-padded yyyyMMddHHmmssSSS timestamp to the configured
    base name, so the lexicographically greatest match is the newest folder.
    """
    if not os.path.isdir(GATLING_REPORTS_DIR):
        return None
    prefix = report_base + "-"
    matches = [
        os.path.join(GATLING_REPORTS_DIR, entry)
        for entry in os.listdir(GATLING_REPORTS_DIR)
        if entry.startswith(prefix) and os.path.isdir(os.path.join(GATLING_REPORTS_DIR, entry))
    ]
    return max(matches) if matches else None


def write_used_config(report_dir: str, report_base: str, run_name: str, run_id: str,
                      merged: dict, command, overrides: dict):
    """Write used_config.txt into the run's report folder.

    The file is .env-style (a comment header plus KEY=VALUE lines), so it can be
    fed back through --env-file. Returns the path, or None when it cannot be
    written: recording the configuration must never fail an otherwise fine run.
    """
    source = os.path.relpath(BASE_ENV, ROOT_DIR).replace(os.sep, "/")
    lines = [
        "# used_config.txt - effective configuration of this Gatling run",
        "# experiment : %s" % report_base,
        "# run item   : %s" % run_name,
        "# run id     : %s" % run_id,
        "# report dir : %s" % os.path.relpath(report_dir, ROOT_DIR).replace(os.sep, "/"),
        "# started    : %s" % now_iso(),
        "# source     : %s" % source,
        "# overrides  : %s" % (", ".join(sorted(overrides)) if overrides else "none"),
        "# command    : %s" % shell_join(command),
        "",
    ]
    lines += ["%s=%s" % (key, merged[key]) for key in sorted(merged)]
    path = os.path.join(report_dir, "used_config.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        print("WARNING: could not write %s (%s)" % (path, exc), file=sys.stderr)
        return None
    return path


def invoke(args, log_path: str) -> int:
    env = dict(os.environ)
    maven_opts = env.get("MAVEN_OPTS", "")
    if "-Xmx" not in maven_opts:
        env["MAVEN_OPTS"] = ("-Xmx4g " + maven_opts).strip()
    ensure_dirs()
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.run(args, cwd=ROOT_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT)
    return proc.returncode


# --------------------------------------------------------------------------- #
# Run processing
# --------------------------------------------------------------------------- #
def _run_one(pending_path: str, dry_run: bool = False) -> bool:
    name = os.path.splitext(os.path.basename(pending_path))[0]
    overrides = load_env_file(pending_path)
    merged = dict(load_env_file(BASE_ENV))
    merged.update(overrides)
    rate_limits = merged.get("USER_RATE_LIMITS", DEFAULT_USER_RATE_LIMITS)
    parse_rate_limits(rate_limits)
    # The queued run name is the experiment name: it is the prefix of the Gatling
    # report folder (target/gatling/<name>-<timestamp>).
    report_base = name
    run_id = run_id_for(name)
    command = build_command(report_base, merged)
    report_dir_hint = os.path.join("target", "gatling", report_base + "-<timestamp>")

    if dry_run:
        print("Run        : %s" % name)
        print("run_id     : %s" % run_id)
        print("Rate limits: USER_RATE_LIMITS=%s (basic,standard,pro units/min)" % rate_limits)
        print("Parameters (%d, passed as -D):" % len(merged))
        for k in sorted(merged):
            print("  %s=%s" % (k, merged[k]))
        print("Command:")
        print("  " + shell_join(command))
        print("Log file (would be): %s" % os.path.join(LOGS_DIR, run_id + ".log"))
        print("Report dir (would be): %s" % report_dir_hint)
        print("Config file (would be): %s" % os.path.join(report_dir_hint, "used_config.txt"))
        return True

    running_path = os.path.join(RUNNING_DIR, os.path.basename(pending_path))
    ensure_dirs()
    os.replace(pending_path, running_path)  # atomic claim on the same filesystem
    log_event({"event": "started", "run": name, "run_id": run_id, "report_base": report_base,
               "status": "running", "started_at": now_iso()})
    print("[%s] START  %s (run_id=%s, report=%s)"
          % (now_iso(), name, run_id, report_dir_hint))

    log_path = os.path.join(LOGS_DIR, run_id + ".log")
    exit_code = invoke(command, log_path)
    status = "done" if exit_code == 0 else "failed"
    report_dir = newest_report_dir(report_base)
    config_file = None
    if report_dir is None:
        print("WARNING: no report folder matching %s was found; used_config.txt not written."
              % os.path.join("target", "gatling", report_base + "-*"), file=sys.stderr)
    else:
        config_file = write_used_config(report_dir, report_base, name, run_id, merged,
                                        command, overrides)
    record = {"event": "finished", "run": name, "run_id": run_id, "status": status,
              "exit_code": exit_code, "finished_at": now_iso(),
              "log_file": os.path.join("logs", run_id + ".log")}
    if report_dir is not None:
        record["report_dir"] = os.path.relpath(report_dir, ROOT_DIR).replace(os.sep, "/")
    if config_file is not None:
        record["used_config"] = os.path.relpath(config_file, ROOT_DIR).replace(os.sep, "/")
    log_event(record)

    dest = os.path.join(DONE_DIR if exit_code == 0 else FAILED_DIR,
                        os.path.basename(pending_path))
    os.replace(running_path, dest)
    print("[%s] END    %s -> %s (exit=%d, log=%s%s)"
          % (now_iso(), name, status, exit_code, log_path,
             ", report=" + record["report_dir"] if "report_dir" in record else ""))
    return exit_code == 0


def requeue_stale() -> None:
    """If a previous worker was interrupted mid-run, put its item back in queue."""
    stale = [f for f in os.listdir(RUNNING_DIR) if f.endswith(".env")]
    for f in stale:
        os.replace(os.path.join(RUNNING_DIR, f), os.path.join(PENDING_DIR, f))
        print("WARNING: interrupted run %r moved back to pending" % f)


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_add(args) -> None:
    ensure_dirs()
    name = args.name
    if not SAFE_NAME_RE.match(name):
        sys.exit("error: name must match %s (got %r)" % (SAFE_NAME_RE.pattern, name))

    overrides = {}
    for kv in args.set or []:
        key, sep, value = kv.partition("=")
        if not key or not sep:
            sys.exit("error: --set must look like KEY=VALUE (got %r)" % kv)
        overrides[key.strip().upper()] = value
    if args.env_file:
        for k, v in load_env_file(args.env_file).items():
            overrides.setdefault(k.upper(), v)  # explicit --set wins

    # Fail fast on malformed rate limits instead of dying when the worker picks
    # the item up.
    merged = dict(load_env_file(BASE_ENV))
    merged.update(overrides)
    parse_rate_limits(merged.get("USER_RATE_LIMITS", DEFAULT_USER_RATE_LIMITS))

    dest = os.path.join(PENDING_DIR, name + ".env")
    if os.path.exists(dest):
        sys.exit("error: a pending run named %r already exists (%s)" % (name, dest))

    if not overrides:
        print("note: no overrides given; run will use the base .env as-is")
    write_item_file(dest, dict(overrides))
    log_event({"event": "enqueued", "run": name, "status": "queued",
               "params": overrides, "enqueued_at": now_iso()})
    print("Enqueued %s -> %s" % (name, dest))
    print("Start the worker with: python run_queue.py start [--once]")


def cmd_start(args) -> None:
    ensure_dirs()
    requeue_stale()
    while True:
        pending = sorted(f for f in os.listdir(PENDING_DIR) if f.endswith(".env"))
        if not pending:
            if args.once:
                print("Queue is empty.")
                break
            time.sleep(max(1.0, args.interval))
            continue
        all_ok = True
        for f in pending:
            all_ok = _run_one(os.path.join(PENDING_DIR, f)) and all_ok
        if args.once:
            print("Queue drained (all ok=%s)." % all_ok)
            if not all_ok:
                # Propagate failures so SLURM marks the job FAILED.
                sys.exit(1)
            break


def cmd_run(args) -> None:
    ensure_dirs()
    name = args.name
    if not SAFE_NAME_RE.match(name):
        sys.exit("error: name must match %s (got %r)" % (SAFE_NAME_RE.pattern, name))
    path = os.path.join(PENDING_DIR, name + ".env")
    if not os.path.exists(path):
        sys.exit("error: no pending run named %r (looked for %s)" % (name, path))
    _run_one(path, dry_run=args.dry_run)


def cmd_list(args) -> None:
    ensure_dirs()
    groups = {}
    dirs = {}
    for label, d in (("pending", PENDING_DIR), ("running", RUNNING_DIR),
                     ("done", DONE_DIR), ("failed", FAILED_DIR)):
        dirs[label] = d
        groups[label] = sorted(f for f in os.listdir(d) if f.endswith(".env"))
    if args.json:
        print(json.dumps({label: files for label, files in groups.items()}))
        return
    for label in ("pending", "running", "done", "failed"):
        files = groups[label]
        print("%-8s %d" % (label + ":", len(files)))
        for f in files:
            print("          %s" % f)
    if os.path.exists(RUNS_LOG):
        with open(RUNS_LOG, encoding="utf-8") as fh:
            lines = [l for l in fh if l.strip()]
        print("\nRecent activity (%d record(s) in %s):" % (len(lines), RUNS_LOG))
        for line in lines[-5:]:
            rec = json.loads(line)
            stamp = rec.get("started_at") or rec.get("enqueued_at") or rec.get("finished_at") or ""
            event = rec.get("event", "")
            if rec.get("case"):
                event = "%s:%s" % (event, rec["case"])
            run_id = rec.get("run_id") or ",".join(rec.get("run_ids", []))
            print("  %s %-24s %-12s %s" % (stamp, event, rec.get("run", ""), run_id))


def cmd_clear(args) -> None:
    ensure_dirs()
    targets = []
    if args.all:
        targets = [PENDING_DIR, RUNNING_DIR, DONE_DIR, FAILED_DIR]
    else:
        if args.done:
            targets.append(DONE_DIR)
        if args.failed:
            targets.append(FAILED_DIR)
        if not targets:
            targets = [DONE_DIR, FAILED_DIR]
    removed = 0
    for d in targets:
        for f in os.listdir(d):
            if f.endswith(".env"):
                os.remove(os.path.join(d, f))
                removed += 1
    print("Removed %d run file(s)." % removed)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="run_queue.py",
        description="Queue and sequentially run Gatling LLM simulations.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Enqueue a single run")
    p_add.add_argument("name", help="Run name (letters, digits, '.', '_', '-')")
    p_add.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="Override a parameter; repeatable")
    p_add.add_argument("--env-file", metavar="PATH",
                       help="Import overrides from an .env-style file")
    p_add.set_defaults(func=cmd_add)

    p_start = sub.add_parser("start", help="Process the queue sequentially")
    p_start.add_argument("--once", action="store_true",
                         help="Drain what is pending now, then exit")
    p_start.add_argument("--interval", type=float, default=2.0,
                         help="Poll interval seconds when watching (default 2)")
    p_start.set_defaults(func=cmd_start)

    p_run = sub.add_parser("run", help="Run a single named pending item")
    p_run.add_argument("name")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Print the command without executing or touching the queue")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="Show queue state")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_clear = sub.add_parser("clear", help="Remove processed run files")
    p_clear.add_argument("--done", action="store_true")
    p_clear.add_argument("--failed", action="store_true")
    p_clear.add_argument("--all", action="store_true",
                         help="Also remove pending and running items")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
