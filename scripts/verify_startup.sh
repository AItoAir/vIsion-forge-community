#!/usr/bin/env bash
# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.


set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  case "${value}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

normalize_profile() {
  local value="${1:-}"
  value="${value,,}"

  case "${value}" in
    "" ) echo "cpu" ;;
    cpu|gpu|cloud ) echo "${value}" ;;
    dev ) echo "gpu" ;;
    stg|prod ) echo "cloud" ;;
    * )
      echo "Unknown verification profile '${value}'. Use cpu, gpu, or cloud." >&2
      return 1
      ;;
  esac
}

REQUESTED_PROFILE="${1:-${LF_VERIFY_PROFILE:-${LF_RUNTIME_PROFILE:-cpu}}}"
PROFILE="$(normalize_profile "${REQUESTED_PROFILE}")"

CANONICAL_ENV_FILE="${LF_ENV_FILE:-${REPO_ROOT}/.env}"
EXAMPLE_ENV_FILE="${REPO_ROOT}/.env.${PROFILE}.example"
FALLBACK_ENV_FILE="${REPO_ROOT}/.env.example"
PORT="${LF_VERIFY_PORT:-${LF_PUBLIC_PORT:-8001}}"
TIMEOUT_SECONDS="${LF_VERIFY_TIMEOUT_SECONDS:-180}"
HEALTH_URL="http://127.0.0.1:${PORT}/healthz"
KEEP_RUNNING="${LF_VERIFY_KEEP_RUNNING:-0}"
SKIP_ENV_COPY="${LF_VERIFY_SKIP_ENV_COPY:-0}"
VERIFICATION_PASSED=0
VERIFICATION_PROJECT_NAME="frame-pin-verify-${PROFILE}-$$"
VERIFICATION_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/frame-pin-verify-${PROFILE}-XXXXXX.env")"
ORIGINAL_LF_ENV_FILE="${LF_ENV_FILE:-}"

ensure_env_file() {
  if [[ -f "${CANONICAL_ENV_FILE}" ]]; then
    echo "[INFO] Using existing env file: ${CANONICAL_ENV_FILE}"
    return 0
  fi

  mkdir -p "$(dirname "${CANONICAL_ENV_FILE}")"

  if [[ -f "${EXAMPLE_ENV_FILE}" ]]; then
    cp "${EXAMPLE_ENV_FILE}" "${CANONICAL_ENV_FILE}"
    echo "[INFO] Created env file from profile template: ${EXAMPLE_ENV_FILE}"
    return 0
  fi

  if [[ -f "${FALLBACK_ENV_FILE}" ]]; then
    cp "${FALLBACK_ENV_FILE}" "${CANONICAL_ENV_FILE}"
    echo "[INFO] Created env file from fallback template: ${FALLBACK_ENV_FILE}"
    return 0
  fi

  echo "ERROR: No .env file exists and no template was found for profile '${PROFILE}'." >&2
  return 1
}

write_verification_env_file() {
  if [[ -f "${CANONICAL_ENV_FILE}" ]]; then
    cp "${CANONICAL_ENV_FILE}" "${VERIFICATION_ENV_FILE}"
  else
    : > "${VERIFICATION_ENV_FILE}"
  fi

  {
    printf '\n'
    printf 'LF_PROJECT_NAME=%s\n' "${VERIFICATION_PROJECT_NAME}"
    printf 'LF_PUBLIC_PORT=%s\n' "${PORT}"
  } >> "${VERIFICATION_ENV_FILE}"

  export LF_ENV_FILE="${VERIFICATION_ENV_FILE}"
}

run_manage() {
  (
    cd "${REPO_ROOT}"
    ./manage_frame_pin.sh "${PROFILE}" "$@"
  )
}

check_health() {
  python - "${HEALTH_URL}" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]

try:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ok = isinstance(payload, dict) and payload.get("status") == "ok"
except Exception:
    ok = False

