#!/usr/bin/env bash
# Generate one-use strong credentials for the isolated MES-189 real stack.
set -euo pipefail

MES189_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES189_ENV_FILE="${MES189_DIR}/stack.env"

if [[ -e "${MES189_ENV_FILE}" && "${1:-}" != "--force" ]]; then
  echo "stack.env already exists — leaving it untouched (--force to regenerate)." >&2
  exit 1
fi

gen_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

umask 077
{
  echo '# MES-189 real-stack e2e — generated strong random values; NEVER commit.'
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
  echo 'MESH_API_PORT=18640'
  echo 'MESH_WS_PORT=18641'
  echo 'MESH_FRONTEND_PORT=18650'
  echo 'MESH_STORAGE_PORT=19640'
  echo 'MESH_STORAGE_CONSOLE_PORT=19641'
  echo 'MESH_STORAGE_PUBLIC_ENDPOINT=http://minio:9000'
  echo 'MESH_APP_BASE_URL=http://127.0.0.1:18650'
} > "${MES189_ENV_FILE}"

echo "Wrote ${MES189_ENV_FILE} (mode $(stat -c '%a' "${MES189_ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${MES189_ENV_FILE}"))."
