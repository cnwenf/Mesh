#!/usr/bin/env bash
# Generate one-use strong credentials for the isolated MES-188 real stack.
set -euo pipefail

MES188_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES188_ENV_FILE="${MES188_DIR}/stack.env"

if [[ -e "${MES188_ENV_FILE}" && "${1:-}" != "--force" ]]; then
  echo "stack.env already exists — leaving it untouched (--force to regenerate)." >&2
  exit 1
fi

gen_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

umask 077
{
  echo '# MES-188 real-stack e2e — generated strong random values; NEVER commit.'
  echo 'MESH_POSTGRES_DB=mesh'
  echo 'MESH_POSTGRES_USER=mesh'
  echo "MESH_POSTGRES_PASSWORD=$(gen_secret)"
  echo "MESH_REDIS_PASSWORD=$(gen_secret)"
  echo "MESH_APP_DB_PASSWORD=$(gen_secret)"
  echo "MESH_STORAGE_ACCESS_KEY=$(gen_secret)"
  echo "MESH_STORAGE_SECRET_KEY=$(gen_secret)"
  echo "MESH_JWT_SECRET=$(gen_secret)"
  echo "MESH_DEVICE_CODE_PEPPER=$(gen_secret)"
  echo "MESH_SEARCH_CURSOR_SECRET=$(gen_secret)"
  echo 'MESH_AUTH_MODE=production'
  echo 'MESH_SESSION_COOKIE_SECURE=false'
  echo 'MESH_API_PORT=18620'
  echo 'MESH_WS_PORT=18621'
  echo 'MESH_FRONTEND_PORT=18630'
  echo 'MESH_STORAGE_PORT=19620'
  echo 'MESH_STORAGE_CONSOLE_PORT=19621'
  echo 'MESH_STORAGE_PUBLIC_ENDPOINT=http://minio:9000'
  echo 'MESH_APP_BASE_URL=http://127.0.0.1:18630'
} > "${MES188_ENV_FILE}"

echo "Wrote ${MES188_ENV_FILE} (mode $(stat -c '%a' "${MES188_ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${MES188_ENV_FILE}"))."
