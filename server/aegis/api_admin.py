"""Stage 3 API: login, users and roles, audit log, full export and import (JSON/CSV)."""
import csv
import io
import json

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth
from . import db as store
from . import findings as F
from .scheduler import validate
from .signatures import Signature
from .targets import classify


class LoginIn(BaseModel):
    username: str = Field(..., examples=["admin"])
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=auth.MIN_PASSWORD)


class UserIn(BaseModel):
    username: str = Field(..., examples=["jan.kowalski"])
    password: str = Field(..., min_length=auth.MIN_PASSWORD)
    role: str = Field(..., description="Rola: " + ", ".join(auth.ROLES))


class UserPatch(BaseModel):
    role: str | None = None
    password: str | None = Field(None, min_length=auth.MIN_PASSWORD)
    disabled: bool | None = None
    unlock: bool | None = Field(None, description="Zdejmij blokadę po błędnych próbach.")


class FindingsImportIn(BaseModel):
    content: str = Field(..., description="CSV (jak z eksportu) albo JSON {\"findings\": [...]}")
    filename: str | None = None


class FullImportIn(BaseModel):
    data: dict = Field(..., description="Zawartość pliku z GET /api/export/full")


def _user_row(row) -> dict:
    return {k: row[k] for k in ("id", "username", "role", "failed_logins", "locked_until", "last_login", "created_at")} | \
        {"disabled": bool(row["disabled"])}


