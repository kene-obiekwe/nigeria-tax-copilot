"""The rate-limit and cost-cap hook (interface design, Section 8; D-162).

The Python gate only chooses the `Limits` for a request; the check itself runs in the database, in
`open_query()`, under an advisory lock and in the same transaction as the receipt insert. Phase 9
ships `LocalGate` (no limits; the sidebar still shows today's spend) and `TierGate` (reads
`app/limits.yml`; unit tested, not enabled). Phase 11 sets the values and enables `TierGate` for
`TAXY_DEPLOYMENT=hosted`; until then `gate_for("hosted")` refuses, so a hosted app cannot start
without a cap.

The limit day runs from midnight West Africa Time. Africa/Lagos is UTC+1 all year (no daylight
saving), so a fixed offset is used here; the database uses the 'Africa/Lagos' zone itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIMITS_FILE = ROOT / "app" / "limits.yml"
WAT = timezone(timedelta(hours=1), "WAT")
DEFAULT_RESERVE_USD = Decimal("0.05")  # above the dearest Phase 7 question ($0.036)


@dataclass(frozen=True)
class RequestContext:
    tier: str               # local | anonymous | signed_in | reviewer
    user_key: str | None
    session_key: str
    now: datetime           # UTC; the limit day is computed in West Africa Time


@dataclass(frozen=True)
class Limits:               # None means no limit
    per_session: int | None
    per_user_day: int | None
    anonymous_pool_day: int | None
    cost_cap_day_usd: Decimal | None
    reserve_usd: Decimal    # counted for a question until its real cost is recorded

    def as_json(self) -> dict:
        """The `limits` argument of open_query (the reserve is passed separately)."""
        return {"per_session": self.per_session, "per_user_day": self.per_user_day,
                "anonymous_pool_day": self.anonymous_pool_day,
                "cost_cap_day_usd": None if self.cost_cap_day_usd is None else str(self.cost_cap_day_usd)}


class Gate(Protocol):
    def limits_for(self, ctx: RequestContext) -> Limits: ...


def day_start(now: datetime) -> datetime:
    """Midnight West Africa Time at the start of `now`'s limit day, in UTC."""
    local = now.astimezone(WAT)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def next_reset(now: datetime) -> datetime:
    return day_start(now) + timedelta(days=1)


@dataclass(frozen=True)
class LocalGate:
    """`TAXY_DEPLOYMENT=local`: no limits."""
    reserve_usd: Decimal = DEFAULT_RESERVE_USD

    def limits_for(self, ctx: RequestContext) -> Limits:
        return Limits(None, None, None, None, self.reserve_usd)


def _int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("a limit is a whole number of 0 or more, or null")
    return value


def _usd(value) -> Decimal | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if amount < 0:
        raise ValueError("an amount is 0 or more")
    return amount


@dataclass(frozen=True)
class TierGate:
    """Limits by tier, from `app/limits.yml`. The global daily cost cap applies to every hosted tier;
    the order of the checks (cap, anonymous pool, per user or per session) is the database's."""
    cost_cap_day_usd: Decimal | None
    reserve_usd: Decimal
    anonymous_per_session: int | None
    anonymous_pool_day: int | None
    signed_in_per_day: int | None
    reviewer_per_day: int | None

    @classmethod
    def from_file(cls, path: Path = LIMITS_FILE) -> TierGate:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tiers = data.get("tiers") or {}
        return cls(cost_cap_day_usd=_usd(data.get("cost_cap_day_usd")),
                   reserve_usd=_usd(data.get("reserve_usd", str(DEFAULT_RESERVE_USD))),
                   anonymous_per_session=_int((tiers.get("anonymous") or {}).get("per_session")),
                   anonymous_pool_day=_int((tiers.get("anonymous") or {}).get("pool_per_day")),
                   signed_in_per_day=_int((tiers.get("signed_in") or {}).get("per_day")),
                   reviewer_per_day=_int((tiers.get("reviewer") or {}).get("per_day")))

    def limits_for(self, ctx: RequestContext) -> Limits:
        cap, reserve = self.cost_cap_day_usd, self.reserve_usd
        if ctx.tier == "anonymous":
            return Limits(self.anonymous_per_session, None, self.anonymous_pool_day, cap, reserve)
        if ctx.tier == "signed_in":
            return Limits(None, self.signed_in_per_day, None, cap, reserve)
        if ctx.tier == "reviewer":
            return Limits(None, self.reviewer_per_day, None, cap, reserve)
        if ctx.tier == "local":
            return Limits(None, None, None, cap, reserve)
        raise ValueError(f"unknown tier {ctx.tier!r}")


def gate_for(deployment: str) -> Gate:
    if deployment in ("local", "test"):
        return LocalGate()
    raise RuntimeError("the hosted gate is switched on in Phase 11 (TierGate with the Phase 11 values)")
