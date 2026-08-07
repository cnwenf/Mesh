#!/usr/bin/env bash
# Build the isolated production-shaped stack, then run the four-combo browser
# walkthrough (desktop/phone x light/dark) against the real API/worker/gateway
# and synchronously remove every MES-189 container and volume on exit.
set -euo pipefail

MES189_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES189_ROOT="$(cd "${MES189_DIR}/../../.." && pwd)"
MES189_ENV_FILE="${MES189_DIR}/stack.env"
MES189_PROJECT="${MES189_COMPOSE_PROJECT:-mes189-real}"
MES189_FRONTEND_PORT="${MES189_FRONTEND_PORT:-18650}"
MES189_COMPOSE=(
  docker compose
  -p "${MES189_PROJECT}"
  -f "${MES189_ROOT}/docker-compose.yml"
  -f "${MES189_DIR}/compose.override.yml"
  --env-file "${MES189_ENV_FILE}"
)

if [[ ! -e "${MES189_ENV_FILE}" ]]; then
  "${MES189_DIR}/gen-stack-env.sh"
fi

cleanup() {
  "${MES189_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${MES189_COMPOSE[@]}" up -d --build

for _ in $(seq 1 240); do
  if curl --fail --silent --show-error "http://127.0.0.1:${MES189_FRONTEND_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${MES189_FRONTEND_PORT}/readyz" >/dev/null

# Storage and middleware remain private to the Compose network. Any published
# mapping is a hard failure, even if an image or local override changes later.
for service in postgres redis minio api gateway; do
  container="${MES189_PROJECT}-${service}-1"
  if [[ -n "$(docker port "${container}")" ]]; then
    echo "${container} unexpectedly publishes a host port" >&2
    docker port "${container}" >&2
    exit 1
  fi
done

cd "${MES189_ROOT}/frontend"
MES189_PLAYWRIGHT_ARGS=()
if [[ -n "${MES189_PLAYWRIGHT_PROJECT:-}" ]]; then
  MES189_PLAYWRIGHT_ARGS+=(--project "${MES189_PLAYWRIGHT_PROJECT}")
fi
MES189_FRONTEND_PORT="${MES189_FRONTEND_PORT}" \
MES189_PROJECT="${MES189_PROJECT}" \
  npx --yes --package=node@22.22.0 -- \
    node ./node_modules/@playwright/test/cli.js test \
    --config playwright.mes189.config.ts "${MES189_PLAYWRIGHT_ARGS[@]}"
