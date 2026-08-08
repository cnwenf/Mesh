#!/usr/bin/env bash
# Build the isolated production-shaped stack, then run the four-combo browser
# walkthrough (desktop/phone x light/dark) against the real API/worker/gateway
# and synchronously remove every MES-193 container and volume on exit.
set -euo pipefail

MES193_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES193_ROOT="$(cd "${MES193_DIR}/../../.." && pwd)"
MES193_ENV_FILE="${MES193_DIR}/stack.env"
MES193_PROJECT="${MES193_COMPOSE_PROJECT:-mes193-real}"
MES193_FRONTEND_PORT="${MES193_FRONTEND_PORT:-18752}"
MES193_COMPOSE=(
  docker compose
  -p "${MES193_PROJECT}"
  -f "${MES193_ROOT}/docker-compose.yml"
  -f "${MES193_DIR}/compose.override.yml"
  --env-file "${MES193_ENV_FILE}"
)

if [[ ! -e "${MES193_ENV_FILE}" ]]; then
  "${MES193_DIR}/gen-stack-env.sh"
fi

cleanup() {
  "${MES193_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${MES193_COMPOSE[@]}" up -d --build

for _ in $(seq 1 240); do
  if curl --fail --silent --show-error "http://127.0.0.1:${MES193_FRONTEND_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${MES193_FRONTEND_PORT}/readyz" >/dev/null

# Storage and middleware remain private to the Compose network. Any published
# mapping is a hard failure, even if an image or local override changes later.
for service in postgres redis minio api gateway; do
  container="${MES193_PROJECT}-${service}-1"
  if [[ -n "$(docker port "${container}")" ]]; then
    echo "${container} unexpectedly publishes a host port" >&2
    docker port "${container}" >&2
    exit 1
  fi
done

cd "${MES193_ROOT}/frontend"
MES193_PLAYWRIGHT_ARGS=()
if [[ -n "${MES193_PLAYWRIGHT_PROJECT:-}" ]]; then
  MES193_PLAYWRIGHT_ARGS+=(--project "${MES193_PLAYWRIGHT_PROJECT}")
fi
MES193_FRONTEND_PORT="${MES193_FRONTEND_PORT}" \
MES193_PROJECT="${MES193_PROJECT}" \
  npx --yes --package=node@22.22.0 -- \
    node ./node_modules/@playwright/test/cli.js test \
    --config playwright.mes193.config.ts "${MES193_PLAYWRIGHT_ARGS[@]}"
