"""Logins, roles, lockout, token expiry, audit log and the permission table."""
import pytest
from fastapi.testclient import TestClient

from aegis import auth
from aegis.api import create_app
from aegis.db import connect


@pytest.fixture
def app(data_dir, tmp_path, fake_nmap, monkeypatch):
    monkeypatch.setenv("AEGIS_ADMIN_PASSWORD", "admin-haslo-123")
    return create_app(data_dir, tmp_path / "keys" / "k", background=False, run_chunk=fake_nmap)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def people(client):
    admin = login(client, "admin", "admin-haslo-123")
    out = {"admin": admin}
    for role in ("edytor", "analityk", "czytelnik"):
        r = client.post("/api/users", json={"username": role, "password": f"{role}-haslo-1", "role": role}, headers=admin)
        assert r.status_code == 200, r.text
        out[role] = login(client, role, f"{role}-haslo-1")
    return out


def test_everything_needs_a_login_except_health_and_login(client):
    health = client.get("/api/health").json()
    assert health["service"] == "aegis-mesh" and set(health) == {"ok", "service", "version", "nmap"}   # the phone looks for this
    assert client.get("/").status_code == 200                          # the panel page itself
    assert client.get("/api/targets").status_code == 401
    assert client.get("/api/findings", headers={"Authorization": "Bearer zly"}).status_code == 401


def test_every_api_route_has_a_permission(app):
    routes = {(m, r.path) for r in app.routes if getattr(r, "methods", None) and r.path.startswith("/api/")
              for m in r.methods}
    missing = routes - set(auth.PERMISSIONS) - auth.PUBLIC
    assert not missing, f"routes without an explicit role: {sorted(missing)}"


@pytest.mark.parametrize("method,path,body,lowest", [
    ("GET", "/api/findings", None, "czytelnik"),
    ("GET", "/api/inventory", None, "czytelnik"),
    ("PATCH", "/api/findings/999", {"priority": 2}, "analityk"),
    ("POST", "/api/targets", {"text": "10.0.0.1"}, "edytor"),
    ("POST", "/api/signatures/test", {"name": "x", "kind": "title", "pattern": "x"}, "edytor"),
    ("GET", "/api/settings", None, "edytor"),
    ("GET", "/api/users", None, "admin"),
    ("GET", "/api/export/full", None, "admin"),
])
def test_roles_include_the_rights_of_lower_roles(client, people, method, path, body, lowest):
    for role in auth.ROLES:
        r = client.request(method, path, json=body, headers=people[role])
        allowed = auth.LEVEL[role] >= auth.LEVEL[lowest]
        assert (r.status_code != 403) == allowed, (role, path, r.status_code)


def test_dojo_settings_need_admin_but_scan_settings_need_editor(client, people):
    assert client.patch("/api/settings", json={"rate": 100}, headers=people["edytor"]).status_code == 200
    assert client.patch("/api/settings", json={"dojo_url": "http://x"}, headers=people["edytor"]).status_code == 403
    assert client.patch("/api/settings", json={"dojo_url": "http://x"}, headers=people["admin"]).status_code == 200


def test_me_tells_the_panel_what_to_show(client, people):
    me = client.get("/api/auth/me", headers=people["analityk"]).json()
    assert me["role"] == "analityk" and me["can"] == {"czytelnik": True, "analityk": True, "edytor": False, "admin": False}


def test_five_wrong_passwords_lock_the_account(client, people, app):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "czytelnik", "password": "zle-haslo-00"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "czytelnik", "password": "czytelnik-haslo-1"})
    assert r.status_code == 429 and "15 min" in r.json()["detail"]
    users = client.get("/api/users", headers=people["admin"]).json()["users"]
    uid = next(u["id"] for u in users if u["username"] == "czytelnik")
    client.patch(f"/api/users/{uid}", json={"unlock": True}, headers=people["admin"])
    login(client, "czytelnik", "czytelnik-haslo-1")


def test_unknown_user_gets_the_same_answer(client):
    r = client.post("/api/auth/login", json={"username": "nikt", "password": "cokolwiek-123"})
    assert r.status_code == 401 and r.json()["detail"] == "Nieprawidłowy login lub hasło."


def test_tokens_expire_and_logout_ends_the_session(client, people, app):
    db = connect(app.state.db_path)
    db.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE user_id="
               "(SELECT id FROM users WHERE username='czytelnik')")
    assert client.get("/api/findings", headers=people["czytelnik"]).status_code == 401
    assert client.post("/api/auth/logout", headers=people["analityk"]).status_code == 200
    assert client.get("/api/findings", headers=people["analityk"]).status_code == 401


def test_role_change_and_disable_end_sessions_and_last_admin_is_kept(client, people):
    users = {u["username"]: u["id"] for u in client.get("/api/users", headers=people["admin"]).json()["users"]}
    client.patch(f"/api/users/{users['edytor']}", json={"role": "czytelnik"}, headers=people["admin"])
    assert client.get("/api/findings", headers=people["edytor"]).status_code == 401           # must log in again
    assert client.patch(f"/api/users/{users['admin']}", json={"role": "czytelnik"}, headers=people["admin"]).status_code == 400
    assert client.delete(f"/api/users/{users['admin']}", headers=people["admin"]).status_code == 400
    client.patch(f"/api/users/{users['analityk']}", json={"disabled": True}, headers=people["admin"])
    assert client.post("/api/auth/login", json={"username": "analityk", "password": "analityk-haslo-1"}).status_code == 403


def test_bad_new_accounts_are_refused(client, people):
    for body in ({"username": "A B", "password": "dlugie-haslo-1", "role": "edytor"},
                 {"username": "ok", "password": "krotkie", "role": "edytor"},
                 {"username": "ok", "password": "dlugie-haslo-1", "role": "szef"},
                 {"username": "edytor", "password": "dlugie-haslo-1", "role": "edytor"}):
        assert client.post("/api/users", json=body, headers=people["admin"]).status_code in (400, 422), body


def test_own_password_change(client, people):
    r = client.post("/api/auth/password", json={"old_password": "zle", "new_password": "nowe-haslo-123"},
                    headers=people["czytelnik"])
    assert r.status_code == 400
    r = client.post("/api/auth/password", json={"old_password": "czytelnik-haslo-1", "new_password": "nowe-haslo-123"},
                    headers=people["czytelnik"])
    assert r.status_code == 200
    login(client, "czytelnik", "nowe-haslo-123")


def test_passwords_are_hashed_and_changes_are_audited(client, people, app, data_dir):
    raw = (data_dir / "aegis.sqlite3").read_bytes() + b"".join(p.read_bytes() for p in data_dir.glob("aegis.sqlite3-*"))
    assert b"edytor-haslo-1" not in raw and b"scrypt$" in raw
    client.post("/api/targets", json={"text": "10.0.0.1"}, headers=people["edytor"])
    entries = client.get("/api/audit", headers=people["admin"]).json()["entries"]
    assert any(e["username"] == "edytor" and e["action"] == "POST /api/targets" for e in entries)
    assert any(e["action"] == "login" for e in entries)


def test_first_admin_password_is_written_to_a_private_file(data_dir, tmp_path, fake_nmap, monkeypatch):
    monkeypatch.delenv("AEGIS_ADMIN_PASSWORD", raising=False)
    create_app(data_dir, tmp_path / "keys" / "k", background=False, run_chunk=fake_nmap)
    path = data_dir / "admin-password.txt"
    assert path.stat().st_mode & 0o777 == 0o600 and "login: admin" in path.read_text()
    assert (data_dir / "cli.token").stat().st_mode & 0o777 == 0o600
