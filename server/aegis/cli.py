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
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def call(method: str, path: str, body=None):
    base = os.environ.get("AEGIS_URL", "http://127.0.0.1:8000").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
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
            show(call("GET", "/api/schedules"))
        elif args.action == "add":
            body = {"template": args.template, "name": args.name, "frequency": args.frequency}
            if args.window:
                body["window_start"], body["window_end"] = args.window.split("-")
            show(call("POST", "/api/schedules", {k: v for k, v in body.items() if v}))
        else:
            show(call("DELETE", f"/api/schedules/{args.id}"))
    elif args.command == "settings":
        if args.pairs:
            body = {}
            for pair in args.pairs:
                key, _, value = pair.partition("=")
                body[key] = int(value) if value.isdigit() else value
            show(call("PATCH", "/api/settings", body))
        else:
            show(call("GET", "/api/settings"))


if __name__ == "__main__":
    main()
