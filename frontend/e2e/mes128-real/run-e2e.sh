#!/usr/bin/env bash
# 一条命令拉起隔离真栈、执行 1440/320/390px 键盘旅程并回收专属容器/卷。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
ENV_FILE="${DIR}/stack.env"
PROJECT="${MES128_COMPOSE_PROJECT:-mes128-real}"
COMPOSE=(docker compose -p "${PROJECT}" -f "${ROOT}/docker-compose.yml" -f "${DIR}/compose.override.yml" --env-file "${ENV_FILE}")

if [[ ! -e "${ENV_FILE}" ]]; then
  "${DIR}/gen-stack-env.sh"
fi

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --build

for _ in $(seq 1 120); do
  if curl --fail --silent --show-error http://127.0.0.1:18430/readyz >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18430/readyz >/dev/null

cd "${ROOT}/frontend"
MES128_FRONTEND_PORT=18430 \
MES128_PG_CONTAINER="${PROJECT}-postgres-1" \
  npx playwright test --config playwright.mes128-real.config.ts
