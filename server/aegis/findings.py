"""Findings: one per service (ip, port, proto), kept across scans.

Every scan recomputes the automatic part (technologies, tags, priority, title, reasons).
A field the user edited by hand is listed in `edited` and is never overwritten by a scan
(task point 14). Resetting a field gives it back to the automatic value.
"""
import json

from . import db as store
from .signatures import Analysis

EDITABLE = ("tags", "priority", "title", "note", "status")
PROTECTED = ("tags", "priority", "title")          # fields that scans would otherwise overwrite
STATUSES = ("nowe", "potwierdzone", "w-trakcie", "rozwiązane", "fałszywy-alarm", "zaakceptowane-ryzyko")


def upsert(db, cipher, run_id: int, ip: str, port: int, proto: str, analysis: Analysis, seen: str) -> int:
    auto = {"technologies": analysis.technologies, "tags": analysis.tags, "priority": analysis.priority,
            "title": analysis.title, "reasons": analysis.reasons}
    seal = cipher.seal
    sealed = {"auto": seal(json.dumps(auto), "findings.auto"),
              "technologies": seal(json.dumps(analysis.technologies), "findings.technologies"),
              "reasons": seal(json.dumps(analysis.reasons), "findings.reasons"),
              "title": seal(analysis.title, "findings.title")}
    row = db.execute("SELECT id, edited FROM findings WHERE ip=? AND port=? AND proto=?", (ip, port, proto)).fetchone()
    if row is None:
        return db.execute(
            "INSERT INTO findings (ip, port, proto, state, auto, technologies, tags, priority, title, reasons, first_seen,"
            " last_seen, first_run_id, last_run_id) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ip, port, proto, sealed["auto"], sealed["technologies"], json.dumps(analysis.tags),
             analysis.priority, sealed["title"], sealed["reasons"], seen, seen, run_id, run_id)).lastrowid
    edited = set(json.loads(row["edited"]))
    values = {"state": "active", "auto": sealed["auto"], "technologies": sealed["technologies"],
              "reasons": sealed["reasons"], "last_seen": seen, "last_run_id": run_id}
    if "tags" not in edited:
        values["tags"] = json.dumps(analysis.tags)
    if "priority" not in edited:
        values["priority"] = analysis.priority
    if "title" not in edited:
        values["title"] = sealed["title"]
    db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), row["id"]))
    return row["id"]


def edit(db, cipher, finding_id: int, changes: dict) -> None:
    row = db.execute("SELECT edited FROM findings WHERE id=?", (finding_id,)).fetchone()
    if row is None:
        raise KeyError(finding_id)
    edited = list(json.loads(row["edited"]))
    values = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            continue
        if key == "status" and value not in STATUSES:
            raise ValueError(f"Status musi być jednym z: {', '.join(STATUSES)}.")
        if key == "priority" and not 1 <= int(value) <= 5:
            raise ValueError("Priorytet musi być od 1 do 5.")
        if key == "tags":
            value = json.dumps(sorted({str(t).strip().lower() for t in value if str(t).strip()}))
        if key == "note":
            value = cipher.seal(value, "findings.note")
        if key == "title":
            value = cipher.seal(str(value).strip(), "findings.title")
        values[key] = value
        if key not in edited:
            edited.append(key)
    if not values:
        raise ValueError("Nic do zmiany. Pola: " + ", ".join(EDITABLE) + ".")
    values.update(edited=json.dumps(edited), edited_at=store.now())
    db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), finding_id))


def reset(db, cipher, finding_id: int, fields: list[str] | None = None) -> None:
    """Gives fields back to the scans. Without `fields`: all protected fields."""
    row = db.execute("SELECT auto, edited FROM findings WHERE id=?", (finding_id,)).fetchone()
    if row is None:
        raise KeyError(finding_id)
    auto, edited = json.loads(cipher.open(row["auto"], "findings.auto")), json.loads(row["edited"])
    fields = [f for f in (fields or PROTECTED) if f in PROTECTED]
    values = {"tags": json.dumps(auto["tags"]), "priority": auto["priority"], "title": cipher.seal(auto["title"], "findings.title")}
    values = {k: v for k, v in values.items() if k in fields}
    values["edited"] = json.dumps([f for f in edited if f not in fields])
    db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), finding_id))


def to_dict(row, cipher) -> dict:
    out = dict(row)
    for key in ("technologies", "reasons", "auto"):
        out[key] = json.loads(cipher.open(out[key], f"findings.{key}"))
    for key in ("tags", "edited"):
        out[key] = json.loads(out[key])
    out["title"] = cipher.open(out["title"], "findings.title")
    out["note"] = cipher.open(out["note"], "findings.note")
    out["is_edited"] = bool(out["edited"])
    return out
