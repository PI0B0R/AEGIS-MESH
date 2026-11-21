"""HTTP API. Interactive documentation: /docs (Swagger) and /redoc, schema: /openapi.json.

Stage 1 has no login yet (roles come in stage 3), so by default the server listens on
127.0.0.1 only and docker compose publishes the port on localhost only.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from . import db as store
from .crypto import FieldCipher
from .engine import Engine
from .scheduler import TEMPLATES, Scheduler, validate
from .targets import parse_any, parse_text


class TargetsIn(BaseModel):
    text: str = Field(..., description="Adresy IP, zakresy CIDR (10.0.0.0/24), zakresy a-b lub domeny. "
                                       "Rozdzielone spacją, przecinkiem albo nową linią.", examples=["10.0.0.0/24 example.com"])
    note: str | None = Field(None, description="Notatka do wszystkich podanych celów (przechowywana zaszyfrowana).")


class ImportIn(BaseModel):
    content: str = Field(..., description="Zawartość pliku CSV lub JSON (albo zwykły tekst).")
    filename: str | None = Field(None, description="Nazwa pliku; rozszerzenie .csv/.json pomaga rozpoznać format.")


class TargetPatch(BaseModel):
    enabled: bool | None = None
    note: str | None = None


class SettingsPatch(BaseModel):
    rate: int | None = Field(None, ge=1, le=100000, description="Limit pakietów na sekundę (nmap --max-rate).")
    threads: int | None = Field(None, ge=1, le=32, description="Ile podgrup skanować jednocześnie.")
    ports: str | None = Field(None, pattern=r"^[0-9,\-TU:]+$", description="Dozwolone porty, np. 22,80,443,8000-8100.")
    host_timeout: int | None = Field(None, ge=5, le=3600)
    max_retries: int | None = Field(None, ge=0, le=10)
    chunk_seconds: int | None = Field(None, ge=5, le=3600, description="Docelowy czas jednej podgrupy.")
    min_chunk: int | None = Field(None, ge=1, le=4096)
    max_chunk: int | None = Field(None, ge=1, le=65536)


class ScheduleIn(BaseModel):
    template: Literal["nocny", "weekend", "co-6-godzin", "co-godzine"] | None = Field(
        None, description="Gotowy szablon. Jeśli podany, pozostałe pola są opcjonalne.")
    name: str | None = None
    frequency: str | None = Field(None, examples=["daily", "weekly:5", "every:6h"])
    window_start: str | None = Field(None, examples=["22:00"])
    window_end: str | None = Field(None, examples=["06:00"])
    enabled: bool = True


def _target_row(row, cipher: FieldCipher) -> dict:
    out = dict(row)
    out["note"] = cipher.open(out["note"], "targets.note")
    out["enabled"] = bool(out["enabled"])
    return out


def _run_row(row) -> dict:
    out = dict(row)
    out["calibrated"] = bool(out["calibrated"])
    return out


def create_app(data_dir: str | os.PathLike | None = None, key_path: str | os.PathLike | None = None,
               background: bool = True, run_chunk=None) -> FastAPI:
    data = Path(data_dir or os.environ.get("AEGIS_DATA", "./data"))
    data.mkdir(parents=True, exist_ok=True)
    db_path = str(data / "aegis.sqlite3")
    key = Path(key_path or os.environ.get("AEGIS_KEY_FILE", data.parent / "keys" / "aegis.key"))
    store.initialize(db_path)
    cipher = FieldCipher.load(key)
    engine = Engine(db_path, cipher, **({"run_chunk": run_chunk} if run_chunk else {}))
    scheduler = Scheduler(db_path, engine)

    @asynccontextmanager
    async def lifespan(_app):
        if background:
            scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(title="AEGIS MESH API", version=__version__, lifespan=lifespan,
                  description="Skaner discovery dużej infrastruktury. Etap 1: cele, skany nmap, harmonogram.")
    app.state.engine, app.state.scheduler, app.state.cipher, app.state.db_path = engine, scheduler, cipher, db_path

    def conn():
        return store.connect(db_path)

    @app.exception_handler(ValueError)
    async def bad_input(_request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def conflict(_request: Request, error: RuntimeError):
        return JSONResponse({"detail": str(error)}, status_code=409)

    # ---- health ------------------------------------------------------------------
    @app.get("/api/health", tags=["system"])
    def health():
        from .scanner import nmap_binary
        try:
            nmap = nmap_binary()
        except RuntimeError:
            nmap = None
        return {"ok": True, "version": __version__, "nmap": nmap, "active_run": engine.active_run_id()}

    # ---- targets -----------------------------------------------------------------
    def _save_targets(targets, source: str, extra_note: str | None = None) -> dict:
        db = conn()
        try:
            added, duplicates = [], 0
            db.execute("BEGIN IMMEDIATE")
            for t in targets:
                note = t.note or extra_note
                cur = db.execute("INSERT OR IGNORE INTO targets (kind, value, size, note, source, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 (t.kind, t.value, t.size, cipher.seal(note, "targets.note"), source, store.now()))
                if cur.rowcount:
                    added.append({"kind": t.kind, "value": t.value, "size": t.size})
                else:
                    duplicates += 1
            db.execute("COMMIT")
            return {"added": added, "duplicates": duplicates, "addresses": sum(a["size"] for a in added)}
        finally:
            db.close()

    @app.post("/api/targets", tags=["cele"], summary="Dodaj cele z formularza (tekst)")
    def add_targets(body: TargetsIn):
        return _save_targets(parse_text(body.text), "manual", body.note)

    @app.post("/api/targets/import", tags=["cele"], summary="Importuj cele z pliku CSV lub JSON")
    def import_targets(body: ImportIn):
        source = "json" if (body.filename or "").lower().endswith(".json") or body.content.lstrip()[:1] in "[{" else "csv"
        return _save_targets(parse_any(body.content, body.filename), source)

    @app.get("/api/targets", tags=["cele"])
    def list_targets():
        db = conn()
        try:
            rows = [_target_row(r, cipher) for r in db.execute("SELECT * FROM targets ORDER BY id")]
            return {"targets": rows, "addresses": sum(r["size"] for r in rows if r["enabled"])}
        finally:
            db.close()

    @app.patch("/api/targets/{target_id}", tags=["cele"])
    def edit_target(target_id: int, body: TargetPatch):
        db = conn()
        try:
            if body.enabled is not None:
                db.execute("UPDATE targets SET enabled=? WHERE id=?", (int(body.enabled), target_id))
            if body.note is not None:
                db.execute("UPDATE targets SET note=? WHERE id=?", (cipher.seal(body.note, "targets.note"), target_id))
            row = db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Nie ma takiego celu.")
            return _target_row(row, cipher)
        finally:
            db.close()

    @app.delete("/api/targets/{target_id}", tags=["cele"])
    def delete_target(target_id: int):
        db = conn()
        try:
            if not db.execute("DELETE FROM targets WHERE id=?", (target_id,)).rowcount:
                raise HTTPException(404, "Nie ma takiego celu.")
            return {"deleted": target_id}
        finally:
            db.close()

    # ---- scans -------------------------------------------------------------------
    @app.post("/api/scans", tags=["skany"], summary="Uruchom skan teraz")
    def start_scan():
        if engine.active_run_id() is not None:
            raise RuntimeError(f"Trwa już skan nr {engine.active_run_id()}.")
        run_id = engine.create_run("manual")
        engine.start(run_id)
        return scan_detail(run_id)

    @app.get("/api/scans", tags=["skany"])
    def list_scans(limit: int = 50):
        db = conn()
        try:
            return {"scans": [_run_row(r) for r in db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,))]}
        finally:
            db.close()

    @app.get("/api/scans/estimate", tags=["skany"], summary="Szacowany czas następnego skanu")
    def estimate():
        db = conn()
        try:
            s = store.settings(db)
            addresses = sum(r[0] for r in db.execute("SELECT size FROM targets WHERE enabled=1"))
            rate = s.get("measured_rate")
            return {"addresses": addresses, "hosts_per_second_per_process": rate, "threads": s["threads"],
                    "chunk_size": engine.chunk_size(s, rate) if rate else s["calibration_chunk"],
                    "estimate_seconds": engine.estimate(addresses, s, rate) if rate and addresses else None}
        finally:
            db.close()

    @app.get("/api/scans/{run_id}", tags=["skany"], summary="Stan skanu, podgrupy i ich czasy")
    def scan_detail(run_id: int):
        db = conn()
        try:
            run = db.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise HTTPException(404, "Nie ma takiego skanu.")
            chunk_rows = db.execute("SELECT id, idx, size, status, started_at, finished_at, duration_seconds, hosts_up, "
                                    "attempts, error FROM scan_chunks WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
            done = sum(c["size"] for c in chunk_rows if c["status"] == "done")
            out = _run_row(run)
            out["progress"] = round(100 * done / run["total_hosts"], 1) if run["total_hosts"] else 100.0
            out["chunks"] = [dict(c) for c in chunk_rows]
            return out
        finally:
            db.close()

    @app.post("/api/scans/{run_id}/pause", tags=["skany"])
    def pause_scan(run_id: int):
        engine.pause(run_id)
        engine.wait(run_id, 15)
        return scan_detail(run_id)

    @app.post("/api/scans/{run_id}/resume", tags=["skany"])
    def resume_scan(run_id: int):
        engine.start(run_id)
        return scan_detail(run_id)

    # ---- hosts -------------------------------------------------------------------
    @app.get("/api/hosts", tags=["hosty"], summary="Hosty z ostatniego zakończonego skanu")
    def list_hosts(run_id: int | None = None):
        db = conn()
        try:
            if run_id is None:
                last = db.execute("SELECT id FROM scan_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
                run_id = last["id"] if last else None
            if run_id is None:
                return {"run_id": None, "hosts": []}
            hosts = []
            for h in db.execute("SELECT ip, hostname FROM host_results WHERE run_id=? ORDER BY ip", (run_id,)).fetchall():
                services = [_service(s) for s in db.execute(
                    "SELECT * FROM services WHERE run_id=? AND ip=? ORDER BY port", (run_id, h["ip"]))]
                hosts.append({"ip": h["ip"], "hostname": h["hostname"], "services": services})
            return {"run_id": run_id, "hosts": hosts}
        finally:
            db.close()

    def _service(row) -> dict:
        out = {k: row[k] for k in ("port", "proto", "state", "name")}
        for column in ("product", "version", "extrainfo"):
            out[column] = cipher.open(row[column], f"services.{column}")
        return out

    # ---- settings ----------------------------------------------------------------
    @app.get("/api/settings", tags=["ustawienia"])
    def get_settings():
        db = conn()
        try:
            return store.settings(db)
        finally:
            db.close()

    @app.patch("/api/settings", tags=["ustawienia"])
    def patch_settings(body: SettingsPatch):
        db = conn()
        try:
            for key, value in body.model_dump(exclude_none=True).items():
                store.save_setting(db, key, value)
            return store.settings(db)
        finally:
            db.close()

    # ---- schedules ---------------------------------------------------------------
    @app.get("/api/schedules/templates", tags=["harmonogram"])
    def templates():
        return TEMPLATES

    @app.get("/api/schedules", tags=["harmonogram"])
    def list_schedules():
        db = conn()
        try:
            return {"schedules": [dict(r, enabled=bool(r["enabled"])) for r in db.execute("SELECT * FROM schedules ORDER BY id")]}
        finally:
            db.close()

    @app.post("/api/schedules", tags=["harmonogram"])
    def add_schedule(body: ScheduleIn):
        values = dict(TEMPLATES[body.template]) if body.template else {}
        values.update(body.model_dump(exclude_none=True, exclude={"template", "enabled"}))
        missing = [k for k in ("name", "frequency", "window_start", "window_end") if k not in values]
        if missing:
            raise ValueError("Podaj szablon albo pola: " + ", ".join(missing))
        validate(values["frequency"], values["window_start"], values["window_end"])
        db = conn()
        try:
            sched_id = db.execute("INSERT INTO schedules (name, frequency, window_start, window_end, enabled, created_at) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (values["name"], values["frequency"], values["window_start"],
                                                                values["window_end"], int(body.enabled), store.now())).lastrowid
            return dict(db.execute("SELECT * FROM schedules WHERE id=?", (sched_id,)).fetchone())
        finally:
            db.close()

    @app.delete("/api/schedules/{sched_id}", tags=["harmonogram"])
    def delete_schedule(sched_id: int):
        db = conn()
        try:
            if not db.execute("DELETE FROM schedules WHERE id=?", (sched_id,)).rowcount:
                raise HTTPException(404, "Nie ma takiego harmonogramu.")
            return {"deleted": sched_id}
        finally:
            db.close()

    return app


def app_from_env() -> FastAPI:
    return create_app()
