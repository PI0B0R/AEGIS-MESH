"""Logins, roles and permissions (task points 12-13).

Roles, each one includes the rights of the roles below it:
    admin      everything: users, DefectDojo, full import
    edytor     targets, signatures, scans, schedules, scan settings ("dodawanie, usuwanie, modyfikacja")
    analityk   editing findings: tags, priority, notes, status, sending to DefectDojo ("modyfikacja i odczyt")
    czytelnik  read only

No self-registration: only an admin creates accounts. Passwords are stored as scrypt hashes.
Five wrong passwords lock the account for 15 minutes. A login gives a token that expires.
Every API route has a minimum role in PERMISSIONS; a route that is missing there needs admin.
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db as store

ROLES = ("czytelnik", "analityk", "edytor", "admin")
LEVEL = {role: i for i, role in enumerate(ROLES)}
MAX_FAILURES, LOCK_MINUTES, TOKEN_HOURS = 5, 15, 12
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 10

PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login"), ("GET", "/")}
PERMISSIONS = {
    # read: everyone with an account
    **{("GET", p): "czytelnik" for p in (
        "/api/auth/me", "/api/targets", "/api/scans", "/api/scans/estimate", "/api/scans/{run_id}", "/api/hosts",
        "/api/hosts/unstable", "/api/hosts/{ip}", "/api/inventory", "/api/summary", "/api/changes",
        "/api/changes/history", "/api/findings", "/api/findings/{finding_id}", "/api/signatures",
        "/api/schedules", "/api/schedules/templates", "/api/export/findings")},
    ("POST", "/api/auth/logout"): "czytelnik",
    ("POST", "/api/auth/password"): "czytelnik",
    # findings
    ("PATCH", "/api/findings/{finding_id}"): "analityk",
    ("POST", "/api/findings/{finding_id}/reset"): "analityk",
    ("POST", "/api/import/findings"): "analityk",
    ("POST", "/api/dojo/send"): "analityk",
    ("POST", "/api/dojo/check"): "analityk",
    # targets, scans, signatures, schedules, scan settings
    ("GET", "/api/settings"): "edytor",
    ("PATCH", "/api/settings"): "edytor",          # DefectDojo keys inside need admin (checked in the route)
    ("POST", "/api/targets"): "edytor",
    ("POST", "/api/targets/import"): "edytor",
    ("PATCH", "/api/targets/{target_id}"): "edytor",
    ("DELETE", "/api/targets/{target_id}"): "edytor",
    ("POST", "/api/scans"): "edytor",
    ("POST", "/api/scans/{run_id}/pause"): "edytor",
    ("POST", "/api/scans/{run_id}/resume"): "edytor",
    ("POST", "/api/schedules"): "edytor",
    ("DELETE", "/api/schedules/{sched_id}"): "edytor",
    ("POST", "/api/signatures/test"): "edytor",
    ("POST", "/api/signatures"): "edytor",
    ("DELETE", "/api/signatures/{sig_id}"): "edytor",
    # admin: users, audit log, full export and import
    ("GET", "/api/users"): "admin",
    ("POST", "/api/users"): "admin",
    ("PATCH", "/api/users/{user_id}"): "admin",
    ("DELETE", "/api/users/{user_id}"): "admin",
    ("GET", "/api/audit"): "admin",
    ("GET", "/api/export/full"): "admin",
    ("POST", "/api/import/full"): "admin",
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, TypeError):
        return False


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Hasło musi mieć co najmniej {MIN_PASSWORD} znaków.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(db, username: str, password: str, role: str) -> int:
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise ValueError("Login: 2-32 znaki, małe litery, cyfry, kropka, myślnik lub podkreślenie.")
    if role not in ROLES:
        raise ValueError(f"Rola musi być jedną z: {', '.join(ROLES)}.")
    check_password(password)
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError(f"Użytkownik {username} już istnieje.")
    return db.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                      (username, hash_password(password), role, store.now())).lastrowid


def login(db, username: str, password: str) -> dict:
    username = (username or "").strip().lower()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    wrong = HTTPException(401, "Nieprawidłowy login lub hasło.")
    if user is None:
        verify_password(password, hash_password("x" * MIN_PASSWORD))      # same time as for a real account
        raise wrong
    if user["disabled"]:
        raise HTTPException(403, "Konto jest wyłączone.")
    locked = store.parse_time(user["locked_until"])
    if locked and locked > _now():
        minutes = max(1, int((locked - _now()).total_seconds() // 60) + 1)
        raise HTTPException(429, f"Konto zablokowane po {MAX_FAILURES} błędnych próbach. Spróbuj za {minutes} min.")
    if not verify_password(password, user["password_hash"]):
        failures = user["failed_logins"] + 1
        until = (_now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds") if failures >= MAX_FAILURES else None
        db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                   (0 if until else failures, until, user["id"]))
        audit(db, user["username"], "login-failed", None)
        raise wrong
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=TOKEN_HOURS)
    db.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login=? WHERE id=?", (store.now(), user["id"]))
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (store.now(),))
    db.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (_token_hash(token), user["id"], store.now(), expires.isoformat(timespec="seconds")))
    audit(db, user["username"], "login", None)
    return {"token": token, "expires_at": expires.isoformat(timespec="seconds"),
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


def user_for_token(db, token: str) -> dict | None:
    row = db.execute("SELECT u.id, u.username, u.role, u.disabled, s.expires_at FROM sessions s "
                     "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (_token_hash(token),)).fetchone()
    if row is None or row["disabled"] or store.parse_time(row["expires_at"]) <= _now():
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def logout(db, token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def audit(db, username: str | None, action: str, target: str | None) -> None:
    db.execute("INSERT INTO audit (at, username, action, target) VALUES (?, ?, ?, ?)",
               (store.now(), username, action, target))


def ensure_first_admin(db, data_dir) -> str | None:
    """No users yet: create `admin` with AEGIS_ADMIN_PASSWORD or a random password saved next to the database."""
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return None
    password = os.environ.get("AEGIS_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    create_user(db, "admin", password, "admin")
    if not os.environ.get("AEGIS_ADMIN_PASSWORD"):
        path = data_dir / "admin-password.txt"
        path.write_text(f"login: admin\nhasło: {password}\nZmień je po pierwszym logowaniu.\n", encoding="utf-8")
        path.chmod(0o600)
        print(f"AEGIS: utworzono konto admin. Hasło zapisane w {path}", flush=True)
    return password


def terminal_token(data_dir) -> str:
    """Token for `aegis` commands run inside the server container; whoever can run them can read the database anyway."""
    token = secrets.token_urlsafe(32)
    path = data_dir / "cli.token"
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return token


bearer = HTTPBearer(auto_error=False, description="Token z POST /api/auth/login (albo z pliku cli.token w kontenerze).")


def dependency(conn, cli_token: str):
    def authorize(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict | None:
        route = request.scope.get("route")
        key = (request.method, getattr(route, "path", request.url.path))
        if key in PUBLIC:
            return None
        if credentials is None:
            raise HTTPException(401, "Zaloguj się: POST /api/auth/login, potem nagłówek Authorization: Bearer TOKEN.",
                                headers={"WWW-Authenticate": "Bearer"})
        if hmac.compare_digest(credentials.credentials, cli_token):
            user = {"id": 0, "username": "terminal", "role": "admin"}
        else:
            db = conn()
            try:
                user = user_for_token(db, credentials.credentials)
            finally:
                db.close()
        if user is None:
            raise HTTPException(401, "Sesja wygasła albo token jest nieprawidłowy. Zaloguj się ponownie.",
                                headers={"WWW-Authenticate": "Bearer"})
        needed = PERMISSIONS.get(key, "admin")
        if LEVEL[user["role"]] < LEVEL[needed]:
            raise HTTPException(403, f"Brak uprawnień: potrzebna rola {needed} (masz: {user['role']}).")
        request.state.user = user
        if request.method != "GET":
            db = conn()
            try:
                audit(db, user["username"], f"{request.method} {key[1]}", request.url.path)
            finally:
                db.close()
        return user
    return authorize


def can(user: dict | None, role: str) -> bool:
    return bool(user) and LEVEL[user["role"]] >= LEVEL[role]
