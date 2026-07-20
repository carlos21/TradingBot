#!/usr/bin/env bash
# Shared helper for scenario entry points (run_scenarios.sh, compare_configs.sh,
# compare_modes.sh). Source it, then:
#   parse_scenario_args "$@"      # extracts --group, sets GROUP and FILTERED_ARGS
#   YAML="$(resolve_scenario_yaml "$GROUP")"
# Forward the remaining flags with: ${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}
# (the [@]+ idiom keeps bash 3.2 happy on empty arrays under set -u)

SCENARIOS_DIR="backend/src/strategies/liquidity_v2/scenarios"

# Extracts --group <name|path> (or --group=<name|path>) from the arguments.
# Sets GROUP (default: ny) and FILTERED_ARGS (all remaining args).
parse_scenario_args() {
  GROUP="ny"
  FILTERED_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --group)
        GROUP="${2:?--group requires a value}"
        shift 2
        ;;
      --group=*)
        GROUP="${1#*=}"
        shift
        ;;
      *)
        FILTERED_ARGS+=("$1")
        shift
        ;;
    esac
  done
}

# Resolves a group name to one or more yaml files, stored in RESOLVED_YAMLS.
# "all" expands to every $SCENARIOS_DIR/*.yaml (sorted); anything else delegates
# to resolve_scenario_yaml. Returns 1 (via resolve_scenario_yaml) on unknown groups.
resolve_scenario_yamls() {
  local group="$1"
  RESOLVED_YAMLS=()

  if [[ "$group" == "all" ]]; then
    local f
    for f in "$SCENARIOS_DIR"/*.yaml; do
      [[ -f "$f" ]] && RESOLVED_YAMLS+=("$f")
    done
    if [[ ${#RESOLVED_YAMLS[@]} -eq 0 ]]; then
      echo "No scenario yamls found in $SCENARIOS_DIR" >&2
      return 1
    fi
  else
    local yaml
    yaml="$(resolve_scenario_yaml "$group")" || return 1
    RESOLVED_YAMLS+=("$yaml")
  fi
}

# Resolves a group name (or a direct yaml path) to a yaml file.
# Echoes the path on success; prints available groups to stderr and returns 1.
resolve_scenario_yaml() {
  local group="$1"

  if [[ -f "$SCENARIOS_DIR/$group.yaml" ]]; then
    echo "$SCENARIOS_DIR/$group.yaml"
  elif [[ -f "$group" ]]; then
    echo "$group"
  else
    echo "Unknown scenario group: $group" >&2
    echo "Available groups:" >&2
    local f
    for f in "$SCENARIOS_DIR"/*.yaml; do
      echo "  $(basename "$f" .yaml)" >&2
    done
    return 1
  fi
}
