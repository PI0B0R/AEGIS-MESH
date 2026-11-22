"""Stage 2 API: findings (with protected manual edits), changes after each scan, host cards,
unstable hosts, signatures, dashboard summary and export."""
import csv
import io
import json
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import db as store
from . import findings as F
from . import signatures as sigs
from . import web as webprobe


class FindingPatch(BaseModel):
    tags: list[str] | None = Field(None, description="Tagi. Po edycji skan ich nie nadpisze.", examples=[["staging", "do-sprawdzenia"]])
    priority: int | None = Field(None, ge=1, le=5, description="Priorytet 1-5. Po edycji skan go nie nadpisze.")
    title: str | None = Field(None, max_length=300, description="Jedno zdanie: dlaczego to ważne.")
    note: str | None = Field(None, max_length=5000, description="Notatka (przechowywana zaszyfrowana).")
    status: str | None = Field(None, description="Jeden z: " + ", ".join(F.STATUSES))


class ResetIn(BaseModel):
    fields: list[str] | None = Field(None, description="Pola do oddania skanom: tags, priority, title. Puste = wszystkie.")


class SignatureIn(BaseModel):
    id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,40}$", description="Identyfikator; nadany sam, jeśli pusty.")
    name: str = Field(..., max_length=80, examples=["Panel Moja-Aplikacja"])
    kind: str = Field(..., description="Rodzaj: " + ", ".join(sigs.KINDS), examples=["title"])
    pattern: str = Field("", max_length=500, description="Wyrażenie regularne (bez rozróżniania wielkości liter). "
                         "Grupa w nawiasach = wersja. Dla favicony: hash mmh3 lub md5.", examples=["Moja-Aplikacja v([\\d.]+)"])
    field: str | None = Field(None, description="Nazwa nagłówka (header) albo ścieżka (path), np. /admin.")
    category: str = Field("inne", description="Kategoria: " + ", ".join(sigs.CATEGORIES))
    priority: int = Field(2, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    message: str = Field("", max_length=300, description="Jedno zdanie po polsku: dlaczego to ważne.")
    status: list[int] | None = Field(None, description="Dla ścieżek: akceptowane kody HTTP (domyślnie 200, 401, 403).")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "sygnatura"


def register(app: FastAPI, conn, cipher, engine) -> None:
    def open_json(value, column, default=None):
        text = cipher.open(value, column)
        return json.loads(text) if text else default

    # ---- dashboard ---------------------------------------------------------------
    @app.get("/api/summary", tags=["pulpit"], summary="Kafelki pulpitu: aktywne hosty, zmiany, wysoki priorytet")
    def summary():
        db = conn()
        try:
            last = db.execute("SELECT * FROM scan_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
            counts = dict(db.execute("SELECT state, COUNT(*) FROM hosts GROUP BY state").fetchall())
            high = db.execute("SELECT COUNT(*) FROM findings WHERE state='active' AND priority>=4 "
                              "AND status NOT IN ('rozwiązane','fałszywy-alarm','zaakceptowane-ryzyko')").fetchone()[0]
            changes = db.execute("SELECT COUNT(*) FROM changes WHERE run_id=?", (last["id"],)).fetchone()[0] if last else 0
            return {"active_hosts": counts.get("active", 0), "unstable_hosts": counts.get("unstable", 0),
                    "inactive_hosts": counts.get("inactive", 0), "changes_since_last_scan": changes,
                    "high_priority": high, "last_scan": dict(last) if last else None,
                    "active_run": engine.active_run_id()}
        finally:
            db.close()

    @app.get("/api/changes", tags=["pulpit"], summary="Co nowego: zmiany po skanie, od najważniejszych")
    def changes(run_id: int | None = None, limit: int = 50, min_priority: int = 1):
        db = conn()
        try:
            if run_id is None:
                last = db.execute("SELECT id FROM scan_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
                run_id = last["id"] if last else None
            if run_id is None:
                return {"run_id": None, "total": 0, "changes": []}
            rows = db.execute("SELECT * FROM changes WHERE run_id=? AND priority>=? ORDER BY priority DESC, id",
                              (run_id, min_priority)).fetchall()
            items = [dict(r, message=cipher.open(r["message"], "changes.message"),
                          detail=open_json(r["detail"], "changes.detail")) for r in rows]
            return {"run_id": run_id, "total": len(items), "changes": items[:limit]}
        finally:
            db.close()

    @app.get("/api/changes/history", tags=["pulpit"], summary="Liczba zmian w kolejnych skanach (wykres)")
    def changes_history(limit: int = 30):
        db = conn()
        try:
            rows = db.execute(
                "SELECT r.id, r.finished_at, r.hosts_up, c.kind, COUNT(c.id) AS n FROM scan_runs r "
                "LEFT JOIN changes c ON c.run_id=r.id WHERE r.status='done' GROUP BY r.id, c.kind ORDER BY r.id").fetchall()
            runs: dict = {}
            for r in rows:
                item = runs.setdefault(r["id"], {"run_id": r["id"], "finished_at": r["finished_at"],
                                                 "hosts_up": r["hosts_up"], "changes": {}})
                if r["kind"]:
                    item["changes"][r["kind"]] = r["n"]
            return {"runs": list(runs.values())[-limit:]}
        finally:
            db.close()

    # ---- findings ----------------------------------------------------------------
    def _finding_rows(db, ip=None, tag=None, min_priority=1, state="active", q=None):
        sql, args = "SELECT * FROM findings WHERE priority>=?", [min_priority]
        if state != "all":
            sql, args = sql + " AND state=?", args + [state]
        if ip:
            sql, args = sql + " AND ip=?", args + [ip]
        out = [F.to_dict(r, cipher) for r in db.execute(sql + " ORDER BY priority DESC, ip, port", args)]
        if tag:
            out = [f for f in out if tag.lower() in f["tags"]]
        if q:
            needle = q.lower()
            out = [f for f in out if needle in json.dumps(f, ensure_ascii=False).lower()]
        return out

    @app.get("/api/findings", tags=["znaleziska"], summary="Znaleziska (usługi z technologiami, tagami i priorytetem)")
    def list_findings(ip: str | None = None, tag: str | None = None, min_priority: int = 1, state: str = "active",
                      q: str | None = None, limit: int = 500):
        db = conn()
        try:
            items = _finding_rows(db, ip, tag, min_priority, state, q)
            return {"total": len(items), "findings": items[:limit]}
        finally:
            db.close()

    @app.get("/api/findings/{finding_id}", tags=["znaleziska"])
    def get_finding(finding_id: int):
        db = conn()
        try:
            row = db.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Nie ma takiego znaleziska.")
            return F.to_dict(row, cipher)
        finally:
            db.close()

    @app.patch("/api/findings/{finding_id}", tags=["znaleziska"],
               summary="Ręczna edycja: zmienione pola dostają znaczek „edytowane” i skan ich nie nadpisze")
    def edit_finding(finding_id: int, body: FindingPatch):
        db = conn()
        try:
            try:
                F.edit(db, cipher, finding_id, body.model_dump(exclude_none=True))
            except KeyError:
                raise HTTPException(404, "Nie ma takiego znaleziska.") from None
            return F.to_dict(db.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone(), cipher)
        finally:
            db.close()

    @app.post("/api/findings/{finding_id}/reset", tags=["znaleziska"], summary="Oddaj pola z powrotem skanom")
    def reset_finding(finding_id: int, body: ResetIn | None = None):
        db = conn()
        try:
            try:
                F.reset(db, cipher, finding_id, body.fields if body else None)
            except KeyError:
                raise HTTPException(404, "Nie ma takiego znaleziska.") from None
            return F.to_dict(db.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone(), cipher)
        finally:
            db.close()

    # ---- hosts -------------------------------------------------------------------
    @app.get("/api/hosts/unstable", tags=["hosty"], summary="Hosty niestabilne i nieaktywne")
    def unstable_hosts():
        db = conn()
        try:
            rows = db.execute("SELECT * FROM hosts WHERE state!='active' ORDER BY state, ip").fetchall()
            return {"hosts": [dict(r, daypart=json.loads(r["daypart"]) if r["daypart"] else None) for r in rows]}
        finally:
            db.close()

    @app.get("/api/hosts/{ip}", tags=["hosty"], summary="Karta hosta: usługi, nagłówki, ciasteczka, certyfikat, historia")
    def host_card(ip: str):
        db = conn()
        try:
            host = db.execute("SELECT * FROM hosts WHERE ip=?", (ip,)).fetchone()
            if host is None:
                raise HTTPException(404, "Nie ma takiego hosta.")
            last = db.execute("SELECT MAX(run_id) FROM host_results WHERE ip=?", (ip,)).fetchone()[0]
            services = []
            for s in db.execute("SELECT * FROM services WHERE run_id=? AND ip=? ORDER BY port", (last, ip)):
                item = {k: s[k] for k in ("port", "proto", "state", "name", "tunnel")}
                for column in ("product", "version", "extrainfo"):
                    item[column] = cipher.open(s[column], f"services.{column}")
                web = db.execute("SELECT data FROM web WHERE run_id=? AND ip=? AND port=? AND proto=?",
                                 (last, ip, s["port"], s["proto"])).fetchone()
                if web:
                    data = open_json(web["data"], "web.data", {})
                    data.pop("body", None)
                    for page in (data.get("paths") or {}).values():
                        page.pop("body", None)
                    item["web"] = data
                services.append(item)
            history = [dict(r) for r in db.execute(
                "SELECT p.run_id, p.up, p.hour, r.started_at FROM host_presence p JOIN scan_runs r ON r.id=p.run_id "
                "WHERE p.ip=? ORDER BY p.run_id", (ip,))]
            changes_rows = db.execute("SELECT * FROM changes WHERE ip=? ORDER BY id DESC LIMIT 50", (ip,)).fetchall()
            return {**dict(host, daypart=json.loads(host["daypart"]) if host["daypart"] else None),
                    "last_run_id": last, "services": services, "findings": _finding_rows(db, ip=ip, state="all"),
                    "presence": history,
                    "changes": [dict(r, message=cipher.open(r["message"], "changes.message"),
                                     detail=open_json(r["detail"], "changes.detail")) for r in changes_rows]}
        finally:
            db.close()

    # ---- signatures --------------------------------------------------------------
    @app.get("/api/signatures", tags=["sygnatury"], summary="Sygnatury wbudowane i własne")
    def list_signatures():
        db = conn()
        try:
            custom = [sigs.from_row(r).to_dict() for r in db.execute("SELECT * FROM signatures ORDER BY id")]
            return {"kinds": sigs.KINDS, "categories": sigs.CATEGORIES,
                    "builtin": [s.to_dict() for s in sigs.builtin()], "custom": custom}
        finally:
            db.close()

    def _signature(body: SignatureIn) -> sigs.Signature:
        data = body.model_dump()
        data["id"] = data["id"] or _slug(body.name)
        data["tags"] = sorted({t.strip().lower() for t in data["tags"] if t.strip()})
        return sigs.Signature(**data, builtin=False)

    def _check_existing(db, sig: sigs.Signature, live_paths: bool) -> list[dict]:
        """Run one signature on the latest stored data of every active service."""
        out = []
        limiter = webprobe.RateLimiter(float(store.settings(db)["http_rate"]))
        rows = db.execute("SELECT f.ip, f.port, f.proto, f.last_run_id FROM findings f WHERE f.state='active' "
                          "ORDER BY f.ip, f.port").fetchall()
        for f in rows:
            s = db.execute("SELECT * FROM services WHERE run_id=? AND ip=? AND port=? AND proto=?",
                           (f["last_run_id"], f["ip"], f["port"], f["proto"])).fetchone()
            if s is None:
                continue
            service = {"name": s["name"], **{c: cipher.open(s[c], f"services.{c}") for c in ("product", "version", "extrainfo")}}
            w = db.execute("SELECT data FROM web WHERE run_id=? AND ip=? AND port=? AND proto=?",
                           (f["last_run_id"], f["ip"], f["port"], f["proto"])).fetchone()
            web = open_json(w["data"], "web.data") if w else None
            if sig.kind == "path" and web and live_paths and sig.field not in (web.get("paths") or {}):
                info = engine.probe(f["ip"], f["port"], web.get("scheme") == "https", paths=[sig.field], limiter=limiter)
                web = {**web, "paths": {**(web.get("paths") or {}), **info.to_dict()["paths"]}}
            found = sigs.match(sig, service, web)
            if found:
                out.append({"ip": f["ip"], "port": f["port"], "proto": f["proto"], "version": found.version,
                            "evidence": found.evidence})
        return out

    @app.post("/api/signatures/test", tags=["sygnatury"], summary="Sprawdź sygnaturę na istniejących hostach (bez zapisu)")
    def test_signature(body: SignatureIn, live: bool = True):
        db = conn()
        try:
            sig = _signature(body)
            matches = _check_existing(db, sig, live)
            return {"signature": sig.to_dict(), "matches": matches, "count": len(matches)}
        finally:
            db.close()

    @app.post("/api/signatures", tags=["sygnatury"], summary="Dodaj własną sygnaturę i zastosuj ją do istniejących hostów")
    def add_signature(body: SignatureIn):
        sig = _signature(body)
        if sig.id in {s.id for s in sigs.builtin()}:
            raise ValueError(f"Identyfikator {sig.id} należy do sygnatury wbudowanej. Wybierz inny.")
        db = conn()
        try:
            try:
                db.execute("INSERT INTO signatures (id, name, kind, pattern, field, category, priority, tags, message, "
                           "status, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                           (sig.id, sig.name, sig.kind, sig.pattern, sig.field, sig.category, sig.priority,
                            json.dumps(sig.tags), sig.message, json.dumps(sig.status) if sig.status else None, store.now()))
            except Exception as error:  # noqa: BLE001
                if "UNIQUE" in str(error):
                    raise ValueError(f"Sygnatura {sig.id} już istnieje.") from None
                raise
            updated = reanalyse(db)
            return {"signature": sig.to_dict(), "reanalysed_findings": updated}
        finally:
            db.close()

    @app.delete("/api/signatures/{sig_id}", tags=["sygnatury"])
    def delete_signature(sig_id: str):
        db = conn()
        try:
            if not db.execute("DELETE FROM signatures WHERE id=?", (sig_id,)).rowcount:
                raise HTTPException(404, "Nie ma takiej własnej sygnatury.")
            return {"deleted": sig_id, "reanalysed_findings": reanalyse(db)}
        finally:
            db.close()

    def reanalyse(db) -> int:
        """Recompute technologies, tags and priority of active findings from stored data (manual edits stay)."""
        signatures = sigs.load_all(db)
        count = 0
        db.execute("BEGIN IMMEDIATE")
        for f in db.execute("SELECT ip, port, proto, last_run_id, last_seen FROM findings WHERE state='active'").fetchall():
            s = db.execute("SELECT * FROM services WHERE run_id=? AND ip=? AND port=? AND proto=?",
                           (f["last_run_id"], f["ip"], f["port"], f["proto"])).fetchone()
            if s is None:
                continue
            service = {"name": s["name"], **{c: cipher.open(s[c], f"services.{c}") for c in ("product", "version", "extrainfo")}}
            w = db.execute("SELECT data FROM web WHERE run_id=? AND ip=? AND port=? AND proto=?",
                           (f["last_run_id"], f["ip"], f["port"], f["proto"])).fetchone()
            F.upsert(db, cipher, f["last_run_id"], f["ip"], f["port"], f["proto"],
                     sigs.analyse(service, open_json(w["data"], "web.data") if w else None, signatures), f["last_seen"])
            count += 1
        db.execute("COMMIT")
        return count

    # ---- export ------------------------------------------------------------------
    @app.get("/api/export/findings", tags=["eksport"], summary="Eksport znalezisk: JSON albo CSV")
    def export_findings(format: str = "json", state: str = "all"):
        db = conn()
        try:
            items = _finding_rows(db, state=state)
        finally:
            db.close()
        if format == "json":
            return {"findings": items}
        if format != "csv":
            raise ValueError("Format: json albo csv.")
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "ip", "port", "proto", "state", "priority", "title", "tags", "technologies", "status",
                         "note", "edited", "first_seen", "last_seen"])
        for f in items:
            tech = "; ".join(f"{t['name']} {t['version'] or ''}".strip() for t in f["technologies"])
            writer.writerow([f["id"], f["ip"], f["port"], f["proto"], f["state"], f["priority"], f["title"],
                             " ".join(f["tags"]), tech, f["status"], f["note"] or "", " ".join(f["edited"]),
                             f["first_seen"], f["last_seen"]])
        return PlainTextResponse(out.getvalue(), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": "attachment; filename=aegis-findings.csv"})
