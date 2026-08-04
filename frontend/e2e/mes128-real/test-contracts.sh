#!/usr/bin/env bash
# Fast executable contracts for the MES-128/MES-159 real-stack harness.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${DIR}/../../.." && pwd)"
GENERATOR="${DIR}/gen-stack-env.sh"
RUNNER="${DIR}/run-e2e.sh"
PROJECT_SPEC="${ROOT}/frontend/e2e/real-mes159-projects.spec.ts"
KEYBOARD_SPEC="${ROOT}/frontend/e2e/real-mes128-keyboard.spec.ts"
CONFIG="${ROOT}/frontend/playwright.mes128-real.config.ts"
WORKFLOW="${ROOT}/.github/workflows/frontend.yml"
PACKAGE="${ROOT}/frontend/package.json"

fail() {
  echo "contract failed: $*" >&2
  exit 1
}

require_literal() {
  local file="$1"
  local literal="$2"
  grep -Fq -- "${literal}" "${file}" || fail "${file#"${ROOT}/"} is missing: ${literal}"
}

forbid_literal() {
  local file="$1"
  local literal="$2"
  if grep -Fq -- "${literal}" "${file}"; then
    fail "${file#"${ROOT}/"} still contains: ${literal}"
  fi
}

probe_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "${probe_dir}"
}
trap cleanup EXIT

# The public MES128 knob must publish the same loopback port through the
# generated Compose environment instead of only changing Playwright/curl.
cp "${GENERATOR}" "${probe_dir}/gen-stack-env.sh"
MES128_FRONTEND_PORT=28430 "${probe_dir}/gen-stack-env.sh" --force >/dev/null
require_literal "${probe_dir}/stack.env" 'MESH_FRONTEND_PORT=28430'
require_literal "${probe_dir}/stack.env" 'MESH_APP_BASE_URL=http://127.0.0.1:28430'

# Resolve the real Compose model, then expose only the frontend port projection
# to the assertion process. This catches publisher drift without printing the
# generated credentials contained in dependent service environments.
docker compose \
  -p mes128-contract-probe \
  -f "${ROOT}/docker-compose.yml" \
  -f "${DIR}/compose.override.yml" \
  --env-file "${probe_dir}/stack.env" \
  config --format json frontend | node -e '
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { input += chunk; });
    process.stdin.on("end", () => {
      const config = JSON.parse(input);
      const ports = config.services?.frontend?.ports ?? [];
      const matches = ports.some((port) =>
        port.host_ip === "127.0.0.1" &&
        Number(port.target) === 80 &&
        String(port.published) === "28430"
      );
      if (!matches) {
        console.error("contract failed: Compose does not publish frontend as 127.0.0.1:28430->80");
        process.exit(1);
      }
    });
  '

# Existing stack.env files are also safe: the runner explicitly republishes the
# one public knob into Compose and passes the same value to Playwright/evidence.
require_literal "${RUNNER}" 'export MESH_FRONTEND_PORT="${FRONTEND_PORT}"'
require_literal "${RUNNER}" 'export MESH_APP_BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"'

# Successful project journeys must leave durable artifacts in the same evidence
# directory uploaded by CI, not only transient testInfo attachments.
require_literal "${PROJECT_SPEC}" 'process.env.MES128_EVIDENCE_DIR'
require_literal "${PROJECT_SPEC}" 'writeFile(join(EVIDENCE_DIR, screenshotFile), screenshot)'
require_literal "${PROJECT_SPEC}" 'writeFile(join(EVIDENCE_DIR, databaseFile), evidenceBody, '\''utf8'\'')'

# The evidence manifest describes the actual configured frontend publication.
forbid_literal "${KEYBOARD_SPEC}" "host_published_ports: ['127.0.0.1:18430->frontend:80/tcp']"
require_literal "${KEYBOARD_SPEC}" 'const FRONTEND_PORT = process.env.MES128_FRONTEND_PORT ?? '\''18430'\'';'
require_literal "${KEYBOARD_SPEC}" '`127.0.0.1:${FRONTEND_PORT}->frontend:80/tcp`'

# The real browser journey pins route-slug scoping at all three product edges:
# list/status reads, quick-create writes + PostgreSQL, and the default board.
require_literal "${KEYBOARD_SPEC}" 'secondIssuesLoadedPromise'
require_literal "${KEYBOARD_SPEC}" 'second_created_issue_in_first_workspace_count'
require_literal "${KEYBOARD_SPEC}" 'secondBoardLoadedPromise'
require_literal "${KEYBOARD_SPEC}" '07-second-workspace-board'

# Config and CI labels must describe both specs selected by testMatch.
require_literal "${CONFIG}" '键盘与项目页旅程'
require_literal "${WORKFLOW}" 'Run desktop/mobile real-stack keyboard, theme, and project journeys'
require_literal "${PACKAGE}" '"test:e2e:mes128-contracts": "./e2e/mes128-real/test-contracts.sh"'
require_literal "${WORKFLOW}" 'run: npm run test:e2e:mes128-contracts'
require_literal "${WORKFLOW}" 'MES128_FRONTEND_PORT: 28430'

echo 'MES-128 real-stack contracts passed.'
