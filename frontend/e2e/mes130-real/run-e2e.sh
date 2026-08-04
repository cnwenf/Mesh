#!/usr/bin/env bash
# Build an isolated production-auth stack, run the 2-D board journey, then destroy it.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
FRONTEND_PORT="${MES130_FRONTEND_PORT:-28440}"
PROJECT="${MES130_COMPOSE_PROJECT:-}"

if [[ ! "${FRONTEND_PORT}" =~ ^[0-9]+$ ]] ||
  ((10#${FRONTEND_PORT} < 1 || 10#${FRONTEND_PORT} > 65535)); then
  echo "MES130_FRONTEND_PORT must be an integer between 1 and 65535." >&2
  exit 2
fi

if [[ -z "${PROJECT}" ]]; then
  PROJECT="mes130-real-$(openssl rand -hex 4)"
fi

RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mesh-mes130-real.XXXXXX")"
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

MES130_FRONTEND_PORT="${FRONTEND_PORT}" "${DIR}/gen-stack-env.sh" "${ENV_FILE}"
"${COMPOSE[@]}" up -d --build

for _ in $(seq 1 120); do
  if curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_PORT}/readyz" >/dev/null

cd "${ROOT}/frontend"
MES130_BASE_URL="http://127.0.0.1:${FRONTEND_PORT}" \
MES130_API_BASE_URL="http://127.0.0.1:${FRONTEND_PORT}" \
  npx playwright test --config playwright.mes130.config.ts
