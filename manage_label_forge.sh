#!/usr/bin/env bash
# Helper script to manage Label-Forge Community Edition Docker profiles.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE=(docker-compose)
else
  echo "ERROR: Neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

ENV_FILE="${LF_ENV_FILE:-$SCRIPT_DIR/.env}"
DOCKER_PRUNE_STATE_FILE="${LF_DOCKER_PRUNE_STATE_FILE:-$SCRIPT_DIR/.git/label-forge-docker-prune.last-run}"

load_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi

  set -a
  # shellcheck disable=SC1090
  source <(tr -d '\r' < "$ENV_FILE")
  set +a
}

is_known_action() {
  case "$1" in
    up|up-build|up_build|restart|restart-build|restart_build|down|destroy|migrate|logs|reset-db|reset_db|config)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

normalize_profile() {
  local requested="${1:-}"
  requested="${requested,,}"

  if [[ -z "$requested" ]]; then
    requested="${LF_RUNTIME_PROFILE:-gpu}"
    requested="${requested,,}"
  fi

  case "$requested" in
    cpu|gpu|cloud)
      printf '%s\n' "$requested"
      ;;
    dev)
      printf 'gpu\n'
      ;;
    stg|prod)
      printf 'cloud\n'
      ;;
    *)
      echo "ERROR: Unknown profile '$requested'. Use cpu | gpu | cloud."
      exit 1
      ;;
  esac
}

usage() {
  cat <<'USAGE'
Usage:
  ./manage_label_forge.sh [profile] <action> [extra docker compose args]

Profiles:
  cpu    - local CPU development
  gpu    - local GPU workstation
  cloud  - single-host self-hosting

Legacy aliases:
  dev -> gpu
  stg -> cloud
  prod -> cloud

Actions:
  up                - start stack (docker compose up -d)
  up-build          - start stack with rebuild (up -d --build)
  restart           - restart API container only
  restart-build     - rebuild and restart the selected profile
  down              - stop stack (docker compose down)
  destroy           - stop stack and remove volumes (docker compose down -v)
  migrate           - run Alembic migrations (alembic upgrade head)
  logs              - follow API logs and save them to logs/
  reset-db          - drop DB volumes, rebuild, and migrate again
  config            - print the effective docker compose config

Environment:
  LF_ENV_FILE                    optional alternate env file path
  LF_RUNTIME_PROFILE             default profile when profile arg is omitted
  LF_RUN_MIGRATIONS_ON_START     run alembic after up/up-build/restart-build (default: 1)
  LF_DOCKER_PRUNE_INTERVAL_HOURS hours between automatic prune runs (default: 24)
  LF_SKIP_DOCKER_PRUNE=1         disable automatic prune before startup

Examples:
  cp .env.example .env
  ./manage_label_forge.sh up-build

  cp .env.cpu.example .env
  ./manage_label_forge.sh up-build

  cp .env.gpu.example .env
  ./manage_label_forge.sh gpu logs

  cp .env.cloud.example .env
  ./manage_label_forge.sh cloud up-build
USAGE
}

get_docker_prune_interval_hours() {
  local interval_hours="${LF_DOCKER_PRUNE_INTERVAL_HOURS:-24}"

  if [[ "$interval_hours" =~ ^[0-9]+$ ]] && (( interval_hours >= 1 )); then
    printf '%s\n' "$interval_hours"
    return 0
  fi

  echo "[WARN] Invalid LF_DOCKER_PRUNE_INTERVAL_HOURS='$interval_hours'; using 24."
  printf '24\n'
}

record_docker_prune_timestamp() {
  mkdir -p "$(dirname "$DOCKER_PRUNE_STATE_FILE")"
  date +%s > "$DOCKER_PRUNE_STATE_FILE"
}

