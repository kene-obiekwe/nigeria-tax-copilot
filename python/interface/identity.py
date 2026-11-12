"""The identity abstraction (interface design, Sections 7.2 and 7.3; D-163, D-164).

    ident = resolve_identity(deployment="hosted", claims=None, secret=None, touch=store.touch_user)
    # -> Identity(tier="anonymous", user_key=None, display=None)

Phase 9 builds and tests this with fake sign-in claims; Phase 11 passes Streamlit's `st.user` as the
claims. The user key is `HMAC-SHA256(secret, issuer + "|" + subject)` in hex: the database never sees
the email, the name or the provider's subject in clear. The key is derived here, server-side, from the
claims of the running session only, never from a URL, query parameter or form field. The secret
(`TAXY_USER_KEY_SECRET`) is read from the environment at run time and never printed or logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Callable, Mapping

SECRET_ENV = "TAXY_USER_KEY_SECRET"
PROVIDER = "google"
TIERS = ("local", "anonymous", "signed_in", "reviewer")


class IdentityError(RuntimeError):
    """Sign-in claims arrived but no user key can be derived (no secret, or no issuer or subject)."""


@dataclass(frozen=True)
class Identity:
    tier: str                 # local | anonymous | signed_in | reviewer
    user_key: str | None      # the HMAC key; None unless signed in
    display: str | None = None  # shown in the sidebar for this session only; never stored

    def __post_init__(self):
        if self.tier not in TIERS:
            raise ValueError(f"unknown tier {self.tier!r}")
        if (self.tier in ("signed_in", "reviewer")) != (self.user_key is not None):
            raise ValueError("a signed-in tier needs a user key, and only a signed-in tier has one")

    @property
    def signed_in(self) -> bool:
        return self.user_key is not None

    def __repr__(self) -> str:  # never shows the key or the display name
        return f"Identity(tier={self.tier!r}, signed_in={self.signed_in})"


LOCAL = Identity("local", None)
ANONYMOUS = Identity("anonymous", None)


def user_key(secret: bytes, issuer: str, subject: str) -> str:
    if not secret:
        raise IdentityError("no user-key secret")
    return hmac.new(secret, f"{issuer}|{subject}".encode("utf-8"), hashlib.sha256).hexdigest()


def secret_from_env(environ: Mapping[str, str] = os.environ) -> bytes | None:
    value = environ.get(SECRET_ENV)
    return value.encode("utf-8") if value else None


def resolve_identity(*, deployment: str, claims: Mapping | None, secret: bytes | None,
                     touch: Callable[[str, str], str] | None, provider: str = PROVIDER) -> Identity:
    """The session's identity. No claims (or not logged in): `local` locally, `anonymous` hosted.
    Claims with an issuer and a subject: the user key, recorded through `touch` (touch_user), whose
    returned tier (signed_in, or reviewer as set by Kene in the database) is the session's tier."""
    if not claims or not claims.get("is_logged_in", True):
        return LOCAL if deployment == "local" else ANONYMOUS
    issuer, subject = claims.get("iss"), claims.get("sub")
    if not issuer or not subject:
        raise IdentityError("sign-in claims without an issuer and a subject")
    key = user_key(secret, str(issuer), str(subject))
    if touch is None:
        raise IdentityError("no user store")
    tier = touch(key, provider)
    return Identity(tier, key, claims.get("email"))


@dataclass
class FakeIdentity:
    """Sign-in claims for tests (Phase 9); Phase 11 uses `st.user`."""
    subject: str
    email: str = "user@example.test"
    issuer: str = "https://accounts.example.test"

    def claims(self) -> dict:
        return {"is_logged_in": True, "iss": self.issuer, "sub": self.subject, "email": self.email}
