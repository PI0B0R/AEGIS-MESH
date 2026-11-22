"""Terminal commands. Everything except `serve` talks to the running server over its API,
so the terminal, the panel and the jury's scripts all go through the same code path.

    aegis serve                       start the server (API + scheduler)
    aegis targets add 10.0.0.0/24 example.com
    aegis targets import cele.csv
    aegis targets list
    aegis scan start [--wait]
    aegis scan list | show ID | pause ID | resume ID
    aegis hosts [--run ID]
    aegis schedule templates | add nocny | list | delete ID
    aegis settings [rate=500 threads=8 ports=22,80,443]

    aegis summary                     dashboard tiles
    aegis changes [--run ID]          what changed after the last scan, most important first
    aegis findings list [--min-priority 4] [--tag staging] [--search wordpress]
    aegis findings show ID | edit ID --priority 5 --tags a,b --note "..." | reset ID
    aegis host IP                     host card: services, headers, cookies, certificate, history
    aegis unstable                    unstable and inactive hosts
    aegis signatures list | test ... | add ... | delete ID
    aegis dojo check | send [--ids 1,2] [--min-priority 4] [--unsent]
    aegis export [--format csv|full] [-o plik]
    aegis import findings plik.csv | full eksport.json
    aegis users list | add LOGIN --role edytor | role ID ROLA | password ID | disable ID | enable ID | unlock ID | delete ID
    aegis audit                       who changed what

Inside the server container the commands use the terminal token automatically.
From another machine: AEGIS_URL=http://serwer:8000 AEGIS_TOKEN=... aegis ...
(token from POST /api/auth/login).
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def token() -> str | None:
    """AEGIS_TOKEN, or the terminal token the server writes next to its database (inside the container)."""
    if os.environ.get("AEGIS_TOKEN"):
        return os.environ["AEGIS_TOKEN"]
    path = Path(os.environ.get("AEGIS_DATA", "./data")) / "cli.token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def headers() -> dict:
    out = {"Content-Type": "application/json"}
    if token():
        out["Authorization"] = f"Bearer {token()}"
    return out


def call(method: str, path: str, body=None):
    base = os.environ.get("AEGIS_URL", "http://127.0.0.1:8000").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read() or b"{}").get("detail", error.reason)
        sys.exit(f"Błąd {error.code}: {detail}")
    except urllib.error.URLError as error:
        sys.exit(f"Brak połączenia z serwerem {base} ({error.reason}). Czy działa `aegis serve`?")


def show(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def scan_line(run: dict) -> str:
    took = f"{run['active_seconds']:.1f} s" if run.get("active_seconds") else "-"
    est = f"{run['estimate_seconds']:.0f} s" if run.get("estimate_seconds") else "?"
    return (f"#{run['id']:<4} {run['status']:<8} adresy {run['total_hosts']:<6} aktywne {run['hosts_up']:<5} "
            f"czas {took:<9} szacunek {est:<7} {run.get('progress', '')}{'%' if 'progress' in run else ''}")


def setting_value(text: str):
    try:
        return json.loads(text)          # numbers, true/false, null
    except ValueError:
        return text


def finding_line(f: dict) -> str:
    tech = ", ".join(f"{t['name']} {t['version'] or ''}".strip() for t in f["technologies"][:3])
    mark = " [edytowane]" if f.get("is_edited") else ""
    state = "" if f["state"] == "active" else f" ({f['state']})"
    return (f"{f['id']:>4}  P{f['priority']}  {f['ip'] + ':' + str(f['port']):<22} {f['title']}{mark}{state}\n"
            f"{'':>10}{tech}{'  #' + ' #'.join(f['tags']) if f['tags'] else ''}")


def signature_args(p) -> None:
    p.add_argument("--name", required=True)
    p.add_argument("--kind", required=True, help="header, cookie, html, title, favicon, path, service, tls")
    p.add_argument("--pattern", default="", help="wyrażenie regularne; grupa w nawiasach = wersja")
    p.add_argument("--field", help="nazwa nagłówka albo ścieżka, np. /admin")
    p.add_argument("--category", default="inne")
    p.add_argument("--priority", type=int, default=2)
    p.add_argument("--tags", default="", help="po przecinku")
    p.add_argument("--message", default="")


def signature_body(args) -> dict:
    return {"name": args.name, "kind": args.kind, "pattern": args.pattern, "field": args.field,
            "category": args.category, "priority": args.priority, "message": args.message,
            "tags": [t for t in args.tags.split(",") if t.strip()]}


def summary_text(d: dict) -> str:
    from datetime import datetime
    from .scheduler import zone

    def clock(iso):
        return datetime.fromisoformat(iso).astimezone(zone()).strftime("%Y-%m-%d %H:%M") if iso else "-"
    lines = [f"Aktywne hosty:        {d['active_hosts']}  (niestabilne {d['unstable_hosts']}, nieaktywne {d['inactive_hosts']})",
             f"Zmiany po skanie:     {d['changes_since_last_scan']}",
             f"Wysoki priorytet 4-5: {d['high_priority']}"]
    last = d.get("last_scan")
    if last:
        lines.append(f"Ostatni skan:         #{last['id']} {last['status']}, {clock(last.get('finished_at'))}, "
                     f"{last['hosts_up']}/{last['total_hosts']} adresów odpowiada")
    if d.get("active_run"):
        lines.append(f"Trwa skan:            #{d['active_run']}")
    if d.get("paused_run"):
        lines.append(f"Wstrzymany skan:      #{d['paused_run']}")
    nxt = d.get("next_scan")
    lines.append(f"Następny skan:        {clock(nxt['at'])} ({nxt['name']})" if nxt else "Następny skan:        brak harmonogramu")
    return "\n".join(lines)


def schedule_line(s: dict) -> str:
    window = "bez okna" if s["window_start"] == s["window_end"] else f"okno {s['window_start']}-{s['window_end']}"
    state = "" if s.get("enabled", True) else "  (wyłączony)"
    return f"#{s['id']:<4} {s['name']}  [{s['frequency']}, {window}]{state}"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="aegis", description="AEGIS MESH: skaner discovery")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="uruchom serwer")
    serve.add_argument("--host", default=os.environ.get("AEGIS_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("AEGIS_PORT", "8000")))

    targets = sub.add_parser("targets", help="cele skanowania").add_subparsers(dest="action", required=True)
    add = targets.add_parser("add", help="dodaj IP, CIDR, zakresy, domeny")
    add.add_argument("items", nargs="+")
    add.add_argument("--note")
    imp = targets.add_parser("import", help="importuj plik CSV lub JSON")
    imp.add_argument("file", help="ścieżka albo - (standardowe wejście)")
    imp.add_argument("--name", help="nazwa pliku przy imporcie z wejścia, np. cele.csv")
    targets.add_parser("list")
    rm = targets.add_parser("delete")
    rm.add_argument("id", type=int)

    scan = sub.add_parser("scan", help="skany").add_subparsers(dest="action", required=True)
    start = scan.add_parser("start", help="uruchom skan teraz")
    start.add_argument("--wait", action="store_true", help="czekaj na koniec i pokaż wynik")
    scan.add_parser("list")
    scan.add_parser("estimate", help="szacowany czas następnego skanu")
    for name in ("show", "pause", "resume"):
        scan.add_parser(name).add_argument("id", type=int)

    hosts = sub.add_parser("hosts", help="hosty i usługi z ostatniego skanu")
    hosts.add_argument("--run", type=int)

    sched = sub.add_parser("schedule", help="harmonogram").add_subparsers(dest="action", required=True)
    sched.add_parser("templates")
    sched.add_parser("list")
    sadd = sched.add_parser("add", help="dodaj z szablonu albo własny")
    sadd.add_argument("template", nargs="?")
    sadd.add_argument("--name")
    sadd.add_argument("--frequency")
    sadd.add_argument("--window", help="np. 22:00-06:00")
    sched.add_parser("delete").add_argument("id", type=int)

    sett = sub.add_parser("settings", help="pokaż lub zmień ustawienia skanu")
    sett.add_argument("pairs", nargs="*", help="klucz=wartość")

    sub.add_parser("summary", help="kafelki pulpitu").add_argument("--json", action="store_true")
    ch = sub.add_parser("changes", help="co się zmieniło po skanie")
    ch.add_argument("--run", type=int)
    ch.add_argument("--min-priority", type=int, default=1)

    fnd = sub.add_parser("findings", help="znaleziska").add_subparsers(dest="action", required=True)
    fl = fnd.add_parser("list")
    fl.add_argument("--min-priority", type=int, default=1)
    fl.add_argument("--tag")
    fl.add_argument("--search")
    fl.add_argument("--ip")
    fl.add_argument("--all", action="store_true", help="także usługi, które zniknęły")
    fnd.add_parser("show").add_argument("id", type=int)
    fe = fnd.add_parser("edit", help="ręczna edycja (skan jej nie nadpisze)")
    fe.add_argument("id", type=int)
    fe.add_argument("--priority", type=int)
    fe.add_argument("--tags", help="po przecinku")
    fe.add_argument("--title")
    fe.add_argument("--note")
    fe.add_argument("--status")
    fr = fnd.add_parser("reset", help="oddaj pola skanom")
    fr.add_argument("id", type=int)
    fr.add_argument("--fields", help="tags,priority,title")

    sub.add_parser("host", help="karta hosta").add_argument("ip")
    sub.add_parser("unstable", help="hosty niestabilne i nieaktywne")

    sg = sub.add_parser("signatures", help="sygnatury").add_subparsers(dest="action", required=True)
    sg.add_parser("list")
    signature_args(sg.add_parser("test", help="sprawdź na istniejących hostach"))
    signature_args(sg.add_parser("add", help="dodaj i zastosuj"))
    sg.add_parser("delete").add_argument("id")

    dj = sub.add_parser("dojo", help="DefectDojo").add_subparsers(dest="action", required=True)
    dj.add_parser("check")
    ds = dj.add_parser("send")
    ds.add_argument("--ids", help="np. 3,7,12")
    ds.add_argument("--min-priority", type=int)
    ds.add_argument("--unsent", action="store_true")

    ex = sub.add_parser("export", help="eksport znalezisk")
    ex.add_argument("--format", choices=("json", "csv", "full"), default="json")
    ex.add_argument("-o", "--output")

    im = sub.add_parser("import", help="import edycji znalezisk albo pełny").add_subparsers(dest="action", required=True)
    im.add_parser("findings", help="CSV/JSON z eksportu znalezisk").add_argument("file", help="ścieżka albo -")
    im.add_parser("full", help="plik z export --format full").add_argument("file", help="ścieżka albo -")

    us = sub.add_parser("users", help="użytkownicy i role").add_subparsers(dest="action", required=True)
    us.add_parser("list")
    ua = us.add_parser("add")
    ua.add_argument("username")
    ua.add_argument("--role", required=True, choices=("czytelnik", "analityk", "edytor", "admin"))
    ua.add_argument("--password", help="bez tej opcji: zapytanie w terminalu")
    ur = us.add_parser("role")
    ur.add_argument("id", type=int)
    ur.add_argument("role", choices=("czytelnik", "analityk", "edytor", "admin"))
    for name in ("password", "disable", "enable", "unlock", "delete"):
        us.add_parser(name).add_argument("id", type=int)
    sub.add_parser("audit", help="dziennik zmian")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("aegis.api:app_from_env", factory=True, host=args.host, port=args.port, log_level="info")
    elif args.command == "targets":
        if args.action == "add":
            result = call("POST", "/api/targets", {"text": " ".join(args.items), "note": args.note})
            print(f"Dodano {len(result['added'])} celów ({result['addresses']} adresów), duplikaty: {result['duplicates']}.")
        elif args.action == "import":
            path = Path(args.file)
            if args.file == "-":            # from stdin: docker compose exec -T aegis aegis targets import - < cele.csv
                content, filename = sys.stdin.read(), args.name
            else:
                content, filename = path.read_text(encoding="utf-8-sig"), path.name
            result = call("POST", "/api/targets/import", {"content": content, "filename": filename})
            print(f"Zaimportowano {len(result['added'])} celów ({result['addresses']} adresów), duplikaty: {result['duplicates']}.")
        elif args.action == "list":
            data = call("GET", "/api/targets")
            for t in data["targets"]:
                print(f"{t['id']:>4}  {t['kind']:<6} {t['value']:<32} {t['size']:>8}  {'' if t['enabled'] else '(wyłączony) '}{t['note'] or ''}")
            print(f"Razem adresów do skanowania: {data['addresses']}")
        else:
            show(call("DELETE", f"/api/targets/{args.id}"))
    elif args.command == "scan":
        if args.action == "start":
            run = call("POST", "/api/scans")
            print(scan_line(run))
            while args.wait and run["status"] in ("queued", "running"):
                time.sleep(2)
                run = call("GET", f"/api/scans/{run['id']}")
                print("\r" + scan_line(run), end="", flush=True)
            if args.wait:
                print()
        elif args.action == "list":
            for run in call("GET", "/api/scans")["scans"]:
                print(scan_line(run))
        elif args.action == "estimate":
            show(call("GET", "/api/scans/estimate"))
        elif args.action == "show":
            show(call("GET", f"/api/scans/{args.id}"))
        else:
            print(scan_line(call("POST", f"/api/scans/{args.id}/{args.action}")))
    elif args.command == "hosts":
        data = call("GET", "/api/hosts" + (f"?run_id={args.run}" if args.run else ""))
        print(f"Skan #{data['run_id']}: {len(data['hosts'])} aktywnych hostów")
        for h in data["hosts"]:
            print(f"{h['ip']}  {h['hostname'] or ''}")
            for s in h["services"]:
                print(f"    {s['port']}/{s['proto']:<4} {s['name'] or '':<12} {' '.join(filter(None, (s['product'], s['version'], s['extrainfo'])))}")
    elif args.command == "schedule":
        if args.action == "templates":
            for key, t in call("GET", "/api/schedules/templates").items():
                print(f"{key:<12} {t['name']}  [{t['frequency']}, okno {t['window_start']}-{t['window_end']}]")
        elif args.action == "list":
            rows = call("GET", "/api/schedules")["schedules"]
            for s in rows:
                print(schedule_line(s))
            if not rows:
                print("Brak harmonogramu: skany tylko na żądanie.")
        elif args.action == "add":
            body = {"template": args.template, "name": args.name, "frequency": args.frequency}
            if args.window:
                body["window_start"], body["window_end"] = args.window.split("-")
            print("Dodano: " + schedule_line(call("POST", "/api/schedules", {k: v for k, v in body.items() if v})))
        else:
            show(call("DELETE", f"/api/schedules/{args.id}"))
    elif args.command == "settings":
        if args.pairs:
            body = {}
            for pair in args.pairs:
                key, _, value = pair.partition("=")
                body[key] = setting_value(value)
            show(call("PATCH", "/api/settings", body))
        else:
            show(call("GET", "/api/settings"))
    elif admin_commands(args):
        pass
    elif args.command == "summary":
        data = call("GET", "/api/summary")
        if args.json:
            show(data)
        else:
            print(summary_text(data))
    elif args.command == "changes":
        query = f"?min_priority={args.min_priority}" + (f"&run_id={args.run}" if args.run else "")
        data = call("GET", "/api/changes" + query)
        print(f"Skan #{data['run_id']}: {data['total']} zmian")
        for c in data["changes"]:
            print(f"  P{c['priority']}  {c['message']}")
    elif args.command == "findings":
        if args.action == "list":
            params = {"min_priority": args.min_priority, "tag": args.tag, "q": args.search, "ip": args.ip,
                      "state": "all" if args.all else "active"}
            query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None)
            data = call("GET", f"/api/findings?{query}")
            for f in data["findings"]:
                print(finding_line(f))
            print(f"Razem: {data['total']}")
        elif args.action == "show":
            show(call("GET", f"/api/findings/{args.id}"))
        elif args.action == "edit":
            body = {"priority": args.priority, "title": args.title, "note": args.note, "status": args.status}
            if args.tags is not None:
                body["tags"] = [t for t in args.tags.split(",") if t.strip()]
            print(finding_line(call("PATCH", f"/api/findings/{args.id}", {k: v for k, v in body.items() if v is not None})))
        else:
            fields = args.fields.split(",") if args.fields else None
            print(finding_line(call("POST", f"/api/findings/{args.id}/reset", {"fields": fields})))
    elif args.command == "host":
        show(call("GET", f"/api/hosts/{args.ip}"))
    elif args.command == "unstable":
        for h in call("GET", "/api/hosts/unstable")["hosts"]:
            pattern = (h.get("daypart") or {}).get("pattern") or ""
            state = {"unstable": "niestabilny", "inactive": "nieaktywny"}.get(h["state"], h["state"])
            print(f"{h['ip']:<16} {state:<12} skany bez odpowiedzi: {h['missed']}  {pattern}")
    elif args.command == "signatures":
        if args.action == "list":
            data = call("GET", "/api/signatures")
            for title, items in (("Wbudowane", data["builtin"]), ("Własne", data["custom"])):
                print(f"{title} ({len(items)}):")
                for sig in items:
                    print(f"  P{sig['priority']} {sig['id']:<20} {sig['kind']:<8} {sig['name']}")
        elif args.action == "test":
            data = call("POST", "/api/signatures/test", signature_body(args))
            for m in data["matches"]:
                print(f"  {m['ip']}:{m['port']}  {m['version'] or ''}  {m['evidence']}")
            print(f"Pasuje do {data['count']} usług.")
        elif args.action == "add":
            data = call("POST", "/api/signatures", signature_body(args))
            print(f"Dodano sygnaturę {data['signature']['id']}, przeliczono {data['reanalysed_findings']} znalezisk.")
        else:
            show(call("DELETE", f"/api/signatures/{args.id}"))
    elif args.command == "dojo":
        if args.action == "check":
            show(call("POST", "/api/dojo/check"))
        else:
            ids = [int(i) for i in args.ids.split(",")] if args.ids else None
            result = call("POST", "/api/dojo/send", {"finding_ids": ids, "min_priority": args.min_priority,
                                                     "only_unsent": args.unsent})
            if not result["sent"]:
                print(result.get("message"))
            else:
                total = ((result.get("statistics") or {}).get("after") or {}).get("total", {})
                print(f"Wysłano {result['sent']} znalezisk do DefectDojo (test #{result['test_id']}, "
                      f"produkt #{result['product_id']}). Aktywnych w teście: {total.get('active', '?')}.")
    elif args.command == "export":
        base = os.environ.get("AEGIS_URL", "http://127.0.0.1:8000").rstrip("/")
        if args.format == "full":
            text = json.dumps(call("GET", "/api/export/full"), ensure_ascii=False, indent=2)
        elif args.format == "json":
            text = json.dumps(call("GET", "/api/export/findings?format=json"), ensure_ascii=False, indent=2)
        else:
            request = urllib.request.Request(f"{base}/api/export/findings?format=csv", headers=headers())
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode("utf-8")
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Zapisano {args.output}")
        else:
            print(text)


def _new_password(given: str | None) -> str:
    if given:
        return given
    first = getpass.getpass("Hasło (min. 10 znaków): ")
    if first != getpass.getpass("Powtórz hasło: "):
        sys.exit("Hasła się różnią.")
    return first


def _read(path: str) -> tuple[str, str]:
    if path == "-":
        return sys.stdin.read(), ""
    return Path(path).read_text(encoding="utf-8-sig"), Path(path).name


def admin_commands(args) -> bool:
    if args.command == "import":
        content, name = _read(args.file)
        if args.action == "findings":
            r = call("POST", "/api/import/findings", {"content": content, "filename": name})
            print(f"Zaktualizowano {r['updated']}, bez zmian {r['unchanged']}, nie znaleziono {len(r['not_found'])}.")
        else:
            r = call("POST", "/api/import/full", {"data": json.loads(content)})
            print("Zaimportowano: " + ", ".join(f"{k} {v}" for k, v in r["imported"].items()))
    elif args.command == "users":
        if args.action == "list":
            for u in call("GET", "/api/users")["users"]:
                state = "wyłączone" if u["disabled"] else ("zablokowane" if u["locked_until"] else "aktywne")
                print(f"{u['id']:>4}  {u['username']:<20} {u['role']:<10} {state:<12} ostatnio: {u['last_login'] or '-'}")
        elif args.action == "add":
            u = call("POST", "/api/users", {"username": args.username, "password": _new_password(args.password),
                                            "role": args.role})
            print(f"Założono konto {u['username']} ({u['role']}).")
        elif args.action == "delete":
            show(call("DELETE", f"/api/users/{args.id}"))
        else:
            body = {"role": {"role": getattr(args, "role", None)}, "password": {}, "disable": {"disabled": True},
                    "enable": {"disabled": False}, "unlock": {"unlock": True}}[args.action]
            if args.action == "password":
                body = {"password": _new_password(None)}
            u = call("PATCH", f"/api/users/{args.id}", body)
            print(f"{u['username']}: rola {u['role']}, {'wyłączone' if u['disabled'] else 'aktywne'}.")
    elif args.command == "audit":
        for e in reversed(call("GET", "/api/audit?limit=100")["entries"]):
            print(f"{e['at']}  {e['username'] or '-':<14} {e['action']:<40} {e['target'] or ''}")
    else:
        return False
    return True


if __name__ == "__main__":
    main()
