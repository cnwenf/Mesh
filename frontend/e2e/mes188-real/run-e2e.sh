#!/usr/bin/env bash
# Build the isolated production-shaped stack, run the real daemon/provider and
# browser matrix, then synchronously remove every MES-188 container and volume.
set -euo pipefail

MES188_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES188_ROOT="$(cd "${MES188_DIR}/../../.." && pwd)"
MES188_ENV_FILE="${MES188_DIR}/stack.env"
MES188_PROJECT="${MES188_COMPOSE_PROJECT:-mes188-real}"
MES188_FRONTEND_PORT="${MES188_FRONTEND_PORT:-18630}"
MES188_COMPOSE=(
  docker compose
  -p "${MES188_PROJECT}"
  -f "${MES188_ROOT}/docker-compose.yml"
  -f "${MES188_DIR}/compose.override.yml"
  --env-file "${MES188_ENV_FILE}"
)

if [[ ! -e "${MES188_ENV_FILE}" ]]; then
  "${MES188_DIR}/gen-stack-env.sh"
fi

cleanup() {
  "${MES188_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${MES188_COMPOSE[@]}" up -d --build

for _ in $(seq 1 180); do
  if curl --fail --silent --show-error "http://127.0.0.1:${MES188_FRONTEND_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${MES188_FRONTEND_PORT}/readyz" >/dev/null

# Storage and middleware remain private to the Compose network. Any published
# mapping is a hard failure, even if an image or local override changes later.
for service in postgres redis minio api gateway; do
  container="${MES188_PROJECT}-${service}-1"
  if [[ -n "$(docker port "${container}")" ]]; then
    echo "${container} unexpectedly publishes a host port" >&2
    docker port "${container}" >&2
    exit 1
  fi
done

cd "${MES188_ROOT}"
# The sandbox helper deliberately clears PYTHONPATH before its privileged
# handshake. Install this checkout's daemon package into the test venv so the
# helper imports the exact code under test inside that clean environment.
if ! "${MES188_ROOT}/backend/.venv/bin/python" -c 'import mesh_runtime' >/dev/null 2>&1; then
  "${MES188_ROOT}/backend/.venv/bin/pip" install --no-deps -e "${MES188_ROOT}/daemon"
fi
if [[ "${MES188_SKIP_PROVIDER:-0}" != "1" ]]; then
  PYTHONPATH="${MES188_ROOT}/daemon/src" \
  MES101_WORK_ROOT="/tmp/mes188-real-provider" \
    "${MES188_ROOT}/backend/.venv/bin/python" \
    daemon/tests/integration/mes188_real_llm_e2e.py \
    "http://127.0.0.1:${MES188_FRONTEND_PORT}"
fi

cd "${MES188_ROOT}/frontend"
MES188_PLAYWRIGHT_ARGS=()
if [[ -n "${MES188_PLAYWRIGHT_PROJECT:-}" ]]; then
  MES188_PLAYWRIGHT_ARGS+=(--project "${MES188_PLAYWRIGHT_PROJECT}")
fi
MES188_FRONTEND_PORT="${MES188_FRONTEND_PORT}" \
MES188_API_CONTAINER="${MES188_PROJECT}-api-1" \
MES188_PG_CONTAINER="${MES188_PROJECT}-postgres-1" \
  npx --yes --package=node@22.22.0 -- \
    node ./node_modules/@playwright/test/cli.js test \
    --config playwright.mes188.config.ts "${MES188_PLAYWRIGHT_ARGS[@]}"
