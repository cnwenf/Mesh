#!/usr/bin/env bash
# Generate one throwaway production-auth environment for the MES-187 browser journey.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "${2:-}" != "--force" ) ]]; then
  echo "usage: $0 OUTPUT_FILE [--force]" >&2
  exit 2
fi

ENV_FILE="$1"
FRONTEND_PORT="${MES187_FRONTEND_PORT:-18740}"

if [[ ! "${FRONTEND_PORT}" =~ ^[0-9]+$ ]] ||
  ((10#${FRONTEND_PORT} < 1 || 10#${FRONTEND_PORT} > 65535)); then
  echo "MES187_FRONTEND_PORT must be an integer between 1 and 65535." >&2
  exit 2
fi

if [[ -L "${ENV_FILE}" ]]; then
  echo "refusing to write credentials through a symbolic link: ${ENV_FILE}" >&2
  exit 1
fi
if [[ -e "${ENV_FILE}" && "${2:-}" != "--force" ]]; then
  echo "${ENV_FILE} already exists; pass --force to replace it." >&2
  exit 1
fi
if [[ ! -d "$(dirname "${ENV_FILE}")" ]]; then
  echo "parent directory does not exist: $(dirname "${ENV_FILE}")" >&2
  exit 1
fi

gen_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

umask 077
{
  echo '# MES-187 throwaway real-stack credentials. Never commit this file.'
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
  echo 'MESH_API_PORT=18741'
  echo 'MESH_WS_PORT=18742'
  echo "MESH_FRONTEND_PORT=${FRONTEND_PORT}"
  echo 'MESH_STORAGE_PORT=19740'
  echo 'MESH_STORAGE_CONSOLE_PORT=19741'
  echo 'MESH_STORAGE_PUBLIC_ENDPOINT=http://minio:9000'
  echo "MESH_APP_BASE_URL=http://127.0.0.1:${FRONTEND_PORT}"
} > "${ENV_FILE}"

chmod 600 "${ENV_FILE}"
