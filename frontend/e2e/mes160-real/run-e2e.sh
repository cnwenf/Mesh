#!/usr/bin/env bash
# Build an isolated production-shaped stack, run MES-160, then remove its
# containers and volumes synchronously before returning.
set -euo pipefail

MES160_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES160_ROOT="$(cd "${MES160_DIR}/../../.." && pwd)"
MES160_ENV_FILE="${MES160_DIR}/stack.env"
MES160_PROJECT="${MES160_COMPOSE_PROJECT:-mes160-real}"
MES160_COMPOSE=(docker compose -p "${MES160_PROJECT}" -f "${MES160_ROOT}/docker-compose.yml" -f "${MES160_DIR}/compose.override.yml" --env-file "${MES160_ENV_FILE}")

if [[ ! -e "${MES160_ENV_FILE}" ]]; then
  "${MES160_DIR}/gen-stack-env.sh"
fi

cleanup() {
  "${MES160_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${MES160_COMPOSE[@]}" up -d --build

for _ in $(seq 1 180); do
  if curl --fail --silent --show-error http://127.0.0.1:18530/readyz >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18530/readyz >/dev/null

cd "${MES160_ROOT}/frontend"
MES160_FRONTEND_PORT=18530 \
MES160_PG_CONTAINER="${MES160_PROJECT}-postgres-1" \
  npx playwright test --config playwright.mes160-real.config.ts
