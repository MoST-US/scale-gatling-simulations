#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Load .env without xargs so quoted prompts and values containing spaces survive.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Protect the generator before increasing request rate.
export JAVA_OPTS="${JAVA_OPTS:--Xmx4g}"
requested_nofile="${GATLING_NOFILE_LIMIT:-65535}"
current_nofile="$(ulimit -n)"
if [[ "$current_nofile" != "unlimited" && "$current_nofile" -lt "$requested_nofile" ]]; then
  if ! ulimit -n "$requested_nofile" 2>/dev/null; then
    echo "WARNING: could not raise open-file limit to $requested_nofile (current: $current_nofile)." >&2
  fi
fi

export LLM_URL="${LLM_URL:-http://localhost:11434}"
export ENDPOINT_PATH="${ENDPOINT_PATH:-/v1/completions}"
export MODELS_ENDPOINT="${MODELS_ENDPOINT:-/v1/models}"

if [[ "$LLM_URL" != http://* && "$LLM_URL" != https://* ]]; then
  export LLM_URL="http://$LLM_URL"
fi

echo "Target: $LLM_URL$ENDPOINT_PATH (model discovered from $LLM_URL$MODELS_ENDPOINT)"
echo "Generator: JAVA_OPTS=$JAVA_OPTS, open files=$(ulimit -n)"

export MAVEN_OPTS="${MAVEN_OPTS:-} $JAVA_OPTS"

# USER_RATE_LIMITS is the single per-user entitlement knob: a "basic,standard,pro"
# triplet of units per minute. Validate it here so a typo fails before Java starts.
validate_rate_limits() {
  local values="$1"
  local basic_units standard_units pro_units extra units
  IFS="," read -r basic_units standard_units pro_units extra <<< "$values"
  if [[ -n "${extra:-}" || -z "${basic_units:-}" || -z "${standard_units:-}" || -z "${pro_units:-}" ]]; then
    echo "ERROR: USER_RATE_LIMITS must contain exactly three comma-separated unit values: '$values'" >&2
    exit 1
  fi
  for units in "$basic_units" "$standard_units" "$pro_units"; do
    if [[ ! "$units" =~ ^[0-9]+$ ]]; then
      echo "ERROR: USER_RATE_LIMITS contains an invalid unit value: '$units'" >&2
      exit 1
    fi
  done
}

# EXPERIMENT_NAME prefixes the Gatling report folder
# (target/gatling/<EXPERIMENT_NAME>-<timestamp>). Gatling receives it as a system
# property, so keep it whitespace- and filesystem-safe.
validate_experiment_name() {
  local name="$1"
  if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "ERROR: EXPERIMENT_NAME must match [A-Za-z0-9][A-Za-z0-9._-]* with no whitespace: '$name'" >&2
    exit 1
  fi
}

user_rate_limits="${USER_RATE_LIMITS:-10,20,40}"
validate_rate_limits "$user_rate_limits"

experiment_name="${EXPERIMENT_NAME:-llm-workload}"
validate_experiment_name "$experiment_name"

# runId is only the human/log label of this execution; Gatling names the report
# folder after the experiment name.
runId="$experiment_name-$(date +%Y%m%d%H%M%S)"
echo "Starting run $runId (EXPERIMENT_NAME=$experiment_name, USER_RATE_LIMITS=$user_rate_limits units/min, basic/standard/pro)"

jar_path="target/gatling-llm-simulations-0.1.0-SNAPSHOT.jar"
if [[ -f "$jar_path" && "${USE_JAR:-false}" == "true" ]]; then
  launcher_command="java $JAVA_OPTS -Dgatling.core.checkVersion=false"
  launcher_command+=" -Dgatling.core.outputDirectoryBaseName=$experiment_name"
  launcher_command+=" -DUSER_RATE_LIMITS=$user_rate_limits"
  launcher_command+=" -jar $jar_path -s simulations.LLMWorkloadSimulation -rf target/gatling"
  # shellcheck disable=SC2086
  java $JAVA_OPTS \
    -Dgatling.core.checkVersion=false \
    -Dgatling.core.outputDirectoryBaseName="$experiment_name" \
    -DUSER_RATE_LIMITS="$user_rate_limits" \
    -jar "$jar_path" \
    -s simulations.LLMWorkloadSimulation -rf target/gatling
else
  launcher_command="sh ./mvnw -o -Dmaven.repo.local=$ROOT_DIR/local-repo"
  launcher_command+=" -Dgatling.core.outputDirectoryBaseName=$experiment_name"
  launcher_command+=" -DUSER_RATE_LIMITS=$user_rate_limits"
  launcher_command+=" gatling:test -Dgatling.simulationClass=simulations.LLMWorkloadSimulation"
  sh ./mvnw -o \
    -Dmaven.repo.local="$ROOT_DIR/local-repo" \
    -Dgatling.core.outputDirectoryBaseName="$experiment_name" \
    -DUSER_RATE_LIMITS="$user_rate_limits" \
    gatling:test \
    -Dgatling.simulationClass=simulations.LLMWorkloadSimulation
fi

# Gatling writes target/gatling/<experiment_name>-<yyyyMMddHHmmssSSS>/; copy the
# configuration that this execution used into that folder so every report is
# self-describing. The zero-padded timestamp makes the greatest match the newest
# folder. This bookkeeping must never fail an otherwise successful run.
write_used_config() {
  local report_dir=""
  local candidate
  for candidate in "target/gatling/$experiment_name"-*; do
    if [[ -d "$candidate" && ( -z "$report_dir" || "$candidate" > "$report_dir" ) ]]; then
      report_dir="$candidate"
    fi
  done
  if [[ -z "$report_dir" ]]; then
    echo "WARNING: no report folder matching target/gatling/$experiment_name-* was found; skipping used_config.txt." >&2
    return 0
  fi
  {
    echo "# used_config.txt - effective configuration of this Gatling run"
    echo "# experiment : $experiment_name"
    echo "# run id     : $runId"
    echo "# report dir : $report_dir"
    echo "# started    : $(date '+%Y-%m-%dT%H:%M:%S%z')"
    echo "# source     : .env (launcher override: USER_RATE_LIMITS=$user_rate_limits)"
    echo "# command    : $launcher_command"
    echo
    if [[ -f .env ]]; then
      grep -v -E '^[[:space:]]*(export[[:space:]]+)?USER_RATE_LIMITS[[:space:]]*=' .env || true
    else
      echo "# .env was not present; only the values passed on the command line apply"
    fi
    echo "USER_RATE_LIMITS=$user_rate_limits"
  } > "$report_dir/used_config.txt"
  echo "Configuration recorded in $report_dir/used_config.txt"
}

write_used_config || echo "WARNING: could not write used_config.txt." >&2
