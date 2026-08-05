#!/usr/bin/env bash
# Build an isolated production-auth stack, run MES-187, then remove its containers and volumes.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
FRONTEND_PORT="${MES187_FRONTEND_PORT:-18740}"
PROJECT="${MES187_COMPOSE_PROJECT:-}"

if [[ ! "${FRONTEND_PORT}" =~ ^[0-9]+$ ]] ||
  ((10#${FRONTEND_PORT} < 1 || 10#${FRONTEND_PORT} > 65535)); then
  echo "MES187_FRONTEND_PORT must be an integer between 1 and 65535." >&2
  exit 2
fi

if [[ -z "${PROJECT}" ]]; then
  PROJECT="mes187-real-$(openssl rand -hex 4)"
fi

RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mesh-mes187-real.XXXXXX")"
ENV_FILE="${RUNTIME_DIR}/stack.env"
COMPOSE=(
  docker compose
  -p "${PROJECT}"
  -f "${ROOT}/docker-compose.yml"
  -f "${ROOT}/frontend/e2e/mes128-real/compose.override.yml"
  --env-file "${ENV_FILE}"
)

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f -- "${ENV_FILE}"
  rmdir -- "${RUNTIME_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

MES187_FRONTEND_PORT="${FRONTEND_PORT}" "${DIR}/gen-stack-env.sh" "${ENV_FILE}"
"${COMPOSE[@]}" up -d --build

for _ in $(seq 1 150); do
  if curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_PORT}/readyz" >/dev/null

cd "${ROOT}/frontend"
MES187_FRONTEND_PORT="${FRONTEND_PORT}" \
MES187_PG_CONTAINER="${PROJECT}-postgres-1" \
  npx playwright test --config playwright.mes187.config.ts "$@"
