"""What changed after a scan: new, missing and changed hosts and services, unstable hosts, time of day.

Runs once when a scan is done. Each host is compared with the last scan in which it
answered, so a host that was outside the previous scan's targets is not reported as new.
A host that stops answering is "unstable" first and becomes "inactive" only after
`unstable_after` (default 3) scans in a row without an answer.
"""
import json
from . import db as store

DAYPARTS = (("noc", 0, 6), ("rano", 6, 12), ("popołudnie", 12, 18), ("wieczór", 18, 24))
COMPARED = ("name", "product", "version", "tunnel")


def daypart(hour: int) -> str:
    return next(name for name, start, end in DAYPARTS if start <= hour < end)


def run_addresses(db, run_id: int) -> set[str]:
    return {a for r in db.execute("SELECT targets FROM scan_chunks WHERE run_id=? AND kind='scan'", (run_id,))
            for a in json.loads(r["targets"])}


def missing_known(db, run_id: int) -> list[str]:
    """Hosts that answered before, are in this scan's targets and did not answer now: candidates for a re-check."""
    scope = run_addresses(db, run_id)
    found = {r["ip"] for r in db.execute("SELECT ip FROM host_results WHERE run_id=?", (run_id,))}
    known = [r["ip"] for r in db.execute("SELECT ip FROM hosts WHERE state IN ('active','unstable') ORDER BY ip")]
    return [ip for ip in known if ip in scope and ip not in found]


def _services(db, cipher, run_id: int, ip: str) -> dict:
    out = {}
    for r in db.execute("SELECT * FROM services WHERE run_id=? AND ip=?", (run_id, ip)):
        values = {k: r[k] for k in ("name", "tunnel")}
        for k in ("product", "version"):
            values[k] = cipher.open(r[k], f"services.{k}")
        web = db.execute("SELECT status, title FROM web WHERE run_id=? AND ip=? AND port=? AND proto=?",
                         (run_id, ip, r["port"], r["proto"])).fetchone()
        values["http_status"], values["http_title"] = \
            (web["status"], cipher.open(web["title"], "web.title")) if web else (None, None)
        out[(r["port"], r["proto"])] = values
    return out


def _finding(db, cipher, ip: str, port: int, proto: str) -> dict | None:
    row = db.execute("SELECT id, priority, title FROM findings WHERE ip=? AND port=? AND proto=?",
                     (ip, port, proto)).fetchone()
    return {"id": row["id"], "priority": row["priority"], "title": cipher.open(row["title"], "findings.title")} if row else None


def _label(service: dict) -> str:
    text = " ".join(str(service.get(k)) for k in ("product", "version") if service.get(k))
    return text or service.get("name") or "usługa"


