"""Issue move/bulk authorization e2e — REAL server, REAL API calls, REAL DB
(MES-46 H1/H2 regression matrix, issue.md §3.8).

Every case drives genuine uvicorn subprocesses over HTTP with real members
(invited human accounts) at real roles — no mocks:

- private source issue: non-project-member member → 404, guest → 404 (LOW-S2
  anti-oracle convergence — a private project the viewer cannot see never
  leaks existence), and neither response may carry a preview plan (the leak
  surface);
- invisible private target: member → 403, guest → 404 (confirm=false too);
- foreign-workspace target → 404;
- bulk unconfirmed with mixed ids → error markers ONLY for unauthorized
  items, never a plan;
- authorized two-step move/bulk flows keep working and leave the §3.8 ⑥
  audit trail with the mapping/clearing manifest.
"""

from __future__ import annotations

import uuid

import pytest

_AUTH_PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _AUTH_PASSWORD, "display_name": "SecE2E"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": _AUTH_PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _invite_accept(client, owner_token, ws_id, email, role="member") -> tuple[str, str]:
    """Invite + accept; returns (joiner token, member id)."""
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    assert inv.status_code == 201, inv.text
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accepted.status_code in (200, 201), accepted.text
    return joiner, accepted.json()["data"]["member"]["id"]


async def _create_project(client, token, ws_id: str, key: str, visibility="public") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"Project {key}", "key": key, "visibility": visibility},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token, ws_id: str, **fields) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": "e2e issue", **fields},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _join_project(client, token, project_id: str, member_id: str) -> None:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"member_id": member_id, "role": "member"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


async def _grant(client, token, ws_id: str, member_id: str, project_id: str, permission: str) -> None:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/members/{member_id}/project-access",
        json={"project_id": project_id, "permission": permission},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


async def _security_setup(client, slug: str) -> dict:
    """Owner workspace with a private project (secret issue), a public
    project (open issue) and a destination project; a plain member and a
    guest, both OUTSIDERS of the private project. The member can write the
    public project and the destination; the guest can read the public
    project and write the destination."""
    suffix = uuid.uuid4().hex[:8]
    owner = await _register_and_login(client, f"sec-owner-{suffix}@corp.com")
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": "Sec E2E", "slug": f"{slug}-{suffix}"},
            headers=_auth(owner),
        )
    ).json()["data"]
    prv = await _create_project(client, owner, ws["id"], "PRV", visibility="private")
    pub = await _create_project(client, owner, ws["id"], "PUB")
    dst = await _create_project(client, owner, ws["id"], "DST")
    secret = await _create_issue(client, owner, ws["id"], project_id=prv["id"], title="secret")
    open_issue = await _create_issue(client, owner, ws["id"], project_id=pub["id"], title="open")

    member_token, member_id = await _invite_accept(
        client, owner, ws["id"], f"sec-member-{suffix}@corp.com", role="member"
    )
    guest_token, guest_id = await _invite_accept(
        client, owner, ws["id"], f"sec-guest-{suffix}@corp.com", role="guest"
    )
    await _join_project(client, owner, pub["id"], member_id)
    await _join_project(client, owner, dst["id"], member_id)
    await _grant(client, owner, ws["id"], guest_id, pub["id"], "read")
    await _grant(client, owner, ws["id"], guest_id, dst["id"], "write")
    return {
        "owner": owner,
        "member": member_token,
        "guest": guest_token,
        "ws": ws["id"],
        "prv": prv["id"],
        "pub": pub["id"],
        "dst": dst["id"],
        "secret": secret,
        "open": open_issue,
    }


def _assert_envelope_no_plan(resp) -> dict:
    """The §6.14 error envelope of a rejected request must not carry a plan."""
    error = resp.json()["error"]
    details = error.get("details") or {}
    assert "preview" not in details
    assert "previews" not in details
    return error


# ---------------------------------------------------------------------------
# H1: unconfirmed move + preview endpoint authorization matrix
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_move_unconfirmed_private_source_404_no_plan(api_client):
    ctx = await _security_setup(api_client, "sec-move-src")
    body = {"target_project_id": ctx["dst"], "confirm": False}
    # non-project-member member → 404 (LOW-S2 anti-oracle), no plan
    member_resp = await api_client.post(
        f"/api/v1/issues/{ctx['secret']['id']}/move", json=body, headers=_auth(ctx["member"])
    )
    assert member_resp.status_code == 404
    assert _assert_envelope_no_plan(member_resp)["code"] == "not_found"
    # guest → 404, no plan (invisible, not forbidden — no existence oracle)
    guest_resp = await api_client.post(
        f"/api/v1/issues/{ctx['secret']['id']}/move", json=body, headers=_auth(ctx["guest"])
    )
    assert guest_resp.status_code == 404
    assert _assert_envelope_no_plan(guest_resp)["code"] == "not_found"
    # …and sweeping target_project_id leaks nothing: the source gate fires
    # before target resolution, so a NONEXISTENT target yields the exact
    # same 404 message as the existing one (no project-existence oracle)
    guest_missing_target = await api_client.post(
        f"/api/v1/issues/{ctx['secret']['id']}/move",
        json={"target_project_id": str(uuid.uuid4()), "confirm": False},
        headers=_auth(ctx["guest"]),
    )
    assert guest_missing_target.status_code == 404
    assert (
        guest_missing_target.json()["error"]["message"]
        == guest_resp.json()["error"]["message"]
    )


