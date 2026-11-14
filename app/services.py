"""The app's services in one injectable object (interface design, Sections 3.8 and 10).

    services = get_services()        # st.session_state["services"] in tests, else built once per process

`build_services()` is the real wiring: the audit store on the project database, the start-up check (Section
3.8), the gate for the deployment, and the `AppService` with the shipped configuration, the parity hook of
the card (D-155) and, locally, the JSONL append. Tests inject a `Services` with fakes through
`st.session_state["services"]`, so no test builds a real model or embedding client.

Nothing here prints or logs a connection string, a key, a question or a message text.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "python"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from audit.store import AuditStore  # noqa: E402
from check_pgvector import connect  # noqa: E402
from generation.audit_log import LOG_DIR  # noqa: E402
from generation.generate import DbCorpusStore  # noqa: E402
from interface.identity import ANONYMOUS, LOCAL, Identity, resolve_identity, secret_from_env  # noqa: E402
from interface.limits import Limits, RequestContext, gate_for  # noqa: E402
from interface.service import UI_WAIT_S, AppService, db_chunk_hashes  # noqa: E402
from interface.startup import P7_CONFIG_SHA256, StartupResult, shipped_config, startup_check  # noqa: E402

LOG = logging.getLogger("taxy.app")
DEPLOYMENT_ENV = "TAXY_DEPLOYMENT"
STAGE_SECONDS = (20.0, 60.0)  # the waiting text changes after 20 s and after 60 s (Section 9)


@dataclass
class Services:
    deployment: str
    startup: StartupResult
    service: AppService | None
    chunks: Callable[[list[str]], dict[str, dict]]       # corpus_chunks rows by chunk id
    identity: Callable[[], Identity]                      # the running session's identity
    config_sha256: str = P7_CONFIG_SHA256
    ui_wait_s: float = UI_WAIT_S
    stage_seconds: tuple[float, float] = STAGE_SECONDS
    poll_s: float = 0.25
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The start-up check passed and the service exists: only then is the question box enabled."""
        return bool(self.startup.ok and self.service is not None)

    @property
    def snapshot(self) -> str | None:
        return self.startup.corpus_snapshot

    def limits_for(self, identity: Identity, session_key: str) -> Limits | None:
        if self.service is None:
            return None
        from datetime import datetime, timezone
        return self.service.gate.limits_for(RequestContext(identity.tier, identity.user_key, session_key,
                                                           datetime.now(timezone.utc)))


def db_chunks(conn_factory: Callable = connect) -> Callable[[list[str]], dict[str, dict]]:
    """`corpus_chunks` rows (citation, text, page_labels, context_header, register_ids, ...) by chunk id,
    read from the same database load the pipeline reads."""
    def read(chunk_ids: list[str]) -> dict[str, dict]:
        with conn_factory() as conn:
            return DbCorpusStore(conn).chunks(list(chunk_ids))
    return read


def default_identity(deployment: str) -> Callable[[], Identity]:
    """No sign-in: `local` locally, `anonymous` on a deployment without sign-in (test)."""
    ident = LOCAL if deployment == "local" else ANONYMOUS
    return lambda: ident


CLAIM_KEYS = ("is_logged_in", "iss", "sub", "email")  # the only claims the app reads from `st.user`
GOOGLE = "google"  # the provider name in the Streamlit secrets file ([auth.google]), design Section 5.1


def streamlit_claims() -> dict | None:
    """The running session's sign-in claims from `st.user`: the four the app uses, or None when nobody is
    logged in (or sign-in is not configured). The tokens are never read."""
    user = st.user
    if not user.get("is_logged_in", False):
        return None
    return {key: user.get(key) for key in CLAIM_KEYS}


def session_identity(deployment: str, *, claims: Callable[[], dict | None], touch: Callable[[str, str], str],
                     secret: Callable[[], bytes | None] = secret_from_env) -> Callable[[], Identity]:
    """The identity of the running session: `resolve_identity()` on its sign-in claims (design Section 5.4).
    If no user key can be derived (no secret, or claims without `iss` or `sub`) or the user store fails, the
    session stays anonymous; the error type is logged, never a claim, a key or the secret."""
    fallback = default_identity(deployment)

    def identity() -> Identity:
        try:
            return resolve_identity(deployment=deployment, claims=claims(), secret=secret(), touch=touch,
                                    provider=GOOGLE)
        except Exception as exc:  # noqa: BLE001 - lower privilege, never a wrong user
            LOG.error("sign-in identity failed (%s); the session stays anonymous", type(exc).__name__)
            return fallback()

    return identity


def sign_in() -> None:
    """The "Sign in with Google" button (`services.extra["sign_in"]`, hosted only; design Section 5.5)."""
    st.login(GOOGLE)


def sign_out() -> None:
    """The "Log out" button (`services.extra["sign_out"]`, hosted only)."""
    st.logout()


def deployment_wiring(deployment: str, store: AuditStore) -> tuple[Callable[[], Identity], dict]:
    """(identity, extra) for a deployment. Only `hosted` has sign-in: identity from `st.user` and the two
    hooks; local and test keep the default identity and no hooks, so the buttons stay disabled there."""
    if deployment == "hosted":
        return (session_identity(deployment, claims=streamlit_claims, touch=store.touch_user),
                {"sign_in": sign_in, "sign_out": sign_out})
    return default_identity(deployment), {}


def build_services() -> Services:
    """The real wiring. A failed start-up check gives a Services with `ok` False: the app fails closed."""
    from app.cards import make_parity  # noqa: PLC0415 - cards imports the pipeline; keep this module light

    deployment = os.environ.get(DEPLOYMENT_ENV, "local")
    store = AuditStore(connect)
    identity, extra = deployment_wiring(deployment, store)
    chunks = db_chunks()
    result = startup_check(deployment=deployment, store=store, connect=connect)
    try:
        gate = gate_for(deployment)
    except RuntimeError:  # hosted without a usable daily cap (or an unknown deployment): no start
        result = StartupResult(False, [*result.problems, "gate"], result.start_id, result.corpus_snapshot, result.info)
        LOG.error("start-up check failed: gate")
        gate = None
    if not result.ok or gate is None or result.start_id is None:
        return Services(deployment, result, None, chunks, identity, extra=extra)
    service = AppService(store=store, gate=gate, start_id=result.start_id, config=shipped_config(),
                         chunk_hashes=db_chunk_hashes(connect), parity=make_parity(chunks),
                         jsonl_dir=LOG_DIR if deployment == "local" else None)
    return Services(deployment, result, service, chunks, identity, extra=extra)


@st.cache_resource(show_spinner=False)
def real_services() -> Services:
    """Built once per process and shared by every session and page (Section 3.8)."""
    return build_services()


def get_services() -> Services:
    """`st.session_state["services"]` when a test injected one, else the process-wide real services."""
    injected = st.session_state.get("services")
    return injected if injected is not None else real_services()
