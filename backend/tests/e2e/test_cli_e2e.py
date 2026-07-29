"""REAL end-to-end for the mesh CLI (cli.md §5.1).

The CLI runs as a genuine subprocess (`python -m meshcli.main`) against the
real uvicorn API server + PostgreSQL + Redis. Covers: the PAT login chain,
the device-code login chain (approved through the API, workspace bound at
approval becomes the CLI default), the exit-code contract, the single-JSON
output contract, --jq, and the workspace command family over the wire.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLI_SRC = REPO_ROOT / "cli" / "src"
EMAIL = "cli-e2e@corp.com"
PASSWORD = "a-strong-passw0rd"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _cli_env(api_url: str, config_dir: Path) -> dict:
    env = os.environ.copy()
    env["MESH_CONFIG"] = str(config_dir)
    env["MESH_API_URL"] = api_url
    # The e2e server is plaintext loopback — the deliberate exception flag.
    env["PYTHONPATH"] = f"{CLI_SRC}{os.pathsep}{env.get('PYTHONPATH', '')}"
    return env


def run_cli(api_url: str, config_dir: Path, *args: str, stdin: str | None = None,
            timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "meshcli.main", *args],
        env=_cli_env(api_url, config_dir),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def _web_login(api_url: str, email: str) -> tuple[str, httpx.Cookies]:
    async with httpx.AsyncClient(base_url=api_url) as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": "CLI-E2E"},
        )
        resp = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        access = resp.json()["data"]["access_token"]
        return access, client.cookies


async def _create_pat(api_url: str, access: str) -> str:
    async with httpx.AsyncClient(base_url=api_url, headers={"Authorization": f"Bearer {access}"}) as client:
        me = (await client.get("/api/v1/me")).json()["data"]
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "CLI WS", "slug": f"cli-{uuid.uuid4().hex[:8]}"},
            )
        ).json()["data"]
        created = (
            await client.post(
                f"/api/v1/workspaces/{ws['id']}/api-tokens",
                json={
                    "name": "cli-e2e-pat",
                    "scopes": ["issue:read", "issue:write", "comment:write"],
                },
            )
        ).json()["data"]
        return created["token"], ws


class TestPatChain:
    async def test_pat_login_status_issue_family_logout(self, api_server, tmp_path):
        api_url = api_server.base_url
        config_dir = tmp_path / "mesh-config"
        email = f"cli-pat-{uuid.uuid4().hex[:8]}@corp.com"
        access, _ = await _web_login(api_url, email)
        pat, ws = await _create_pat(api_url, access)

        # PAT enters via stdin — never an argument (cli.md C1/§5.3).
        login = run_cli(api_url, config_dir, "--insecure", "auth", "login", "--with-token", stdin=pat)
        assert login.returncode == 0, login.stderr

        # Status shows masked credential info, no plaintext.
        status = run_cli(api_url, config_dir, "--insecure", "auth", "status", "--output", "json")
        assert status.returncode == 0, status.stderr
        status_data = json.loads(status.stdout)["data"]
        assert status_data["kind"] == "pat"
        assert pat not in status.stdout

        # Issue family over the wire with the PAT.
        created = run_cli(
            api_url, config_dir, "--insecure", "--workspace", ws["slug"],
            "--output", "json", "issue", "create", "--title", "CLI 创建的 issue",
        )
        assert created.returncode == 0, created.stderr
        issue = json.loads(created.stdout)["data"]
        assert issue["title"] == "CLI 创建的 issue"

        got = run_cli(
            api_url, config_dir, "--insecure", "--output", "json",
            "issue", "get", issue["id"],
        )
        assert got.returncode == 0, got.stderr
        assert json.loads(got.stdout)["data"]["id"] == issue["id"]

        listed = run_cli(
            api_url, config_dir, "--insecure", "--workspace", ws["slug"],
            "--output", "json", "--jq", ".[] | .identifier", "issue", "list",
        )
        assert listed.returncode == 0, listed.stderr
        assert issue["identifier"] in [line.strip('"') for line in listed.stdout.splitlines()]

        # Logout (PAT: local clear by default).
        logout = run_cli(api_url, config_dir, "--insecure", "auth", "logout")
        assert logout.returncode == 0, logout.stderr
        # Now unauthenticated → exit 2 (auth-exclusive).
        after = run_cli(api_url, config_dir, "--insecure", "--workspace", ws["slug"], "issue", "list")
        assert after.returncode == 2, after.stderr

    async def test_exit_code_contract_over_wire(self, api_server, tmp_path):
        api_url = api_server.base_url
        config_dir = tmp_path / "mesh-config2"

        # Unauthenticated → 2.
        unauth = run_cli(api_url, config_dir, "--insecure", "auth", "status")
        assert unauth.returncode == 2

        # Unknown command / unknown flag → 3 (usage, never 2).
        assert run_cli(api_url, config_dir, "frobnicate").returncode == 3
        assert run_cli(api_url, config_dir, "issue", "--bogus-flag").returncode == 3

        # Authenticated 404 → 3.
        email = f"cli-exit-{uuid.uuid4().hex[:8]}@corp.com"
        access, _ = await _web_login(api_url, email)
        pat, ws = await _create_pat(api_url, access)
        login = run_cli(api_url, config_dir, "--insecure", "auth", "login", "--with-token", stdin=pat)
        assert login.returncode == 0
        missing = run_cli(
            api_url, config_dir, "--insecure", "issue", "get", str(uuid.uuid4())
        )
        assert missing.returncode == 3, missing.stderr


class TestDeviceChain:
    async def test_device_login_binds_approved_workspace(self, api_server, tmp_path):
        """Golden path: CLI issues → approves via API → polls to success; the
        workspace chosen at approval becomes the CLI default (cli.md §4.2)."""
        api_url = api_server.base_url
        config_dir = tmp_path / "mesh-config3"
        email = f"cli-dev-{uuid.uuid4().hex[:8]}@corp.com"

        # Approver web session + a workspace to bind.
        async with httpx.AsyncClient(base_url=api_url) as client:
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": PASSWORD, "display_name": "Approver"},
            )
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            access = login.json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {access}"}
            ws = (
                await client.post(
                    "/api/v1/workspaces",
                    json={"name": "DEV WS", "slug": f"devws-{uuid.uuid4().hex[:8]}"},
                    headers=headers,
                )
            ).json()["data"]

            # The CLI login polls in a subprocess; approve through the API.
            proc = subprocess.Popen(
                [sys.executable, "-m", "meshcli.main", "--insecure", "auth", "login"],
                env=_cli_env(api_url, config_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Wait for the user code to appear on stderr, then approve.
            user_code = None
            deadline = 30
            import time

            start = time.monotonic()
            buffer = ""
            while time.monotonic() - start < deadline:
                line = proc.stderr.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                buffer += line
                if "Code:" in line:
                    user_code = line.split("Code:")[-1].strip()
                    break
            assert user_code is not None, f"no user code printed:\n{buffer}"

            approved = await client.post(
                "/api/v1/auth/device/approve",
                json={"user_code": user_code, "workspace_id": ws["id"]},
                headers=headers,
            )
            assert approved.status_code == 200, approved.text

            out, err = proc.communicate(timeout=60)
            assert proc.returncode == 0, f"stdout={out}\nstderr={err}"

        # The approved workspace is now the CLI default (no second selection).
        config_file = config_dir / "config.yaml"
        assert config_file.exists()
        import yaml

        stored = yaml.safe_load(config_file.read_text())
        assert stored.get("workspace") == ws["slug"]

        # And the CLI works without --workspace (default resolves).
        status = run_cli(api_url, config_dir, "--insecure", "auth", "status", "--output", "json")
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["data"]["kind"] == "session"
        listed = run_cli(api_url, config_dir, "--insecure", "--output", "json", "issue", "list")
        assert listed.returncode == 0, listed.stderr