def register(app: FastAPI, conn, cipher) -> None:
    # ---- login -------------------------------------------------------------------
    @app.post("/api/auth/login", tags=["logowanie"], summary="Zaloguj się i dostań token (wygasa po 12 h)")
    def do_login(body: LoginIn):
        db = conn()
        try:
            return auth.login(db, body.username, body.password)
        finally:
            db.close()

    @app.post("/api/auth/logout", tags=["logowanie"])
    def do_logout(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        db = conn()
        try:
            auth.logout(db, token)
            return {"ok": True}
        finally:
            db.close()

    @app.get("/api/auth/me", tags=["logowanie"], summary="Kim jestem i co mogę")
    def me(request: Request):
        user = request.state.user
        return {**user, "can": {role: auth.can(user, role) for role in auth.ROLES}}

    @app.post("/api/auth/password", tags=["logowanie"], summary="Zmień własne hasło")
    def change_password(request: Request, body: PasswordIn):
        user = request.state.user
        db = conn()
        try:
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if row is None or not auth.verify_password(body.old_password, row["password_hash"]):
                raise HTTPException(400, "Obecne hasło jest nieprawidłowe.")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.new_password), user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            return {"ok": True, "message": "Hasło zmienione. Zaloguj się ponownie."}
        finally:
            db.close()

    # ---- users -------------------------------------------------------------------
    @app.get("/api/users", tags=["użytkownicy"])
    def list_users():
        db = conn()
        try:
            return {"roles": auth.ROLES, "users": [_user_row(r) for r in db.execute("SELECT * FROM users ORDER BY username")]}
        finally:
            db.close()

    @app.post("/api/users", tags=["użytkownicy"], summary="Załóż konto (tylko admin, bez samodzielnej rejestracji)")
    def add_user(body: UserIn):
        db = conn()
        try:
            user_id = auth.create_user(db, body.username, body.password, body.role)
            return _user_row(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        finally:
            db.close()

    def _admins_left(db, without: int) -> int:
        return db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id!=?", (without,)).fetchone()[0]

    @app.patch("/api/users/{user_id}", tags=["użytkownicy"])
    def edit_user(user_id: int, body: UserPatch):
        db = conn()
        try:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Nie ma takiego użytkownika.")
            if body.role is not None and body.role not in auth.ROLES:
                raise ValueError(f"Rola musi być jedną z: {', '.join(auth.ROLES)}.")
            demoted = (body.role and body.role != "admin") or body.disabled
            if row["role"] == "admin" and demoted and not _admins_left(db, user_id):
                raise ValueError("To ostatni aktywny admin. Najpierw nadaj rolę admin komuś innemu.")
            values = {}
            if body.role:
                values["role"] = body.role
            if body.password:
                values["password_hash"] = auth.hash_password(body.password)
            if body.disabled is not None:
                values["disabled"] = int(body.disabled)
            if body.unlock:
                values.update(failed_logins=0, locked_until=None)
            if values:
                db.execute(f"UPDATE users SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), user_id))
            if body.password or body.disabled or body.role:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))     # changed rights: log in again
            return _user_row(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        finally:
            db.close()

    @app.delete("/api/users/{user_id}", tags=["użytkownicy"])
    def delete_user(user_id: int):
        db = conn()
        try:
            row = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Nie ma takiego użytkownika.")
            if row["role"] == "admin" and not _admins_left(db, user_id):
                raise ValueError("Nie można usunąć ostatniego admina.")
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            return {"deleted": user_id}
        finally:
            db.close()

    @app.get("/api/audit", tags=["użytkownicy"], summary="Dziennik: logowania i każda zmiana przez API")
    def audit_log(limit: int = 200):
        db = conn()
        try:
            return {"entries": [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]}
        finally:
            db.close()

    # ---- findings import (edits) ---------------------------------------------------
    @app.post("/api/import/findings", tags=["eksport"],
              summary="Import edycji znalezisk z CSV/JSON (tagi, priorytet, tytuł, notatka, status)")
    def import_findings(body: FindingsImportIn):
        text = body.content.lstrip("﻿ \n")
        if (body.filename or "").lower().endswith(".json") or text[:1] in "[{":
            data = json.loads(text)
            items = data.get("findings", []) if isinstance(data, dict) else data
        else:
            items = list(csv.DictReader(io.StringIO(text)))
        db = conn()
        updated, missing = 0, []
        try:
            for item in items:
                try:
                    port = int(item.get("port"))
                except (TypeError, ValueError):
                    raise ValueError(f"Brak poprawnego portu w wierszu: {item}") from None
                ip, proto = str(item.get("ip", "")).strip(), str(item.get("proto") or "tcp").strip()
                row = db.execute("SELECT * FROM findings WHERE ip=? AND port=? AND proto=?", (ip, port, proto)).fetchone()
                if row is None:
                    missing.append(f"{ip}:{port}/{proto}")
                    continue
                current = F.to_dict(row, cipher)
                changes = {}
                tags = item.get("tags")
                if isinstance(tags, str):
                    tags = [t for t in tags.replace(",", " ").split() if t]
                if tags is not None and sorted(tags) != sorted(current["tags"]):
                    changes["tags"] = tags
                for key in ("priority", "title", "note", "status"):
                    value = item.get(key)
                    if value in (None, ""):
                        continue
                    value = int(value) if key == "priority" else str(value)
                    if value != current[key]:
                        changes[key] = value
                if changes:
                    F.edit(db, cipher, row["id"], changes)
                    updated += 1
            return {"updated": updated, "unchanged": len(items) - updated - len(missing), "not_found": missing}
        finally:
            db.close()

    # ---- full export and import ---------------------------------------------------
    @app.get("/api/export/full", tags=["eksport"], summary="Pełny eksport JSON: cele, harmonogramy, sygnatury, hosty, znaleziska")
    def export_full():
        db = conn()
        try:
            targets = [dict(r, note=cipher.open(r["note"], "targets.note")) for r in db.execute("SELECT * FROM targets")]
            findings = [F.to_dict(r, cipher) for r in db.execute("SELECT * FROM findings ORDER BY ip, port")]
            settings = {k: v for k, v in store.settings(db).items() if not k.startswith("dojo_") and k != "measured_rate"}
            return {"format": "aegis-mesh/1", "exported_at": store.now(), "settings": settings,
                    "targets": targets,
                    "schedules": [dict(r) for r in db.execute("SELECT * FROM schedules")],
                    "signatures": [dict(r, tags=json.loads(r["tags"]), status=json.loads(r["status"]) if r["status"] else None)
                                   for r in db.execute("SELECT * FROM signatures")],
                    "hosts": [dict(r, daypart=json.loads(r["daypart"]) if r["daypart"] else None)
                              for r in db.execute("SELECT * FROM hosts ORDER BY ip")],
                    "findings": findings,
                    "scans": [dict(r) for r in db.execute("SELECT * FROM scan_runs ORDER BY id")]}
        finally:
            db.close()

    @app.post("/api/import/full", tags=["eksport"], summary="Pełny import JSON (np. na nowy serwer). Istniejące dane zostają.")
    def import_full(body: FullImportIn):
        data = body.data
        if data.get("format") != "aegis-mesh/1":
            raise ValueError("To nie jest plik z eksportu AEGIS MESH (pole format: aegis-mesh/1).")
        db = conn()
        counts = {"targets": 0, "schedules": 0, "signatures": 0, "findings": 0}
        try:
            db.execute("BEGIN IMMEDIATE")
            for t in data.get("targets", []):
                target = classify(t["value"], t.get("note"))
                counts["targets"] += db.execute(
                    "INSERT OR IGNORE INTO targets (kind, value, size, note, source, enabled, created_at) "
                    "VALUES (?, ?, ?, ?, 'import', ?, ?)", (target.kind, target.value, target.size,
                                                             cipher.seal(target.note, "targets.note"),
                                                             int(t.get("enabled", 1)), store.now())).rowcount
            for sch in data.get("schedules", []):
                validate(sch["frequency"], sch["window_start"], sch["window_end"])
                if not db.execute("SELECT 1 FROM schedules WHERE name=?", (sch["name"],)).fetchone():
                    db.execute("INSERT INTO schedules (name, frequency, window_start, window_end, enabled, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?)", (sch["name"], sch["frequency"], sch["window_start"],
                                                             sch["window_end"], int(sch.get("enabled", 1)), store.now()))
                    counts["schedules"] += 1
            for sig in data.get("signatures", []):
                s = Signature(**{k: sig.get(k) for k in ("id", "name", "kind", "pattern", "field", "category",
                                                         "priority", "tags", "message", "status") if k in sig},
                              builtin=False)
                counts["signatures"] += db.execute(
                    "INSERT OR IGNORE INTO signatures (id, name, kind, pattern, field, category, priority, tags, message, "
                    "status, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (s.id, s.name, s.kind, s.pattern, s.field, s.category, s.priority, json.dumps(s.tags), s.message,
                     json.dumps(s.status) if s.status else None, store.now())).rowcount
            for f in data.get("findings", []):
                row = db.execute("SELECT id FROM findings WHERE ip=? AND port=? AND proto=?",
                                 (f["ip"], f["port"], f["proto"])).fetchone()
                if row is None:
                    auto = f.get("auto") or {k: f.get(k) for k in ("technologies", "tags", "priority", "title", "reasons")}
                    finding_id = db.execute(
                        "INSERT INTO findings (ip, port, proto, state, auto, technologies, tags, priority, title, reasons, "
                        "status, edited, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)",
                        (f["ip"], f["port"], f["proto"], f.get("state", "active"),
                         cipher.seal(json.dumps(auto), "findings.auto"),
                         cipher.seal(json.dumps(f.get("technologies", [])), "findings.technologies"),
                         json.dumps(f.get("tags", [])), int(f.get("priority", 1)),
                         cipher.seal(f.get("title") or "Usługa.", "findings.title"),
                         cipher.seal(json.dumps(f.get("reasons", [])), "findings.reasons"), "nowe",
                         f.get("first_seen") or store.now(), f.get("last_seen") or store.now())).lastrowid
                else:
                    finding_id = row["id"]
                edits = {k: f[k] for k in f.get("edited", []) if k in F.EDITABLE and f.get(k) is not None}
                if f.get("status") and f["status"] != "nowe":
                    edits.setdefault("status", f["status"])
                if edits:
                    F.edit(db, cipher, finding_id, edits)
                counts["findings"] += 1
            db.execute("COMMIT")
            return {"imported": counts}
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