@pytest.mark.e2e
async def test_move_preview_private_source_matrix_regression(api_client):
    ctx = await _security_setup(api_client, "sec-preview-src")
    member_resp = await api_client.post(
        f"/api/v1/issues/{ctx['secret']['id']}/move-preview",
        json={"target_project_id": ctx["dst"]},
        headers=_auth(ctx["member"]),
    )
    assert member_resp.status_code == 404
    guest_resp = await api_client.post(
        f"/api/v1/issues/{ctx['secret']['id']}/move-preview",
        json={"target_project_id": ctx["dst"]},
        headers=_auth(ctx["guest"]),
    )
    assert guest_resp.status_code == 404


@pytest.mark.e2e
async def test_move_unconfirmed_invisible_target_403_404(api_client):
    ctx = await _security_setup(api_client, "sec-move-tgt")
    issue_id = ctx["open"]["id"]
    # destination is the INVISIBLE private project
    body = {"target_project_id": ctx["prv"], "confirm": False}
    member_resp = await api_client.post(
        f"/api/v1/issues/{issue_id}/move", json=body, headers=_auth(ctx["member"])
    )
    assert member_resp.status_code == 403
    assert _assert_envelope_no_plan(member_resp)["code"] == "forbidden"
    guest_resp = await api_client.post(
        f"/api/v1/issues/{issue_id}/move", json=body, headers=_auth(ctx["guest"])
    )
    assert guest_resp.status_code == 404
    assert _assert_envelope_no_plan(guest_resp)["code"] == "not_found"
    # the preview endpoint rejects the same invisible target identically
    preview_member = await api_client.post(
        f"/api/v1/issues/{issue_id}/move-preview",
        json={"target_project_id": ctx["prv"]},
        headers=_auth(ctx["member"]),
    )
    assert preview_member.status_code == 403
    preview_guest = await api_client.post(
        f"/api/v1/issues/{issue_id}/move-preview",
        json={"target_project_id": ctx["prv"]},
        headers=_auth(ctx["guest"]),
    )
    assert preview_guest.status_code == 404


@pytest.mark.e2e
async def test_move_target_other_workspace_404(api_client):
    ctx = await _security_setup(api_client, "sec-move-xws")
    # a completely separate workspace + project owned by someone else
    outsider = await _register_and_login(api_client, f"sec-outsider-{uuid.uuid4().hex[:8]}@corp.com")
    other_ws = (
        await api_client.post(
            "/api/v1/workspaces",
            json={"name": "Other", "slug": f"sec-other-{uuid.uuid4().hex[:8]}"},
            headers=_auth(outsider),
        )
    ).json()["data"]
    foreign = await _create_project(api_client, outsider, other_ws["id"], "FOR")
    for confirm in (False, True):
        resp = await api_client.post(
            f"/api/v1/issues/{ctx['open']['id']}/move",
            json={
                "target_project_id": foreign["id"],
                "confirm": confirm,
                "version": ctx["open"]["version"],
            },
            headers=_auth(ctx["owner"]),
        )
        assert resp.status_code == 404, resp.text
    preview = await api_client.post(
        f"/api/v1/issues/{ctx['open']['id']}/move-preview",
        json={"target_project_id": foreign["id"]},
        headers=_auth(ctx["owner"]),
    )
    assert preview.status_code == 404


# ---------------------------------------------------------------------------
# H2: bulk unconfirmed preview — per-item markers, never a plan
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_bulk_unconfirmed_mixed_ids_error_markers_only(api_client):
    ctx = await _security_setup(api_client, "sec-bulk-mix")
    body = {
        "issue_ids": [ctx["open"]["id"], ctx["secret"]["id"]],
        "changes": {"project_id": ctx["dst"]},
    }
    # member: own (public) issue gets a plan; the private one → not_found
    # (LOW-S2 anti-oracle: a member outside the private project sees 404)
    member_resp = await api_client.post(
        "/api/v1/issues/bulk", json=body, headers=_auth(ctx["member"])
    )
    assert member_resp.status_code == 422
    member_err = member_resp.json()["error"]
    assert member_err["code"] == "move_confirmation_required"
    member_by_id = {p["issue_id"]: p for p in member_err["details"]["previews"]}
    assert "mapped_fields" in member_by_id[ctx["open"]["id"]]
    not_found_marker = member_by_id[ctx["secret"]["id"]]
    assert not_found_marker["error"] == "not_found"
    assert "mapped_fields" not in not_found_marker and "identifier" not in not_found_marker

    # guest: the invisible issue is not_found (not forbidden — no oracle)
    guest_resp = await api_client.post(
        "/api/v1/issues/bulk", json=body, headers=_auth(ctx["guest"])
    )
    assert guest_resp.status_code == 422
    guest_by_id = {
        p["issue_id"]: p for p in guest_resp.json()["error"]["details"]["previews"]
    }
    assert "mapped_fields" in guest_by_id[ctx["open"]["id"]]
    assert guest_by_id[ctx["secret"]["id"]]["error"] == "not_found"