maybe_auto_prune_before_start() {
  local interval_hours last_run now next_run

  if [[ "${LF_SKIP_DOCKER_PRUNE:-0}" == "1" ]]; then
    echo "[INFO] Skipping Docker auto-prune because LF_SKIP_DOCKER_PRUNE=1."
    return 0
  fi

  interval_hours="$(get_docker_prune_interval_hours)"

  if [[ -f "$DOCKER_PRUNE_STATE_FILE" ]]; then
    last_run="$(<"$DOCKER_PRUNE_STATE_FILE")"
    if [[ "$last_run" =~ ^[0-9]+$ ]]; then
      now="$(date +%s)"
      next_run=$((last_run + interval_hours * 3600))
      if (( now < next_run )); then
        echo "[INFO] Skipping Docker auto-prune; last run was within ${interval_hours} hours."
        return 0
      fi
    fi
  fi

  echo "[INFO] Auto-pruning unused Docker images..."
  if ! docker image prune -af; then
    echo "[WARN] Failed to prune unused Docker images; continuing."
  fi

  echo "[INFO] Auto-pruning unused Docker build cache..."
  if ! docker builder prune -af; then
    echo "[WARN] Failed to prune unused Docker build cache; continuing."
  fi

  record_docker_prune_timestamp
}

prune_build_artifacts() {
  echo "[INFO] Pruning dangling Docker images..."
  if ! docker image prune -f; then
    echo "[WARN] Failed to prune dangling Docker images; continuing."
  fi
}

should_run_migrations_on_start() {
  [[ "${LF_RUN_MIGRATIONS_ON_START:-1}" == "1" ]]
}

build_api_image() {
  echo "[INFO] Building API image for '$PROFILE' profile..."
  compose_cmd build api
}

start_database_service() {
  echo "[INFO] Starting database for '$PROFILE' profile..."
  compose_cmd up -d db
}

start_api_service() {
  local recreate_flag="${1:-0}"
  shift || true

  if [[ "$recreate_flag" == "1" ]]; then
    compose_cmd up -d --force-recreate "$@" api
    return
  fi

  compose_cmd up -d "$@" api
}

load_env_file

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

PROFILE_INPUT=""
ACTION=""

if is_known_action "$1"; then
  ACTION="$1"
  shift