def compute(db, cipher, run_id: int, local_hour: int, unstable_after: int = 3) -> list[dict]:
    changes: list[dict] = []

    def add(kind, ip, priority, message, port=None, proto=None, detail=None):
        changes.append({"kind": kind, "ip": ip, "port": port, "proto": proto, "priority": priority,
                        "message": message, "detail": detail})

    scope = run_addresses(db, run_id)
    found = {r["ip"] for r in db.execute("SELECT ip FROM host_results WHERE run_id=?", (run_id,))}
    hosts = {r["ip"]: r for r in db.execute("SELECT * FROM hosts")}

    for ip in sorted(found):
        host = hosts[ip]
        previous = db.execute("SELECT MAX(h.run_id) FROM host_results h JOIN scan_runs r ON r.id=h.run_id "
                              "WHERE h.ip=? AND h.run_id<? AND r.status='done'", (ip, run_id)).fetchone()[0]
        now = _services(db, cipher, run_id, ip)
        if previous is None:
            top = max(((_finding(db, cipher, ip, *key) or {"priority": 2})["priority"] for key in now), default=2)
            ports = ", ".join(f"{p}/{pr} {_label(s)}" for (p, pr), s in sorted(now.items())) or "bez otwartych portów"
            add("new_host", ip, max(2, top), f"Nowy host {ip}: {ports}.", detail={"services": len(now)})
        elif host["state"] != "active":
            add("host_back", ip, 2, f"Host {ip} znowu odpowiada (wcześniej: {'nieaktywny' if host['state'] == 'inactive' else 'niestabilny'}).")
        if previous is not None:
            before = _services(db, cipher, previous, ip)
            for key in sorted(now.keys() - before.keys()):
                f = _finding(db, cipher, ip, *key)
                add("new_service", ip, f["priority"] if f else 2, f"Nowa usługa {ip}:{key[0]}/{key[1]} {_label(now[key])}. "
                    + (f["title"] if f and f["priority"] >= 2 else ""), *key)
            for key in sorted(before.keys() - now.keys()):
                add("gone_service", ip, 2, f"Usługa {ip}:{key[0]}/{key[1]} {_label(before[key])} zniknęła.", *key)
                db.execute("UPDATE findings SET state='gone' WHERE ip=? AND port=? AND proto=?", (ip, *key))
            for key in sorted(now.keys() & before.keys()):
                diff = {k: [before[key].get(k), now[key].get(k)] for k in (*COMPARED, "http_status", "http_title")
                        if before[key].get(k) != now[key].get(k)}
                if diff:
                    f = _finding(db, cipher, ip, *key)
                    what = ", ".join(f"{k}: {a} → {b}" for k, (a, b) in diff.items())
                    add("changed_service", ip, max(2, f["priority"] if f else 2),
                        f"Zmiana {ip}:{key[0]}/{key[1]}: {what}.", *key, detail=diff)
        db.execute("UPDATE hosts SET state='active', missed=0 WHERE ip=?", (ip,))

    for ip, host in hosts.items():
        if ip in found or ip not in scope or host["state"] == "inactive":
            continue
        missed = host["missed"] + 1
        if missed >= unstable_after:
            db.execute("UPDATE hosts SET state='inactive', missed=? WHERE ip=?", (missed, ip))
            db.execute("UPDATE findings SET state='gone' WHERE ip=?", (ip,))
            add("gone_host", ip, 3, f"Host {ip} nieaktywny: {missed} skany z rzędu bez odpowiedzi.")
        else:
            db.execute("UPDATE hosts SET state='unstable', missed=? WHERE ip=?", (missed, ip))
            add("unstable_host", ip, 2, f"Host {ip} nie odpowiedział mimo ponownego sprawdzenia ({missed}/{unstable_after}).")

    # Presence by time of day: every host that was in scope and is known.
    for ip in found | {ip for ip, h in hosts.items() if ip in scope}:
        db.execute("INSERT OR REPLACE INTO host_presence VALUES (?, ?, ?, ?)", (run_id, ip, int(ip in found), local_hour))
    for ip in sorted({ip for ip in hosts if ip in scope} | found):
        summary = presence_summary(db, ip)
        pattern = summary["pattern"]
        old = json.loads(hosts[ip]["daypart"]).get("pattern") if ip in hosts and hosts[ip]["daypart"] else None
        db.execute("UPDATE hosts SET daypart=? WHERE ip=?", (json.dumps(summary), ip))
        if pattern and pattern != old:
            add("daypart", ip, 3, f"Host {ip} {pattern}.", detail=summary["parts"])

    for change in changes:
        db.execute("INSERT INTO changes (run_id, kind, ip, port, proto, priority, message, detail, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (run_id, change["kind"], change["ip"], change["port"], change["proto"], change["priority"],
                    cipher.seal(change["message"].strip(), "changes.message"),
                    cipher.seal(json.dumps(change["detail"]), "changes.detail") if change["detail"] else None,
                    store.now()))
    return changes


def presence_summary(db, ip: str) -> dict:
    """Answer rate per part of day, and a sentence when the host answers only at some times of day."""
    parts = {name: {"up": 0, "total": 0} for name, _, _ in DAYPARTS}
    for r in db.execute("SELECT up, hour FROM host_presence WHERE ip=?", (ip,)):
        part = parts[daypart(r["hour"])]
        part["total"] += 1
        part["up"] += r["up"]
    observed = {k: v for k, v in parts.items() if v["total"]}
    always = [k for k, v in observed.items() if v["up"] == v["total"]]
    never = [k for k, v in observed.items() if v["up"] == 0]
    pattern = None
    if sum(v["total"] for v in observed.values()) >= 3 and always and never:
        pattern = f"odpowiada tylko w porze: {', '.join(always)} (brak odpowiedzi: {', '.join(never)})"
    return {"parts": parts, "pattern": pattern}

