"""HTTP API and web panel. Interactive documentation: /docs (Swagger) and /redoc, schema: /openapi.json.

Every API route needs a login token (see auth.py for roles). The panel is served at /.
"""
import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import auth
from . import bgp
from . import db as store
from . import dojo
from .crypto import FieldCipher
from .engine import Engine
from .scheduler import TEMPLATES, Scheduler, validate
from .api_admin import register as register_admin
from .api_results import register as register_results
from .targets import classify, parse_any, parse_text


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
    http_rate: float | None = Field(None, gt=0, le=1000, description="Zapytania HTTP na sekundę przy rozpoznawaniu.")
    http_timeout: float | None = Field(None, gt=0, le=60)
    unstable_after: int | None = Field(None, ge=1, le=20, description="Po ilu skanach bez odpowiedzi host jest nieaktywny.")
    dojo_url: str | None = Field(None, pattern=r"^https?://", description="Adres DefectDojo, np. http://dojo-nginx:8080")
    dojo_user: str | None = None
    dojo_password: str | None = Field(None, description="Przechowywane zaszyfrowane.")
    dojo_token: str | None = Field(None, description="Token API DefectDojo (przechowywany zaszyfrowany).")
    dojo_product: str | None = None
    dojo_auto: bool | None = Field(None, description="Po każdym skanie wysyłaj nowe ważne znaleziska.")
    dojo_min_priority: int | None = Field(None, ge=1, le=5)
    bgp_asn: int | None = Field(None, ge=1, le=4294967295, description="ASN do radaru zakresów, np. 8501.")


class BgpImportIn(BaseModel):
    asn: str | None = Field(None, description="ASN, np. 8501 albo AS8501. Puste = z ustawień.", examples=["AS8501"])
    prefixes: list[str] | None = Field(None, description="Wybrane prefiksy. Puste = wszystkie brakujące.")


class DojoSendIn(BaseModel):
    finding_ids: list[int] | None = Field(None, description="Wybrane znaleziska. Puste = według min_priority.")
    min_priority: int | None = Field(None, ge=1, le=5)
    only_unsent: bool = Field(False, description="Tylko te, których jeszcze nie wysłano.")


