"""DefectDojo export: chosen findings go to DefectDojo as informational findings with evidence.

Uses the standard API v2 endpoint /api/v2/reimport-scan/ with the "Generic Findings Import"
format and auto_create_context, so the product, engagement and test are created on the
first send and later sends update the same test instead of creating duplicates.
"""
import json
import uuid
import urllib.error
import urllib.request
from datetime import date

from . import db as store
from . import findings as F

SCAN_TYPE = "Generic Findings Import"
ENGAGEMENT = "AEGIS MESH discovery"
TEST_TITLE = "AEGIS MESH"
PRODUCT_TYPE = "AEGIS MESH"
SEVERITY = "Info"          # discovery results are information and evidence, not confirmed vulnerabilities


class DojoError(RuntimeError):
    pass


def _secret(cipher, s: dict, key: str) -> str | None:
    return cipher.open(s.get(key), f"settings.{key}") if s.get(key) else None


class Dojo:
    def __init__(self, s: dict, cipher, timeout: float = 60):
        self.url = (s.get("dojo_url") or "").rstrip("/")
        if not self.url:
            raise DojoError("DefectDojo nie jest skonfigurowany. Ustaw dojo_url oraz dojo_token albo dojo_user i dojo_password.")
        self.user, self.password = s.get("dojo_user"), _secret(cipher, s, "dojo_password")
        self.token, self.product, self.timeout = _secret(cipher, s, "dojo_token"), s.get("dojo_product") or "AEGIS MESH", timeout

    def _request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None,
                 auth: bool = True) -> dict:
        request = urllib.request.Request(self.url + path, data=body, method=method)
        request.add_header("Accept", "application/json")
        if content_type:
            request.add_header("Content-Type", content_type)
        if auth:
            request.add_header("Authorization", f"Token {self.api_token()}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - URL set by the admin
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            raise DojoError(f"DefectDojo odpowiedział {error.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as error:
            raise DojoError(f"Brak połączenia z DefectDojo ({self.url}): {error}") from None

    def api_token(self) -> str:
        if not self.token:
            if not (self.user and self.password):
                raise DojoError("Podaj dojo_token albo dojo_user i dojo_password.")
            body = json.dumps({"username": self.user, "password": self.password}).encode()
            self.token = self._request("POST", "/api/v2/api-token-auth/", body, "application/json", auth=False)["token"]
        return self.token

    def check(self) -> dict:
        me = self._request("GET", "/api/v2/users/?limit=1")
        return {"ok": True, "url": self.url, "users_visible": me.get("count")}

    def reimport(self, report: dict, tags: list[str]) -> dict:
        fields = {"scan_type": SCAN_TYPE, "product_name": self.product, "product_type_name": PRODUCT_TYPE,
                  "engagement_name": ENGAGEMENT, "test_title": TEST_TITLE, "auto_create_context": "true",
                  "minimum_severity": SEVERITY, "active": "true", "verified": "false", "close_old_findings": "false",
                  "do_not_reactivate": "false", "scan_date": date.today().isoformat(), "tags": ",".join(tags)}
        body, content_type = multipart(fields, ("file", "aegis-mesh.json", json.dumps(report).encode()))
        return self._request("POST", "/api/v2/reimport-scan/", body, content_type)


def multipart(fields: dict, file: tuple[str, str, bytes]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    name, filename, content = file
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f"Content-Type: application/json\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def to_generic(finding: dict, evidence: dict | None) -> dict:
    """One AEGIS finding as a DefectDojo Generic Findings Import entry."""
    tech = ", ".join(f"{t['name']} {t['version'] or ''}".strip() for t in finding["technologies"]) or "brak"
    reasons = "\n".join(f"- [{r['priority']}] {r['message']} ({r['evidence']})" if r.get("evidence") else
                        f"- [{r['priority']}] {r['message']}" for r in finding["reasons"])
    lines = [finding["title"], "", f"Usługa: {finding['ip']}:{finding['port']}/{finding['proto']}",
             f"Priorytet AEGIS: {finding['priority']}/5", f"Technologie: {tech}",
             f"Tagi: {', '.join(finding['tags']) or 'brak'}", f"Pierwszy raz: {finding['first_seen']}",
             f"Ostatnio: {finding['last_seen']}", "", "Dlaczego:", reasons or "- usługa wykryta skanem discovery"]
    if finding.get("note"):
        lines += ["", f"Notatka analityka: {finding['note']}"]
    if evidence:
        lines += ["", "Dowody (HTTP):", f"- URL: {evidence.get('url')} → {evidence.get('status')}",
                  f"- Tytuł: {evidence.get('title')}", f"- Server: {evidence.get('server')}"]
        if evidence.get("tls"):
            tls = evidence["tls"]
            lines.append(f"- Certyfikat: {tls.get('subject')} od {tls.get('issuer')}, ważny do {tls.get('not_after')}")
    return {
        "title": f"[AEGIS P{finding['priority']}] {finding['ip']}:{finding['port']} {finding['title']}"[:500],
        "description": "\n".join(lines),
        "severity": SEVERITY,
        "active": finding["state"] == "active",
        "verified": finding["status"] == "potwierdzone",
        "false_p": finding["status"] == "fałszywy-alarm",
        "date": (finding["last_seen"] or "")[:10] or date.today().isoformat(),
        "unique_id_from_tool": f"aegis:{finding['ip']}:{finding['port']}/{finding['proto']}",
        "vuln_id_from_tool": (finding["reasons"][0]["signature"] if finding["reasons"] else "service"),
        "component_name": finding["technologies"][0]["name"] if finding["technologies"] else None,
        "component_version": finding["technologies"][0]["version"] if finding["technologies"] else None,
        "endpoints": [{"host": finding["ip"], "port": finding["port"], "protocol":
                       (evidence or {}).get("scheme") or finding["proto"]}],
        "tags": ["aegis-mesh", f"priorytet-{finding['priority']}", *finding["tags"]][:20],
    }


def send(db, cipher, finding_ids: list[int] | None = None, min_priority: int | None = None,
         only_unsent: bool = False) -> dict:
    s = store.settings(db)
    client = Dojo(s, cipher)
    sql, args = "SELECT * FROM findings WHERE 1=1", []
    if finding_ids:
        sql += f" AND id IN ({','.join('?' * len(finding_ids))})"
        args += list(finding_ids)
    if min_priority is not None:
        sql, args = sql + " AND priority>=?", args + [min_priority]
    if only_unsent:
        sql += " AND dojo_sent_at IS NULL AND state='active'"
    rows = [F.to_dict(r, cipher) for r in db.execute(sql + " ORDER BY priority DESC, ip, port", args)]
    if not rows:
        return {"sent": 0, "message": "Brak znalezisk do wysłania."}
    entries = []
    for f in rows:
        w = db.execute("SELECT data FROM web WHERE run_id=? AND ip=? AND port=? AND proto=?",
                       (f["last_run_id"], f["ip"], f["port"], f["proto"])).fetchone()
        evidence = json.loads(cipher.open(w["data"], "web.data")) if w else None
        entries.append(to_generic(f, evidence))
    result = client.reimport({"findings": entries}, ["aegis-mesh"])
    sent_at, test_id = store.now(), result.get("test") or result.get("test_id")
    db.executemany("UPDATE findings SET dojo_sent_at=?, dojo_id=? WHERE id=?", [(sent_at, test_id, f["id"]) for f in rows])
    return {"sent": len(rows), "test_id": test_id, "engagement_id": result.get("engagement_id"),
            "product_id": result.get("product_id"), "statistics": result.get("statistics")}


def after_run(db_path: str, cipher):
    """Engine hook: with dojo_auto on, new important findings go to DefectDojo after every scan."""
    def hook(run_id: int, changes: list[dict]) -> None:
        db = store.connect(db_path)
        try:
            s = store.settings(db)
            if s.get("dojo_auto") and s.get("dojo_url"):
                send(db, cipher, min_priority=int(s["dojo_min_priority"]), only_unsent=True)
        finally:
            db.close()
    return hook
