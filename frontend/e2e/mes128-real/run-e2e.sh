#!/usr/bin/env bash
# 一条命令拉起隔离真栈，执行键盘/主题与全局辅助页旅程并回收专属容器/卷。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
ENV_FILE="${DIR}/stack.env"
PROJECT="${MES128_COMPOSE_PROJECT:-mes128-real}"
COMPOSE=(docker compose -p "${PROJECT}" -f "${ROOT}/docker-compose.yml" -f "${DIR}/compose.override.yml" --env-file "${ENV_FILE}")

if [[ ! -e "${ENV_FILE}" ]]; then
  "${DIR}/gen-stack-env.sh"
fi

read_env_value() {
  local name="$1"
  local file="$2"
  local line
  local seen=0
  local value=""

  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" == "${name}="* ]]; then
      if (( seen )); then
        echo "${name} must appear exactly once in ${file}" >&2
        return 1
      fi
      seen=1
      value="${line#*=}"
    fi
  done < "${file}"

  if (( ! seen )) || [[ -z "${value}" ]]; then
    echo "${name} is required in ${file}" >&2
    return 1
  fi
  printf '%s' "${value}"
}

configured_port() {
  local name="$1"
  local value="$2"

  if [[ ! "${value}" =~ ^[0-9]{1,5}$ ]] || (( 10#${value} < 1 || 10#${value} > 65535 )); then
    echo "${name} must be an integer TCP port" >&2
    return 1
  fi
  printf '%s' "$((10#${value}))"
}

# Shell variables override Compose's env file, so resolve the frontend port with
# the same precedence and pass that single value to readiness and both suites.
stack_frontend_port="$(read_env_value MESH_FRONTEND_PORT "${ENV_FILE}")"
frontend_port="$(configured_port MESH_FRONTEND_PORT "${MESH_FRONTEND_PORT:-${stack_frontend_port}}")"
export MESH_FRONTEND_PORT="${frontend_port}"

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --build

for _ in $(seq 1 120); do
  if curl --fail --silent --show-error "http://127.0.0.1:${frontend_port}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${frontend_port}/readyz" >/dev/null

cd "${ROOT}/frontend"
MES128_FRONTEND_PORT="${frontend_port}" \
MES128_PG_CONTAINER="${PROJECT}-postgres-1" \
  npx playwright test --config playwright.mes128-real.config.ts

# MES-161：同一安全真栈继续验收全局辅助页族；使用独立账号，互不污染前一旅程。
MES161_FRONTEND_PORT="${frontend_port}" \
MES161_PG_CONTAINER="${PROJECT}-postgres-1" \
  npx playwright test --config playwright.mes161.config.ts