@pytest.mark.e2e
async def test_bulk_unconfirmed_invisible_target_403_404(api_client):
    ctx = await _security_setup(api_client, "sec-bulk-tgt")
    body = {
        "issue_ids": [ctx["open"]["id"]],
        "changes": {"project_id": ctx["prv"]},
    }
    member_resp = await api_client.post(
        "/api/v1/issues/bulk", json=body, headers=_auth(ctx["member"])
    )
    assert member_resp.status_code == 403
    assert _assert_envelope_no_plan(member_resp)["code"] == "forbidden"
    guest_resp = await api_client.post(
        "/api/v1/issues/bulk", json=body, headers=_auth(ctx["guest"])
    )
    assert guest_resp.status_code == 404
    assert _assert_envelope_no_plan(guest_resp)["code"] == "not_found"


# ---------------------------------------------------------------------------
# authorized flows keep working (+ §3.8 ⑥ audit trail via the real API)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_authorized_two_step_move_and_audit_trail(api_client):
    ctx = await _security_setup(api_client, "sec-move-ok")
    # attach a project-private milestone so the move has something to clear
    milestone = (
        await api_client.post(
            f"/api/v1/projects/{ctx['pub']}/milestones",
            json={"title": "v1"},
            headers=_auth(ctx["owner"]),
        )
    ).json()["data"]
    issue = await _create_issue(
        api_client,
        ctx["owner"],
        ctx["ws"],
        project_id=ctx["pub"],
        milestone_id=milestone["id"],
        title="movable",
    )
    # step 1 — preview carries the plan AND the version (§3.8 M1)
    preview = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move-preview",
        json={"target_project_id": ctx["dst"]},
        headers=_auth(ctx["member"]),
    )
    assert preview.status_code == 200
    plan = preview.json()["data"]
    assert plan["version"] == issue["version"]
    assert {c["field"] for c in plan["cleared_fields"]} >= {"milestone_id"}
    # unconfirmed → 422 with the same preview (authorized caller)
    unconfirmed = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": ctx["dst"], "confirm": False},
        headers=_auth(ctx["member"]),
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"]["details"]["preview"]["version"] == issue["version"]
    # confirmed without version → 422 move_version_required
    # (MES-54 M-1: §3.8 version mandatory, enforced at the request schema)
    no_version = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": ctx["dst"], "confirm": True},
        headers=_auth(ctx["member"]),
    )
    assert no_version.status_code == 422
    assert no_version.json()["error"]["code"] == "move_version_required"
    # step 2 — confirmed move applies
    moved = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={
            "target_project_id": ctx["dst"],
            "confirm": True,
            "version": issue["version"],
        },
        headers=_auth(ctx["member"]),
    )
    assert moved.status_code == 200, moved.text
    data = moved.json()["data"]
    assert data["project_id"] == ctx["dst"]
    assert data["milestone_id"] is None
    assert data["version"] == issue["version"] + 1
    # §3.8 ⑥ — the trail carries the project change AND the clearing manifest
    activity = await api_client.get(
        f"/api/v1/issues/{issue['id']}/activity", headers=_auth(ctx["member"])
    )
    assert activity.status_code == 200
    rows = {row["field"]: row for row in activity.json()["data"]}
    assert rows["project_id"]["old_value"] == ctx["pub"]
    assert rows["project_id"]["new_value"] == ctx["dst"]
    assert rows["milestone_id"]["old_value"] == milestone["id"]
    assert rows["milestone_id"]["new_value"] is None


@pytest.mark.e2e
async def test_bulk_confirmed_move_writes_audit_trail(api_client):
    """M3: a bulk cross-project move leaves the same trail as the explicit
    move — the earlier gap let bulk moves vanish from the audit history."""
    ctx = await _security_setup(api_client, "sec-bulk-ok")
    issue = await _create_issue(
        api_client, ctx["owner"], ctx["ws"], project_id=ctx["prv"], title="bulk-move"
    )
    confirmed = await api_client.post(
        "/api/v1/issues/bulk",
        json={
            "issue_ids": [issue["id"]],
            "changes": {"project_id": ctx["dst"]},
            "confirm": True,
        },
        headers=_auth(ctx["owner"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["succeeded"] == 1
    activity = await api_client.get(
        f"/api/v1/issues/{issue['id']}/activity", headers=_auth(ctx["owner"])
    )
    rows = {row["field"]: row for row in activity.json()["data"]}
    assert rows["project_id"]["old_value"] == ctx["prv"]
    assert rows["project_id"]["new_value"] == ctx["dst"]
