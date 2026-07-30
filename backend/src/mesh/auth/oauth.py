"""Third-party OAuth login & binding (auth.md §1.2 A5/A6, §3.1, §4.5).

Vendor-neutral by design: Mesh integrates "a third-party OAuth provider" through
the :class:`OAuthProvider` interface — no specific vendor is hardcoded and no
reference source is implied. A :class:`MockOAuthProvider` implements the same
interface so the full authorization-code + PKCE round-trip is exercised in tests
without an external service.

Flow (authorization code + PKCE, §4.5 step 5):
  start  → generate ``state`` (CSRF) + ``code_verifier`` (PKCE), stash both in
           Redis, return the provider authorization URL (302 target).
  callback → validate ``state``, exchange ``code`` + ``code_verifier`` for the
           provider identity, then login-or-register-and-bind (A5) — or bind to
           the calling account (A6).

Tokens/identities: the provider subject is bound in ``oauth_identities``
(unique per provider+subject); auto-registered users have ``password_hash=NULL``
and a verified email (the provider vouched for it). Unbinding keeps at least one
login method (A6).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth import security
from mesh.auth.service import AuthService, TokenResult
from mesh.db.models.user import OAuthIdentity, User
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError

STATE_TTL_SECONDS = 600
STATE_KEY_PREFIX = "mesh:oauth:state:"
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


# --- PKCE (RFC 7636, S256) ---------------------------------------------------


def generate_code_verifier() -> str:
    """A high-entropy PKCE code verifier (base64url, ≥43 chars)."""
    return secrets.token_urlsafe(32)


def code_challenge_s256(verifier: str) -> str:
    """S256 code challenge = BASE64URL(SHA256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# --- provider abstraction ----------------------------------------------------


@dataclass(frozen=True)
class OAuthUserInfo:
    """The identity a provider vouches for after the code exchange.

    ``email_verified`` is load-bearing for security (H1): an identity may only be
    auto-linked to an existing account by email when the provider has actually
    verified that email — otherwise an attacker holding an unverified
    ``victim@corp.com`` on some provider could take over the victim's account.
    """

    provider_subject: str
    email: str | None = None
    name: str | None = None
    email_verified: bool = False


@runtime_checkable
class OAuthProvider(Protocol):
    """A third-party OAuth provider (vendor-neutral interface)."""

    name: str

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        """Build the provider's authorization endpoint URL (state + PKCE)."""
        ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthUserInfo:
        """Exchange the authorization code (+ PKCE verifier) for the identity."""
        ...

    def is_redirect_allowed(self, redirect_uri: str) -> bool:
        """Whether ``redirect_uri`` is on this provider's exact-match allowlist (M1)."""
        ...


class MockOAuthProvider:
    """In-process provider for tests/dev — same interface, no network.

    ``exchange_code`` interprets the code as base64url(JSON
    ``{sub,email,name,email_verified}``) so a single provider instance can
    simulate arbitrary identities (``email_verified`` defaults to False —
    callers opt in, exactly like a real provider that may or may not have
    verified the email). A malformed code — e.g. the placeholder ``code=mock``
    emitted by ``authorization_url`` in the dev browser round-trip — falls back
    to a fixed default identity whose email the mock vouches for
    (``email_verified=True``): H1 guards against *real* providers forwarding
    unverified emails, while the dev/test mock is itself the authority for its
    own default identity — otherwise dev third-party login could never succeed.
    ``allowed_redirect_uris`` is an exact-match allowlist (M1) — empty denies
    all redirect URIs.
    """

    def __init__(
        self, name: str = "mock", *, allowed_redirect_uris: frozenset[str] = frozenset()
    ) -> None:
        self.name = name
        self._allowed_redirect_uris = allowed_redirect_uris

    def is_redirect_allowed(self, redirect_uri: str) -> bool:
        return redirect_uri in self._allowed_redirect_uris

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        # The mock "authorizes" instantly, redirecting straight back to the
        # callback with a placeholder code (the test supplies the real code).
        sep = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{sep}code=mock&state={state}&challenge={code_challenge}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str  # noqa: ARG002
    ) -> OAuthUserInfo:
        try:
            padded = code + "=" * (-len(code) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return OAuthUserInfo(
                provider_subject=str(payload["sub"]),
                email=payload.get("email"),
                name=payload.get("name"),
                email_verified=bool(payload.get("email_verified", False)),
            )
        except (ValueError, KeyError, json.JSONDecodeError):
            return OAuthUserInfo(
                provider_subject="mock-subject-1",
                email="mock-user@example.com",
                name="Mock User",
                email_verified=True,  # mock vouches for its own default identity
            )


def encode_mock_code(
    *, sub: str, email: str, name: str | None = None, email_verified: bool = False
) -> str:
    """Helper for tests: build a mock-provider code carrying an identity."""
    payload = json.dumps(
        {"sub": sub, "email": email, "name": name, "email_verified": email_verified},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


# --- service -----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class OAuthService:
    """OAuth start/callback/bind/unbind over a provider registry."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        auth_service: AuthService,
        redis,
        *,
        providers: dict[str, OAuthProvider] | None = None,
    ) -> None:
        self._sf = session_factory
        self._auth = auth_service
        self._redis = redis
        self._providers = providers or {}

    def register_provider(self, provider: OAuthProvider) -> None:
        self._providers[provider.name] = provider

    def _provider(self, name: str) -> OAuthProvider:
        if not _PROVIDER_NAME_RE.match(name or ""):
            raise NotFoundError("unknown OAuth provider")
        provider = self._providers.get(name)
        if provider is None:
            raise NotFoundError("unknown OAuth provider")
        return provider

    # -- start -----------------------------------------------------------------

    async def start(
        self,
        *,
        provider_name: str,
        redirect_uri: str,
        mode: str = "login",
        user_id: uuid.UUID | None = None,
    ) -> dict:
        """Begin an OAuth flow; returns ``{state, authorization_url}``."""
        provider = self._provider(provider_name)
        if mode not in ("login", "bind"):
            raise ValidationError("invalid mode", details={"mode": mode})
        if mode == "bind" and user_id is None:
            raise ValidationError("bind mode requires an authenticated user")
        # M1: exact-match redirect_uri allowlist (OAuth 2.0 security BCP) — the
        # app never 302s to an arbitrary URI with code+state (open redirect).
        if not provider.is_redirect_allowed(redirect_uri):
            raise BusinessRuleError(
                "redirect_uri is not allowed for this provider",
                code="redirect_uri_not_allowed",
            )
        state = security.generate_token()
        verifier = generate_code_verifier()
        challenge = code_challenge_s256(verifier)
        await self._redis.set(
            f"{STATE_KEY_PREFIX}{state}",
            json.dumps(
                {
                    "provider": provider_name,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "mode": mode,
                    "user_id": str(user_id) if user_id else None,
                }
            ),
            ex=STATE_TTL_SECONDS,
        )
        url = provider.authorization_url(
            state=state, code_challenge=challenge, redirect_uri=redirect_uri
        )
        return {"state": state, "authorization_url": url}

    # -- callback --------------------------------------------------------------

    async def _consume_state(self, *, provider_name: str, state: str) -> dict:
        key = f"{STATE_KEY_PREFIX}{state}"
        raw = await self._redis.get(key)
        if raw is None:
            raise ValidationError(
                "invalid or expired OAuth state", code="invalid_oauth_state"
            )
        await self._redis.delete(key)  # one-time
        data = json.loads(raw)
        if data.get("provider") != provider_name:
            raise ValidationError(
                "invalid or expired OAuth state", code="invalid_oauth_state"
            )
        return data

    async def callback(
        self,
        *,
        provider_name: str,
        code: str,
        state: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Complete the flow: login-or-register-and-bind, or bind to caller."""
        provider = self._provider(provider_name)
        data = await self._consume_state(provider_name=provider_name, state=state)
        userinfo = await provider.exchange_code(
            code=code,
            code_verifier=data["code_verifier"],
            redirect_uri=data["redirect_uri"],
        )
        if data["mode"] == "bind":
            await self.bind_identity(
                user_id=uuid.UUID(data["user_id"]),
                provider_name=provider_name,
                userinfo=userinfo,
            )
            return {"status": "bound", "provider": provider_name}
        user_id = await self._login_or_register(provider_name=provider_name, userinfo=userinfo)
        # A provider round trip just completed an interactive login — this is
        # a fresh primary authentication (R6-H3); the mock/dev provider is
        # interactive by definition (real providers' auth_time verification is
        # the §2.4.3 increment). The refresh token rides the mesh_session
        # cookie set by the route (R4-H1) — the body keeps only the access.
        tokens: TokenResult = await self._auth.issue_session(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            authenticated_at=datetime.now(UTC),
        )
        return {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,  # route moves this to the cookie
        }

    async def _login_or_register(
        self, *, provider_name: str, userinfo: OAuthUserInfo
    ) -> uuid.UUID:
        """A5: existing binding logs in; verified email links/registers; else reject.

        H1: an identity is only linked to an existing account by email when the
        provider has *verified* that email (``email_verified=True``). An
        unverified email never auto-links (account-takeover prevention) — the
        user must instead sign in with their password and bind explicitly.
        """
        now = _now()
        async with self._sf() as session, session.begin():
            identity = await session.scalar(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider == provider_name,
                    OAuthIdentity.provider_subject == userinfo.provider_subject,
                )
            )
            if identity is not None:
                return identity.user_id

            if not userinfo.email:
                raise BusinessRuleError(
                    "provider did not supply an email; cannot auto-register",
                    code="oauth_email_required",
                )
            if not userinfo.email_verified:
                raise BusinessRuleError(
                    "provider has not verified this email; sign in and link manually",
                    code="oauth_email_not_verified",
                )

            user = await session.scalar(
                select(User).where(User.email == userinfo.email.lower())
            )
            if user is None:
                user = User(
                    email=userinfo.email.lower(),
                    display_name=userinfo.name or userinfo.email,
                    password_hash=None,  # OAuth-only account
                    status="active",
                    email_verified_at=now,  # provider vouched for the email
                )
                session.add(user)
                await session.flush()
            session.add(
                OAuthIdentity(
                    user_id=user.id,
                    provider=provider_name,
                    provider_subject=userinfo.provider_subject,
                    provider_email=userinfo.email,
                )
            )
            return user.id

    # -- bind / unbind (A6) ----------------------------------------------------

    async def bind_identity(
        self, *, user_id: uuid.UUID, provider_name: str, userinfo: OAuthUserInfo
    ) -> None:
        """Bind a provider identity to an existing account (A6)."""
        async with self._sf() as session, session.begin():
            existing = await session.scalar(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider == provider_name,
                    OAuthIdentity.provider_subject == userinfo.provider_subject,
                )
            )
            if existing is not None:
                if existing.user_id == user_id:
                    return  # already bound to this user — idempotent
                raise ConflictError("identity already bound to another account")
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user not found")
            session.add(
                OAuthIdentity(
                    user_id=user_id,
                    provider=provider_name,
                    provider_subject=userinfo.provider_subject,
                    provider_email=userinfo.email,
                )
            )

    async def unbind_identity(self, *, user_id: uuid.UUID, provider_name: str) -> None:
        """Unbind a provider, refusing to remove the last login method (A6)."""
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user not found")
            identity = await session.scalar(
                select(OAuthIdentity).where(
                    OAuthIdentity.user_id == user_id,
                    OAuthIdentity.provider == provider_name,
                )
            )
            if identity is None:
                raise NotFoundError("identity not found")
            other_identities = (
                await session.scalar(
                    select(func.count(OAuthIdentity.id)).where(
                        OAuthIdentity.user_id == user_id,
                        OAuthIdentity.provider != provider_name,
                    )
                )
            ) or 0
            has_password = bool(user.password_hash)
            if not has_password and other_identities == 0:
                raise BusinessRuleError(
                    "cannot remove the last login method",
                    code="last_login_method",
                )
            await session.execute(
                delete(OAuthIdentity).where(OAuthIdentity.id == identity.id)
            )

    async def list_identities(self, *, user_id: uuid.UUID) -> list[dict]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(OAuthIdentity)
                        .where(OAuthIdentity.user_id == user_id)
                        .order_by(OAuthIdentity.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "provider": row.provider,
                    "provider_email": row.provider_email,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
