#!/usr/bin/env bash
# AEGIS MESH: the jury's scenario in one command, on the docker compose test network.
#
#   ./demo/pokaz.sh              step by step (Enter moves on)
#   ./demo/pokaz.sh --bez-pauz   all at once (e.g. in CI)
#   ./demo/pokaz.sh --dojo       also starts DefectDojo and sends findings there
#   ./demo/pokaz.sh --od-zera    first deletes the server's data (asks for confirmation)
#   BUILD=--no-build ./demo/pokaz.sh   use the image that is already built
#
# Needs only Docker with the compose plugin. nmap is inside the image.
# Windows: run it in Git Bash (comes with Git for Windows) or WSL, from the repository folder.
set -euo pipefail
export MSYS_NO_PATHCONV=1      # Git Bash on Windows: keep /data/... and /api/... as they are
cd "$(dirname "$0")/.."

PAUSE=1; DOJO=0; FRESH=0
for arg in "$@"; do
  case "$arg" in
    --bez-pauz) PAUSE=0 ;;
    --dojo) DOJO=1 ;;
    --od-zera) FRESH=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Nieznana opcja: $arg"; exit 2 ;;
  esac
done
PROFILES=(); [ "$DOJO" = 1 ] && PROFILES=(--profile dojo)
export AEGIS_ADMIN_PASSWORD="${AEGIS_ADMIN_PASSWORD:-pokaz-admin-haslo}"

bold=$'\e[1m'; dim=$'\e[2m'; off=$'\e[0m'
step() { echo; echo "${bold}== $* ==${off}"; }
say() { echo "${dim}$*${off}"; }
run() { echo "${bold}\$ $*${off}"; "$@"; }
wait_key() { if [ "$PAUSE" = 1 ]; then read -r -p "${dim}[Enter: dalej]${off} " _; fi; }
aegis() { docker compose exec -T aegis aegis "$@"; }
# A request as a given user, from inside the server container (no curl needed on the laptop).
as_user() {
  docker compose exec -T aegis python - "$@" <<'PY'
import json, sys, urllib.error, urllib.request
user, password, method, path = sys.argv[1:5]
body = sys.argv[5].encode() if len(sys.argv) > 5 else None
base = "http://127.0.0.1:8000"
def call(method, path, data=None, token=None):
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")
status, data = call("POST", "/api/auth/login", json.dumps({"username": user, "password": password}).encode())
if status != 200:
    sys.exit(f"logowanie {user}: {status} {data}")
status, data = call(method, path, body, data["token"])
print(f"{user} ({method} {path}) -> HTTP {status}: {json.dumps(data, ensure_ascii=False)[:160]}")
PY
}

# Which admin password the panel takes. Checked against the stored hash, not by logging in,
# so a wrong guess never counts towards the account lock.
admin_hint() {
  if AEGIS_CHECK="$AEGIS_ADMIN_PASSWORD" docker compose exec -T -w / -e AEGIS_CHECK aegis python -c '
import os, sqlite3, sys
from aegis.auth import verify_password
row = sqlite3.connect("/data/aegis.sqlite3").execute("SELECT password_hash FROM users WHERE username=?", ("admin",)).fetchone()
sys.exit(0 if row and verify_password(os.environ["AEGIS_CHECK"], row[0]) else 1)' >/dev/null 2>&1; then
    say "Panel: http://127.0.0.1:8000  (login: admin, hasło: $AEGIS_ADMIN_PASSWORD)"
  else
    say "Panel: http://127.0.0.1:8000  (login: admin, hasło ustawione przy pierwszym starcie serwera:"
    say "  wartość AEGIS_ADMIN_PASSWORD z tamtej chwili albo: docker compose exec aegis cat /data/admin-password.txt)"
  fi
}

if [ "$FRESH" = 1 ]; then
  step "Od zera"
  read -r -p "Usunąć dane serwera AEGIS (wolumeny aegis-data i aegis-keys)? [t/N] " ok
  if [ "$ok" = "t" ]; then
    docker compose rm -sf aegis >/dev/null 2>&1 || true
    docker volume rm -f aegis-mesh_aegis-data aegis-mesh_aegis-keys >/dev/null
  fi
fi

