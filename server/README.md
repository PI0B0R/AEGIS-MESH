# AEGIS MESH: serwer (etap 1)

Serwer skanuje sieć programem nmap w bezpiecznych ustawieniach, zapisuje wyniki
w bazie SQLite i pokazuje je przez API oraz komendy w terminalu. Wszystko działa
lokalnie, bez chmury i bez AI. Laptop potrzebuje tylko Dockera: nmap jest w obrazie.

## Start w 3 krokach

```bash
docker compose up -d --build                                   # serwer + mała sieć testowa
docker compose exec aegis aegis targets add 172.30.0.0/27 --note "sieć testowa"
docker compose exec aegis aegis scan start --wait              # skan z paskiem postępu
docker compose exec aegis aegis hosts                          # hosty i usługi
```

Dokumentacja API (można klikać i wysyłać zapytania): http://127.0.0.1:8000/docs

Sieć testowa `172.30.0.0/24` zawiera: nginx (.20), Apache (.21), stronę „staging”
na Pythonie (.22, port 8080) i Redis (.30). Serwer AEGIS ma adres .10.

## Cele: ręcznie, CSV, JSON

Pojedyncze IP, CIDR (`10.0.0.0/24`), zakres (`10.0.0.5-10.0.0.40` albo `10.0.0.5-40`)
i domeny. Wszystkie drogi wejścia przechodzą przez ten sam parser.

```bash
docker compose exec aegis aegis targets add 10.0.0.1 10.0.1.0/24 example.org
docker compose exec -T aegis aegis targets import - --name cele.csv < lab/cele/przyklad.csv
docker compose exec -T aegis aegis targets import - --name cele.json < lab/cele/przyklad.json
docker compose exec aegis aegis targets list
```

CSV może mieć nagłówek (`cel`/`target`/`ip`… oraz `notatka`/`note`) albo nie mieć go wcale.
JSON to lista adresów, lista obiektów albo obiekt `{"targets": [...]}`.

## Skan: limit szybkości, podgrupy, czas

- nmap działa z `--max-rate` (domyślnie 300 pakietów/s), tylko na dozwolonych portach
  i z lekkim rozpoznawaniem wersji (`-sV --version-light`). Bez logowania i bez exploitów.
- Pierwszy skan zaczyna od małej podgrupy kalibracyjnej, mierzy szybkość i dzieli resztę
  na podgrupy po około minucie pracy. Podgrupy idą równolegle (`threads`, domyślnie 4).
- Każda podgrupa ma zapisany czas, a kolejny skan dostaje realny szacunek czasu.

```bash
docker compose exec aegis aegis scan estimate     # ile potrwa następny skan
docker compose exec aegis aegis scan list
docker compose exec aegis aegis scan show 1       # podgrupy i ich czasy
docker compose exec aegis aegis scan pause 1
docker compose exec aegis aegis scan resume 1     # kontynuuje od miejsca zatrzymania
docker compose exec aegis aegis settings rate=100 threads=2
```

Postęp jest w bazie, więc wstrzymany skan, skan zatrzymany końcem okna czasowego
i skan przerwany restartem kontynuują od miejsca, w którym stanęły.

## Harmonogram: okno i częstotliwość

```bash
docker compose exec aegis aegis schedule templates
docker compose exec aegis aegis schedule add nocny                       # codziennie 22:00-06:00
docker compose exec aegis aegis schedule add --name "Mój" --frequency every:6h --window 08:00-16:00
docker compose exec aegis aegis schedule list
```

Częstotliwość: `daily`, `weekly:0-6` (0 = poniedziałek), `every:Nh`. Skan, który nie zmieści się
w oknie, zostaje wstrzymany o końcu okna i wznowiony w następnym. Strefa czasu: `AEGIS_TZ`
(domyślnie Europe/Warsaw).

## Szyfrowanie

Wrażliwe pola (notatki do celów, banery usług: produkt, wersja, dodatkowe informacje)
są w bazie zaszyfrowane AES-256-GCM. Klucz leży w osobnym wolumenie `aegis-keys`
(`/keys/aegis.key`), a baza w `aegis-data`. Klucz można też podać zmienną `AEGIS_KEY` (base64).

## Testy

```bash
cd server
pip install -r requirements-dev.txt -e .
pytest -q                                          # testy jednostkowe (bez nmapa)
AEGIS_URL=http://127.0.0.1:8000 pytest -q -m lab   # dwa prawdziwe skany sieci testowej
```

CI na GitHubie (`.github/workflows/server.yml`) uruchamia oba zestawy, w tym `docker compose`.
