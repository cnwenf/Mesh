#!/usr/bin/env bash
# MES-128 真栈键盘旅程环境生成器:每次验收栈使用强随机凭据,仅绑定回环端口。
#
#   ./frontend/e2e/mes128-real/gen-stack-env.sh
#   ./frontend/e2e/mes128-real/gen-stack-env.sh --force
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DIR}/stack.env"

if [[ -e "${ENV_FILE}" && "${1:-}" != "--force" ]]; then
  echo "stack.env already exists — leaving it untouched (--force to regenerate)." >&2
  exit 1
fi

gen_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

umask 077
{
  echo '# MES-128 real-stack e2e — generated strong random values; NEVER commit.'
  echo 'MESH_POSTGRES_DB=mesh'
  echo 'MESH_POSTGRES_USER=mesh'
  echo "MESH_POSTGRES_PASSWORD=$(gen_secret)"
  echo "MESH_REDIS_PASSWORD=$(gen_secret)"
  echo "MESH_APP_DB_PASSWORD=$(gen_secret)"
  echo "MESH_STORAGE_ACCESS_KEY=$(gen_secret)"
  echo "MESH_STORAGE_SECRET_KEY=$(gen_secret)"
  echo "MESH_JWT_SECRET=$(gen_secret)"
  echo "MESH_DEVICE_CODE_PEPPER=$(gen_secret)"
  echo 'MESH_AUTH_MODE=production'
  echo 'MESH_SESSION_COOKIE_SECURE=false'
  echo 'MESH_API_PORT=18420'
  echo 'MESH_WS_PORT=18421'
  echo 'MESH_FRONTEND_PORT=18430'
  echo 'MESH_STORAGE_PORT=19420'
  echo 'MESH_STORAGE_CONSOLE_PORT=19421'
  echo 'MESH_STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:19420'
  echo 'MESH_APP_BASE_URL=http://127.0.0.1:18430'
} > "${ENV_FILE}"

echo "Wrote ${ENV_FILE} (mode $(stat -c '%a' "${ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${ENV_FILE}"))."
