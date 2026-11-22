"""Agents: every source of data has a stable unique id (a UUID).

    telefon   one install of the phone app; it makes the id itself and registers it after logging in
    skaner    this server's own scanner (created on the first start)

Every finding and every scan carries the id of the agent that produced it, so each alert, also in
DefectDojo, can be traced back to the phone or scanner. A revoked agent can no longer send data.
"""
import re

from fastapi import HTTPException

from . import db as store

ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KINDS = ("telefon", "skaner")


def short(agent_id: str | None) -> str:
    return (agent_id or "")[:8]


def check_id(agent_id: str) -> str:
    value = str(agent_id).strip().lower()
    if not ID.match(value):
        raise ValueError("Identyfikator agenta musi być UUID, np. 3f2b8c1e-6d4a-4b7e-9c2f-1a2b3c4d5e6f.")
    return value


def register(db, agent_id: str, name: str, kind: str, user: str | None) -> dict:
    """First call creates the agent; later calls only say it is alive (a name set by the admin stays)."""
    agent_id = check_id(agent_id)
    if kind not in KINDS:
        raise ValueError(f"Rodzaj agenta: {', '.join(KINDS)}.")
    row = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is not None and row["revoked"]:
        raise HTTPException(403, "Ten agent został odwołany przez admina. Poproś o ponowne włączenie.")
    moment = store.now()
    if row is None:
        db.execute("INSERT INTO agents (id, kind, name, registered_by, created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                   (agent_id, kind, (name or kind).strip()[:80], user, moment, moment))
    else:
        db.execute("UPDATE agents SET last_seen=? WHERE id=?", (moment, agent_id))
    return dict(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())


def active(db, agent_id: str | None, user: str | None) -> str | None:
    """The agent behind a report: unknown ids are registered as phones, revoked ones are refused."""
    if not agent_id:
        return None
    return register(db, agent_id, "Telefon", "telefon", user)["id"]


def listing(db) -> list[dict]:
    counts = dict(db.execute("SELECT agent_id, COUNT(*) FROM findings GROUP BY agent_id").fetchall())
    scanner = store.settings(db).get("scanner_agent_id")
    out = []
    for r in db.execute("SELECT * FROM agents ORDER BY kind, created_at"):
        item = dict(r, revoked=bool(r["revoked"]), findings=counts.get(r["id"], 0), short=short(r["id"]))
        item["this_server"] = r["id"] == scanner
        out.append(item)
    return out