else
  if [[ $# -lt 2 ]]; then
    usage
    exit 1
  fi
  PROFILE_INPUT="$1"
  ACTION="$2"
  shift 2
fi

PROFILE="$(normalize_profile "$PROFILE_INPUT")"
if [[ -n "$PROFILE_INPUT" && -n "${LF_RUNTIME_PROFILE:-}" ]]; then
  ENV_PROFILE="$(normalize_profile "${LF_RUNTIME_PROFILE:-}")"
  if [[ "$ENV_PROFILE" != "$PROFILE" ]]; then
    echo "[WARN] CLI profile '$PROFILE' overrides LF_RUNTIME_PROFILE='$ENV_PROFILE' from $ENV_FILE."
    echo "[WARN] Update the env file as well so profile-specific settings stay aligned."
  fi
fi
PROJECT_NAME="${LF_PROJECT_NAME:-label-forge-${PROFILE}}"
COMPOSE_BASE_FILE="infra/compose.base.yaml"
COMPOSE_PROFILE_FILE="infra/compose.${PROFILE}.yaml"

if [[ ! -f "$COMPOSE_BASE_FILE" ]]; then
  echo "ERROR: Compose file not found: $COMPOSE_BASE_FILE"
  exit 1
fi

if [[ ! -f "$COMPOSE_PROFILE_FILE" ]]; then
  echo "ERROR: Compose file not found: $COMPOSE_PROFILE_FILE"
  exit 1
fi

COMPOSE_FILE_ARGS=(-f "$COMPOSE_BASE_FILE" -f "$COMPOSE_PROFILE_FILE")
ENV_FILE_ARGS=()

if [[ -f "$ENV_FILE" ]]; then
  ENV_FILE_ARGS=(--env-file "$ENV_FILE")
else
  echo "[WARN] Env file not found at $ENV_FILE; using compose defaults."
fi

compose_cmd() {
  "${DOCKER_COMPOSE[@]}" "${ENV_FILE_ARGS[@]}" -p "$PROJECT_NAME" "${COMPOSE_FILE_ARGS[@]}" "$@"
}

get_api_image_id() {
  local container_id
  container_id="$(compose_cmd ps -q api 2>/dev/null | head -n 1)"
  if [[ -z "$container_id" ]]; then
    return 0
  fi
  docker inspect -f '{{.Image}}' "$container_id" 2>/dev/null || true
}

remove_previous_api_image() {
  local old_api_image_id="$1"
  local new_api_image_id

  if [[ -z "$old_api_image_id" ]]; then
    return 0
  fi

  new_api_image_id="$(get_api_image_id)"
  if [[ -z "$new_api_image_id" || "$old_api_image_id" == "$new_api_image_id" ]]; then
    return 0
  fi

  echo "[INFO] Removing previous API image: $old_api_image_id"
  if ! docker image rm -f "$old_api_image_id"; then
    echo "[WARN] Failed to remove previous API image; it may still be in use."
  fi
}

run_migrations() {
  echo "[INFO] Running Alembic migrations for profile '$PROFILE'..."
  compose_cmd run --rm api alembic upgrade head
}

run_migrations_if_enabled() {
  if ! should_run_migrations_on_start; then
    return 0
  fi
  run_migrations
}

case "$ACTION" in
  up)
    maybe_auto_prune_before_start
    if should_run_migrations_on_start; then
      start_database_service
      run_migrations
      echo "[INFO] Starting API for '$PROFILE' profile..."
      start_api_service 0 "$@"
    else
      echo "[INFO] Starting '$PROFILE' profile (no rebuild)..."
      compose_cmd up -d "$@"
    fi
    ;;

  up-build|up_build)
    previous_api_image_id="$(get_api_image_id)"
    maybe_auto_prune_before_start
    if should_run_migrations_on_start; then
      build_api_image
      start_database_service
      run_migrations
      echo "[INFO] Starting API for '$PROFILE' profile..."
      start_api_service 1 "$@"
    else
      echo "[INFO] Starting '$PROFILE' profile with rebuild..."
      compose_cmd up -d --build "$@"
    fi
    remove_previous_api_image "$previous_api_image_id"
    prune_build_artifacts
    ;;

  restart)
    echo "[INFO] Restarting API container for '$PROFILE' profile..."
    compose_cmd restart api
    ;;

  restart-build|restart_build)
    previous_api_image_id="$(get_api_image_id)"
    maybe_auto_prune_before_start
    if should_run_migrations_on_start; then
      build_api_image
      start_database_service
      run_migrations
      echo "[INFO] Recreating API for '$PROFILE' profile..."
      start_api_service 1 "$@"
    else
      echo "[INFO] Rebuilding and restarting '$PROFILE' profile..."
      compose_cmd up -d --build "$@"
    fi
    remove_previous_api_image "$previous_api_image_id"
    prune_build_artifacts
    ;;

  down)
    echo "[INFO] Stopping '$PROFILE' profile..."
    compose_cmd down
    ;;

  destroy)
    echo "[WARN] Destroying '$PROFILE' profile (containers + volumes)..."
    echo "       This will remove DB volumes and data."
    read -r -p "Are you sure? (type 'yes' to continue): " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
      echo "[INFO] Aborted."
      exit 0
    fi
    compose_cmd down -v
    ;;

  migrate)
    run_migrations
    ;;

  logs)
    mkdir -p logs
    TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
    LOG_FILE="logs/label-forge-${PROFILE}-${TIMESTAMP}.log"
    echo "[INFO] Streaming API logs for '$PROFILE' profile..."
    echo "[INFO] Writing to: ${LOG_FILE}"
    echo "[INFO] Press Ctrl+C to stop."
    compose_cmd logs -f api "$@" | tee "$LOG_FILE"
    ;;

  reset-db|reset_db)
    previous_api_image_id="$(get_api_image_id)"
    echo "[WARN] Resetting DB for '$PROFILE' profile (containers + volumes)..."
    echo "       This will erase all database data."
    read -r -p "Type 'reset' to continue: " CONFIRM
    if [[ "$CONFIRM" != "reset" ]]; then
      echo "[INFO] Aborted."
      exit 0
    fi
    compose_cmd down -v
    maybe_auto_prune_before_start
    if should_run_migrations_on_start; then
      build_api_image
      start_database_service
      run_migrations
      echo "[INFO] Starting API for '$PROFILE' profile..."
      start_api_service 1 "$@"
    else
      compose_cmd up -d --build "$@"
    fi
    remove_previous_api_image "$previous_api_image_id"
    prune_build_artifacts
    ;;

  config)
    compose_cmd config "$@"
    ;;

  *)
    echo "ERROR: Unknown action: $ACTION"
    usage
    exit 1
    ;;
esac
