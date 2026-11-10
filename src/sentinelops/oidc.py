"""OIDC bearer-token verification for non-local deployments (lane B item B-e).

Verifies signed JWT access tokens from any standards-based identity provider
(Amazon Cognito, Okta, Entra ID, Keycloak) against its published JWKS. Only
asymmetric algorithms are accepted, so the API never holds a shared signing
secret. A token maps to one of the two existing API roles through an explicit
scope; a valid token without either scope is authenticated but not authorized.
Static bearer tokens stay restricted to local/test environments.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import re
import time
from urllib.parse import urlsplit

import httpx
import jwt

ALGORITHMS = ("RS256", "ES256")
JWKS_MAX_BYTES = 64 * 1024
REFRESH_INTERVAL_SECONDS = 30
SUBJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|+-]{0,199}")
PRODUCER, OPERATOR = "producer", "operator"


class TokenRejected(Exception):
    """The bearer token is missing, malformed, unsigned, expired or for another audience."""


class ScopeMissing(Exception):
    """A valid token that carries neither SentinelOps scope."""


class VerifierUnavailable(Exception):
    """The identity provider's JWKS could not be fetched or parsed."""


def _https(url, name):
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError(f"{name} must be a credential-free https URL")


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    producer_scope: str = "sentinelops/incidents.write"
    operator_scope: str = "sentinelops/incidents.read"
    audience_claim: str = "aud"
    leeway_seconds: int = 60
    jwks_ttl_seconds: int = 300

    def __post_init__(self):
        _https(self.issuer, "OIDC issuer")
        _https(self.jwks_url, "OIDC JWKS URL")
        if not isinstance(self.audience, str) or not self.audience.strip():
            raise ValueError("OIDC audience is required")
        # Cognito access tokens carry the app client in client_id instead of aud.
        if self.audience_claim not in ("aud", "client_id"):
            raise ValueError("OIDC audience claim must be aud or client_id")
        scopes = (self.producer_scope, self.operator_scope)
        if any(not isinstance(s, str) or not s or any(c.isspace() for c in s) for s in scopes) or len(set(scopes)) != 2:
            raise ValueError("OIDC producer and operator scopes must be distinct single tokens")
        if not 0 <= self.leeway_seconds <= 300 or not 30 <= self.jwks_ttl_seconds <= 86400:
            raise ValueError("OIDC leeway or JWKS cache lifetime is out of bounds")


async def fetch_jwks(url):
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > JWKS_MAX_BYTES:
            raise ValueError("JWKS document is too large")
        return response.json()


def _numeric_date(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class OidcVerifier:
    def __init__(self, config: OidcConfig, *, fetch=fetch_jwks, clock=time.monotonic):
        self.config, self._fetch, self._clock = config, fetch, clock
        self._keys, self._loaded_at, self._forced_at, self._retry_after = {}, None, None, None
        self._lock = asyncio.Lock()

    def _usable(self, now):
        # Keys outlive one failed refresh, but never 4 cache lifetimes: a revoked key must stop working.
        return self._loaded_at is not None and now - self._loaded_at < 4 * self.config.jwks_ttl_seconds

    async def _refresh(self, *, forced):
        async with self._lock:
            now = self._clock()
            if not forced and self._loaded_at is not None and now - self._loaded_at < self.config.jwks_ttl_seconds:
                return
            # Failed fetches back off, and unknown key IDs (rotation) refresh at most once per
            # interval, so neither an outage nor forged kids turn the API into a JWKS amplifier.
            backing_off = self._retry_after is not None and now < self._retry_after
            if backing_off or (forced and self._forced_at is not None
                               and now - self._forced_at < REFRESH_INTERVAL_SECONDS):
                if not self._usable(now):
                    raise VerifierUnavailable("identity provider keys are unavailable")
                return
            if forced:
                self._forced_at = now
            try:
                keys = {item.key_id: item for item in jwt.PyJWKSet.from_dict(await self._fetch(self.config.jwks_url)).keys
                        if item.key_id and item.algorithm_name in ALGORITHMS and item.public_key_use in (None, "sig")}
            except Exception:
                keys = {}
            if not keys:
                self._retry_after = now + REFRESH_INTERVAL_SECONDS
                if not self._usable(now):
                    raise VerifierUnavailable("identity provider keys are unavailable")
                return  # keep the last good key set; an unknown kid still fails closed
            self._keys, self._loaded_at, self._retry_after = keys, now, None

    async def identify(self, token: str) -> tuple[str, str]:
        """Return (role, subject) for a verified token; role is producer or operator."""
        if not token or len(token) > 8192:
            raise TokenRejected("bearer token is missing or too long")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenRejected("bearer token is not a JWT") from None
        kid, algorithm = header.get("kid"), header.get("alg")
        if algorithm not in ALGORITHMS or not isinstance(kid, str) or not kid:
            raise TokenRejected("token algorithm or key ID is not accepted")
        await self._refresh(forced=False)
        if kid not in self._keys:
            await self._refresh(forced=True)  # key rotation
        key = self._keys.get(kid)
        if key is None or key.algorithm_name != algorithm:
            raise TokenRejected("token was not signed by a published key")
        config = self.config
        by_aud = config.audience_claim == "aud"
        try:
            claims = jwt.decode(token, key.key, algorithms=[algorithm], issuer=config.issuer,
                                audience=config.audience if by_aud else None, leeway=config.leeway_seconds,
                                options={"require": ["exp", "iat", "iss", "sub"] + (["aud"] if by_aud else []),
                                         "verify_aud": by_aud})
        except (jwt.PyJWTError, TypeError, ValueError, OverflowError):
            # TypeError/ValueError: claims of the wrong JSON shape (e.g. an object as iat).
            # OverflowError: Infinity as a NumericDate.
            raise TokenRejected("token signature or claims are invalid") from None
        # RFC 7519 NumericDate is a JSON number. PyJWT coerces numeric strings with int(), so a
        # string exp/iat/nbf would otherwise pass; reject any non-number or non-finite value.
        for name in ("exp", "iat", "nbf"):
            if name in claims and not _numeric_date(claims[name]):
                raise TokenRejected("token time claims are not NumericDate values")
        if not by_aud:
            # Compatibility only for unbound Cognito access tokens: explicit token_use=access and no
            # aud at all. A token bound to another resource (any aud) is never accepted by client_id.
            if "aud" in claims or claims.get("token_use") != "access" or claims.get("client_id") != config.audience:
                raise TokenRejected("token is not an unbound access token of this client")
        elif claims.get("token_use", "access") != "access":
            raise TokenRejected("only access tokens are accepted")
        subject = claims["sub"]
        if not isinstance(subject, str) or not SUBJECT.fullmatch(subject):
            raise TokenRejected("token subject is not a bounded identifier")
        raw = claims.get("scope", claims.get("scp", ""))
        if isinstance(raw, str):
            scopes = set(raw.split())
        elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            scopes = set(raw)
        else:
            raise TokenRejected("token scope claim has an invalid shape")
        if config.producer_scope in scopes:
            return PRODUCER, subject
        if config.operator_scope in scopes:
            return OPERATOR, subject
        raise ScopeMissing("token carries no SentinelOps scope")