raise SystemExit(0 if ok else 1)
PY
}

list_verification_images() {
  local exclude_project_name="${1:-}"
  local temporary_prefixes=(
    "frame-pin-verify-"
    "frame-pin-review-comments-"
  )
  local repository tag prefix matches_prefix

  docker image ls --format '{{.Repository}}|{{.Tag}}' | while IFS='|' read -r repository tag; do
    [[ -n "${repository}" ]] || continue
    matches_prefix=0
    for prefix in "${temporary_prefixes[@]}"; do
      if [[ "${repository}" == "${prefix}"* ]]; then
        matches_prefix=1
        break
      fi
    done
    (( matches_prefix == 1 )) || continue
    if [[ -n "${exclude_project_name}" && "${repository}" == "${exclude_project_name}-"* ]]; then
      continue
    fi

    if [[ "${tag}" == "<none>" ]]; then
      printf '%s\n' "${repository}"
    else
      printf '%s:%s\n' "${repository}" "${tag}"
    fi
  done
}

remove_verification_images() {
  local exclude_project_name="${1:-}"
  local references=()
  local reference

  while IFS= read -r reference; do
    [[ -n "${reference}" ]] || continue
    references+=("${reference}")
  done < <(list_verification_images "${exclude_project_name}" | sort -u)

  if (( ${#references[@]} == 0 )); then
    echo "[INFO] No verification-only Docker images to remove."
    return 0
  fi

  for reference in "${references[@]}"; do
    echo "[INFO] Removing temporary Docker image: ${reference}"
    if ! docker image rm "${reference}" >/dev/null 2>&1; then
      echo "[WARN] Failed to remove temporary Docker image '${reference}'; it may still be in use."
    fi
  done

  if ! docker image prune -f >/dev/null 2>&1; then
    echo "[WARN] Failed to prune dangling Docker images after temporary image cleanup."
  fi
}

cleanup() {
  local cleanup_exit_code=$?

  if ! is_truthy "${KEEP_RUNNING}"; then
    echo "[INFO] Stopping verification stack..."
    run_manage down || true
  else
    echo "[INFO] Leaving the stack running because LF_VERIFY_KEEP_RUNNING is enabled."
  fi

  if is_truthy "${KEEP_RUNNING}"; then
    remove_verification_images "${VERIFICATION_PROJECT_NAME}"
  else
    remove_verification_images
  fi

  rm -f "${VERIFICATION_ENV_FILE}"

  if [[ -n "${ORIGINAL_LF_ENV_FILE}" ]]; then
    export LF_ENV_FILE="${ORIGINAL_LF_ENV_FILE}"
  else
    unset LF_ENV_FILE || true
  fi

  return "${cleanup_exit_code}"
}

trap cleanup EXIT

main() {
  if [[ ! -x "${REPO_ROOT}/manage_frame_pin.sh" ]]; then
    echo "ERROR: Management script was not found or is not executable: ${REPO_ROOT}/manage_frame_pin.sh" >&2
    return 1
  fi

  if ! is_truthy "${SKIP_ENV_COPY}"; then
    ensure_env_file
  fi

  write_verification_env_file

  export LF_SKIP_DOCKER_PRUNE="${LF_SKIP_DOCKER_PRUNE:-1}"

  echo "[INFO] Running FramePin startup verification..."
  echo "[INFO] Profile: ${PROFILE}"
  echo "[INFO] Project name: ${VERIFICATION_PROJECT_NAME}"
  echo "[INFO] Health URL: ${HEALTH_URL}"

  run_manage up-build

  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if check_health; then
      VERIFICATION_PASSED=1
      echo "[INFO] Health check passed: ${HEALTH_URL}"
      return 0
    fi
    sleep 2
  done

  echo "ERROR: Health check did not pass within ${TIMEOUT_SECONDS} seconds: ${HEALTH_URL}" >&2
  return 1
}

main