SECRET_SETTINGS = ("dojo_password", "dojo_token")


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
               background: bool = True, run_chunk=None, probe=None) -> FastAPI:
    data = Path(data_dir or os.environ.get("AEGIS_DATA", "./data"))
    data.mkdir(parents=True, exist_ok=True)
    db_path = str(data / "aegis.sqlite3")
    key = Path(key_path or os.environ.get("AEGIS_KEY_FILE", data.parent / "keys" / "aegis.key"))
    store.initialize(db_path)
    cipher = FieldCipher.load(key)
    _dojo_from_env(db_path, cipher)
    first = store.connect(db_path)
    try:
        auth.ensure_first_admin(first, data)
    finally:
        first.close()
    cli_token = auth.terminal_token(data)
    engine = Engine(db_path, cipher, probe=probe, after_run=dojo.after_run(db_path, cipher),
                    **({"run_chunk": run_chunk} if run_chunk else {}))
    scheduler = Scheduler(db_path, engine)

    @asynccontextmanager
    async def lifespan(_app):
        if background:
            scheduler.start()
        yield
        scheduler.stop()

    def conn():
        return store.connect(db_path)

    app = FastAPI(title="AEGIS MESH API", version=__version__, lifespan=lifespan,
                  dependencies=[Depends(auth.dependency(conn, cli_token))],
                  description="Skaner discovery dużej infrastruktury: cele, skany nmap z harmonogramem, "
                              "rozpoznawanie HTTP/HTTPS i technologii, sygnatury, porównanie skanów, "
                              "hosty niestabilne, znaleziska z ochroną ręcznych edycji, eksport do DefectDojo.")
    app.state.engine, app.state.scheduler, app.state.cipher, app.state.db_path = engine, scheduler, cipher, db_path
    app.state.cli_token = cli_token

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(("/docs", "/redoc")):
            response.headers.setdefault("Content-Security-Policy",
                                        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ValueError)
    async def bad_input(_request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(dojo.DojoError)
    async def dojo_failed(_request: Request, error: dojo.DojoError):
        return JSONResponse({"detail": str(error)}, status_code=502)

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
        # Public (no login), also through Tailscale Funnel: says only that this is AEGIS MESH and whether it can scan.
        return {"ok": True, "service": "aegis-mesh", "version": __version__, "nmap": nmap is not None}

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

    # ---- scope radar (BGP) ---------------------------------------------------------
    app.state.bgp_download = bgp._download            # tests replace it with a fake RIPEstat

    def _bgp_scope(asn_text: str | None) -> dict:
        db = conn()
        try:
            s = store.settings(db)
            if not asn_text and not s.get("bgp_asn"):
                raise ValueError("Podaj numer ASN, np. 8501, albo zapisz go w ustawieniach (bgp_asn).")
            asn = bgp.parse_asn(asn_text or s["bgp_asn"])
            last = s.get("bgp_last") or {}
            stale, error = False, None
            try:
                prefixes = bgp.fetch_prefixes(asn, download=app.state.bgp_download)
                fetched_at = store.now()
                store.save_setting(db, "bgp_last", {"asn": asn, "prefixes": prefixes, "fetched_at": fetched_at})
            except bgp.BgpUnavailable as failure:
                if last.get("asn") != asn:
                    raise HTTPException(503, f"{failure} Sprawdź internet i spróbuj ponownie.") from None
                prefixes, fetched_at, stale, error = last["prefixes"], last["fetched_at"], True, str(failure)
            rows = [dict(r) for r in db.execute("SELECT kind, value, enabled FROM targets ORDER BY id")]
            result = bgp.compare(asn, prefixes, rows)
            result.update(source="RIPEstat announced-prefixes", fetched_at=fetched_at, stale=stale, error=error)
            return result
        finally:
            db.close()

    @app.get("/api/scope/bgp", tags=["cele"],
             summary="Radar zakresów: prefiksy ogłaszane w BGP przez ASN kontra lista celów")
    def scope_bgp(asn: str | None = None):
        return _bgp_scope(asn)

    @app.post("/api/scope/bgp/import", tags=["cele"],
              summary="Dodaj brakujące prefiksy ASN jako cele (wszystkie albo wybrane)")
    def scope_bgp_import(body: BgpImportIn):
        scope = _bgp_scope(body.asn)
        announced = {p["prefix"]: p for p in scope["prefixes"]}
        if body.prefixes is None:
            chosen = scope["to_import"]
        else:
            chosen = []
            for raw in body.prefixes:
                prefix = str(ipaddress.ip_network(raw.strip(), strict=False))
                if prefix not in announced:
                    raise ValueError(f"AS{scope['asn']} nie ogłasza prefiksu {raw}.")
                if announced[prefix]["blocked"]:
                    raise ValueError(f"Prefiks {prefix} jest {announced[prefix]['blocked']}.")
                chosen.append(prefix)
        result = _save_targets([classify(p) for p in chosen], "bgp", f"AS{scope['asn']} (BGP)")
        result["asn"], result["stale"] = scope["asn"], scope["stale"]
        return result

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
            chunk_rows = db.execute("SELECT id, idx, kind, size, status, started_at, finished_at, duration_seconds, "
                                    "hosts_up, attempts, error FROM scan_chunks WHERE run_id=? ORDER BY idx",
                                    (run_id,)).fetchall()
            done = sum(c["size"] for c in chunk_rows if c["status"] == "done" and c["kind"] == "scan")
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
    def _public_settings(db) -> dict:
        values = store.settings(db)
        values.pop("bgp_last", None)                                   # cache, not a setting
        for key in SECRET_SETTINGS:
            values[key] = "********" if values.get(key) else None      # secrets are never returned
        return values

    @app.get("/api/settings", tags=["ustawienia"])
    def get_settings():
        db = conn()
        try:
            return _public_settings(db)
        finally:
            db.close()

    @app.patch("/api/settings", tags=["ustawienia"])
    def patch_settings(body: SettingsPatch, request: Request):
        changes = body.model_dump(exclude_none=True)
        if any(k.startswith("dojo_") for k in changes) and not auth.can(request.state.user, "admin"):
            raise HTTPException(403, "Ustawienia DefectDojo może zmieniać tylko admin.")
        db = conn()
        try:
            for key, value in changes.items():
                if key in SECRET_SETTINGS:
                    value = cipher.seal(value, f"settings.{key}") if value else None
                store.save_setting(db, key, value)
            return _public_settings(db)
        finally:
            db.close()

    # ---- DefectDojo ---------------------------------------------------------------
    @app.post("/api/dojo/check", tags=["defectdojo"], summary="Sprawdź połączenie z DefectDojo")
    def dojo_check():
        db = conn()
        try:
            return dojo.Dojo(store.settings(db), cipher).check()
        finally:
            db.close()

    @app.post("/api/dojo/send", tags=["defectdojo"],
              summary="Wyślij wybrane znaleziska do DefectDojo (finding informacyjny z dowodami)")
    def dojo_send(body: DojoSendIn):
        db = conn()
        try:
            return dojo.send(db, cipher, body.finding_ids, body.min_priority, body.only_unsent)
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

    register_results(app, conn, cipher, engine)
    register_admin(app, conn, cipher)

    panel = Path(__file__).with_name("panel")
    app.mount("/static", StaticFiles(directory=panel / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(panel / "index.html")

    return app


def _dojo_from_env(db_path: str, cipher: FieldCipher) -> None:
    """AEGIS_DOJO_URL/USER/PASSWORD/TOKEN fill the DefectDojo settings once, if they are still empty."""
    url = os.environ.get("AEGIS_DOJO_URL")
    db = store.connect(db_path)
    try:
        if not url or store.settings(db).get("dojo_url"):
            return
        store.save_setting(db, "dojo_url", url)
        store.save_setting(db, "dojo_user", os.environ.get("AEGIS_DOJO_USER"))
        for key, env in (("dojo_password", "AEGIS_DOJO_PASSWORD"), ("dojo_token", "AEGIS_DOJO_TOKEN")):
            if os.environ.get(env):
                store.save_setting(db, key, cipher.seal(os.environ[env], f"settings.{key}"))
    finally:
        db.close()


def app_from_env() -> FastAPI:
    return create_app()
