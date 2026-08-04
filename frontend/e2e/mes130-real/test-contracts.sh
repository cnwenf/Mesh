#!/usr/bin/env bash
# Fast security and lifecycle contracts for the MES-130 real-stack harness.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
GENERATOR="${DIR}/gen-stack-env.sh"
RUNNER="${DIR}/run-e2e.sh"

fail() {
  echo "contract failed: $*" >&2
  exit 1
}

require_literal() {
  local file="$1"
  local literal="$2"
  grep -Fq -- "${literal}" "${file}" || fail "${file#"${ROOT}/"} is missing: ${literal}"
}

probe_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "${probe_dir}"
}
trap cleanup EXIT

bash -n "${GENERATOR}" "${RUNNER}"
MES130_FRONTEND_PORT=38440 "${GENERATOR}" "${probe_dir}/stack.env"

[[ "$(stat -c '%a' "${probe_dir}/stack.env" 2>/dev/null || stat -f '%Lp' "${probe_dir}/stack.env")" == '600' ]] ||
  fail 'generated credential file must have mode 600'

for key in MESH_POSTGRES_PASSWORD MESH_REDIS_PASSWORD MESH_APP_DB_PASSWORD \
  MESH_STORAGE_ACCESS_KEY MESH_STORAGE_SECRET_KEY MESH_JWT_SECRET \
  MESH_DEVICE_CODE_PEPPER MESH_SEARCH_CURSOR_SECRET; do
  value="$(sed -n "s/^${key}=//p" "${probe_dir}/stack.env")"
  [[ ${#value} -ge 40 ]] || fail "${key} is not a strong generated value"
done

docker compose \
  -p mes130-contract-probe \
  -f "${ROOT}/docker-compose.yml" \
  -f "${ROOT}/frontend/e2e/mes128-real/compose.override.yml" \
  --env-file "${probe_dir}/stack.env" \
  config --format json | node -e '
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { input += chunk; });
    process.stdin.on("end", () => {
      const services = JSON.parse(input).services ?? {};
      for (const name of ["postgres", "redis", "minio", "api", "gateway", "worker"]) {
        if ((services[name]?.ports ?? []).length !== 0) {
          console.error(`contract failed: ${name} must not publish a host port`);
          process.exit(1);
        }
      }
      const ports = services.frontend?.ports ?? [];
      const safe = ports.length === 1 && ports[0].host_ip === "127.0.0.1" &&
        Number(ports[0].target) === 80 && String(ports[0].published) === "38440";
      if (!safe) {
        console.error("contract failed: frontend must publish only 127.0.0.1:38440->80");
        process.exit(1);
      }
    });
  '

require_literal "${RUNNER}" 'trap cleanup EXIT'
require_literal "${RUNNER}" 'down --volumes --remove-orphans'
require_literal "${RUNNER}" 'PROJECT="mes130-real-$(openssl rand -hex 4)"'
require_literal "${RUNNER}" 'MES130_API_BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"'
require_literal "${ROOT}/frontend/package.json" '"test:e2e:mes130": "./e2e/mes130-real/run-e2e.sh"'
require_literal "${ROOT}/.github/workflows/frontend.yml" 'run: npm run test:e2e:mes130-contracts'

echo 'MES-130 real-stack contracts passed.'
