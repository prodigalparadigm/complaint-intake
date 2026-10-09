"""SLA clocks.

The 48-hour compliance clock is the one number in this system with a regulator
attached to it. It is computed from `received_at` — the moment the customer
hit send — not from the moment we got around to processing, because that is
how an examiner would compute it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

FMT = "%Y-%m-%dT%H:%M:%S"


def parse(ts: str) -> datetime:
    return datetime.strptime(ts[:19], FMT)


def fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def due(received_at: str, hours: float) -> str:
    return fmt(parse(received_at) + timedelta(hours=hours))


def hours_between(a: str, b: str) -> float:
    return (parse(b) - parse(a)).total_seconds() / 3600.0


def build_clocks(received_at: str, band: str, regulatory: bool, policy: dict) -> dict:
    sla = policy["sla_hours"]
    clocks = {
        "received_at": received_at,
        "ack_customer_due": due(received_at, sla["ack_customer"]),
        "resolve_due": due(received_at, sla.get(f"resolve_{band.lower()}", sla["resolve_s4"])),
    }
    if regulatory:
        clocks["compliance_flag_due"] = due(received_at, sla["compliance_flag"])
    return clocks