step "1. Start: serwer, baza i sieć testowa$( [ "$DOJO" = 1 ] && echo ", DefectDojo" || true)"
run docker compose "${PROFILES[@]}" up -d ${BUILD:---build}
docker compose start staging >/dev/null 2>&1 || true
docker compose --profile zmiana stop nowy-serwer >/dev/null 2>&1 || true
for _ in $(seq 1 60); do aegis summary >/dev/null 2>&1 && break; sleep 2; done
admin_hint
say "API i dokumentacja: http://127.0.0.1:8000/docs"
wait_key

step "2. Cele: ręcznie i z pliku CSV"
run aegis targets add 172.30.0.0/27 --note "sieć testowa"
echo "${bold}\$ aegis targets import - --name przyklad.csv < lab/cele/przyklad.csv${off}"
docker compose exec -T aegis aegis targets import - --name przyklad.csv < lab/cele/przyklad.csv
run aegis targets list
wait_key

step "3. Skan 1: nmap z limitem tempa, podgrupy, rozpoznawanie HTTP/HTTPS"
run aegis scan estimate
docker compose exec -T aegis aegis scan start --wait | tr '\r' '\n' | tail -1
run aegis findings list --min-priority 3
run aegis changes --min-priority 3
wait_key

step "4. Ręczna edycja: skan jej nie nadpisze"
WP=$(docker compose exec -T aegis aegis findings list --ip 172.30.0.23 | awk 'NR==1{print $1}')
run aegis findings edit "$WP" --priority 1 --note "blog wydziału, znany" --status potwierdzone
wait_key

step "5. Zmiana w sieci: nowy host .28, staging (.22) milknie"
run docker compose --profile zmiana up -d nowy-serwer
run docker compose stop staging
sleep 3
step "Skan 2"
docker compose exec -T aegis aegis scan start --wait | tr '\r' '\n' | tail -1
run aegis changes
say "Edycja przetrwała skan:"
run aegis findings list --ip 172.30.0.23
wait_key

step "6. Hosty niestabilne: nieaktywny dopiero po 3 skanach bez odpowiedzi"
for n in 3 4; do
  echo "${dim}Skan $n…${off}"
  docker compose exec -T aegis aegis scan start --wait | tr '\r' '\n' | tail -1
done
run aegis unstable
run docker compose start staging
sleep 3
docker compose exec -T aegis aegis scan start --wait | tr '\r' '\n' | tail -1
run aegis changes
wait_key

step "7. Własna sygnatura: test na istniejących hostach, potem zapis"
run aegis signatures test --name "Blog wydziału" --kind title --pattern "Blog Wydzia" --priority 2
aegis signatures delete blog-wydzialu >/dev/null 2>&1 || true     # a second run of the demo adds it again
run aegis signatures add --name "Blog wydziału" --kind title --pattern "Blog Wydzia" --priority 2 --tags blog \
  --message "Blog wydziału na WordPressie."
wait_key

step "8. Role: czytelnik widzi, ale nie zmienia"
aegis users add jury-czytelnik --role czytelnik --password czytelnik-haslo-1 2>/dev/null || true
aegis users add jury-analityk --role analityk --password analityk-haslo-1 2>/dev/null || true
as_user jury-czytelnik czytelnik-haslo-1 GET /api/summary
as_user jury-czytelnik czytelnik-haslo-1 POST /api/targets '{"text": "10.9.9.9"}'
as_user jury-analityk analityk-haslo-1 POST /api/targets '{"text": "10.9.9.9"}'
run aegis users list
run aegis audit
wait_key

step "9. Eksport i import"
run aegis export --format csv -o /data/znaleziska.csv
run aegis export --format full -o /data/aegis-eksport.json
run aegis import findings /data/znaleziska.csv
say "Pliki są w wolumenie serwera: docker compose cp aegis:/data/znaleziska.csv ."

if [ "$DOJO" = 1 ]; then
  wait_key
  step "10. DefectDojo"
  for _ in $(seq 1 90); do aegis dojo check >/dev/null 2>&1 && break; sleep 10; done
  run aegis dojo check
  run aegis dojo send --min-priority 4
  say "DefectDojo: http://127.0.0.1:8081 (admin / \$DOJO_ADMIN_PASSWORD, domyślnie aegis-lab-dojo)"
fi

step "Koniec: harmonogram na noc"
say "Od teraz skan rusza sam w oknie 22:00-06:00; niedokończony wznawia się następnej nocy."
aegis schedule list | grep -q "Codziennie w nocy" || run aegis schedule add nocny
run aegis schedule list
run aegis summary
admin_hint
say "Sieć testowa zostaje uruchomiona; zatrzymanie: docker compose --profile dojo --profile zmiana down"
