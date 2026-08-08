#!/usr/bin/env bash
# Generate one-use strong credentials for the isolated MES-193 real stack.
set -euo pipefail

MES193_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES193_ENV_FILE="${MES193_DIR}/stack.env"

if [[ -e "${MES193_ENV_FILE}" && "${1:-}" != "--force" ]]; then
  echo "stack.env already exists — leaving it untouched (--force to regenerate)." >&2
  exit 1
fi

gen_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

umask 077
{
  echo '# MES-193 real-stack e2e — generated strong random values; NEVER commit.'
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
  echo 'MESH_API_PORT=18750'
  echo 'MESH_WS_PORT=18751'
  echo 'MESH_FRONTEND_PORT=18752'
  echo 'MESH_STORAGE_PORT=18753'
  echo 'MESH_STORAGE_CONSOLE_PORT=18754'
  echo 'MESH_STORAGE_PUBLIC_ENDPOINT=http://minio:9000'
  echo 'MESH_APP_BASE_URL=http://127.0.0.1:18752'
} > "${MES193_ENV_FILE}"

echo "Wrote ${MES193_ENV_FILE} (mode $(stat -c '%a' "${MES193_ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${MES193_ENV_FILE}"))."
