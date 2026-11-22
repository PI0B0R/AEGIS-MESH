# AEGIS MESH: serwer (etapy 1 i 2)

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
z panelem logowania bez HTTPS (.22, port 8080), stronę na WordPressie (.23),
„kamerę IP” na HTTPS z certyfikatem self-signed (.24) i Redis (.30). Serwer AEGIS ma adres .10.
Z `--profile zmiana` dochodzi nowy host (.28), żeby pokazać zmianę między skanami.

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

## Rozpoznawanie: HTTP/HTTPS, technologie, sygnatury

Po nmapie każda usługa WWW dostaje kilka zapytań GET (z limitem `http_rate`, domyślnie 10/s):
strona główna (tytuł, kod, nagłówki, ciasteczka z flagami, przekierowania tylko w obrębie hosta),
certyfikat TLS (wystawca, ważność, self-signed, SAN), favicona (hash mmh3 jak w Shodanie i md5)
oraz krótka lista ścieżek z sygnatur (`/admin/`, `/login`, `/version`, `/server-status`, `/.git/HEAD`).
Bez logowania, bez wysyłania formularzy, bez exploitów.

Wbudowane sygnatury są w [`aegis/signatures.json`](aegis/signatures.json): serwery, frameworki,
CMS (WordPress, Joomla, Drupal), panele admina, staging, IoT (kamery, routery, drukarki, MQTT),
bazy danych, usługi bez szyfrowania. Rodzaje: `header`, `cookie`, `html`, `title`, `favicon`,
`path`, `service` (baner nmapa), `tls`. Grupa w nawiasach w wyrażeniu to wersja.

```bash
docker compose exec aegis aegis signatures list
docker compose exec aegis aegis signatures test --name "Moja aplikacja" --kind title --pattern "Moja-App v([\d.]+)" --priority 4
docker compose exec aegis aegis signatures add  --name "Moja aplikacja" --kind title --pattern "Moja-App v([\d.]+)" --priority 4 \
    --tags wewnetrzne --message "Wewnętrzna aplikacja widoczna w sieci."
```

`test` sprawdza sygnaturę na zapisanych danych hostów (bez zapisu), `add` zapisuje ją
i od razu przelicza znaleziska.

## Znaleziska: tagi, priorytet 1–5, ręczne edycje

Każda usługa to jedno znalezisko z technologiami, tagami (np. `staging`, `panel-admina`, `cms`,
`iot`, `baza-danych`, `logowanie-bez-https`), priorytetem 1–5 i jednym zdaniem po polsku,
dlaczego jest ważne, np. „Panel logowania bez HTTPS: hasła idą otwartym tekstem”.

```bash
docker compose exec aegis aegis findings list --min-priority 4
docker compose exec aegis aegis findings list --tag staging
docker compose exec aegis aegis findings edit 5 --priority 1 --tags cms,znany --note "blog wydziału"
docker compose exec aegis aegis findings reset 5 --fields priority      # oddaj pole skanom
docker compose exec aegis aegis host 172.30.0.24                         # karta hosta
```

Pole zmienione ręcznie dostaje znaczek „edytowane” (`is_edited`, `edited`) i **kolejne skany go nie nadpisują**.
Skan dalej liczy wartość automatyczną (`auto`), więc `reset` przywraca aktualny stan.

## Porównanie skanów i hosty niestabilne

Po każdym skanie system sam zapisuje zmiany: nowy host, nowa usługa, usługa zniknęła,
zmiana wersji, tytułu lub kodu HTTP, host niestabilny, host nieaktywny, host wrócił.

```bash
docker compose exec aegis aegis changes          # od najważniejszych
docker compose exec aegis aegis summary          # kafelki pulpitu
docker compose exec aegis aegis unstable
```

Host, który przestał odpowiadać, jest **sprawdzany ponownie** w tym samym skanie (osobna podgrupa).
Dopiero po **3 kolejnych skanach** bez odpowiedzi (`unstable_after`) staje się nieaktywny.
System zapisuje też, o jakiej porze dnia host odpowiada, i zgłasza hosty, które odpowiadają
np. tylko w nocy.

Pokaz na sieci testowej:

```bash
docker compose exec aegis aegis scan start --wait
docker compose --profile zmiana up -d nowy-serwer   # nowy host
docker compose stop staging                         # host milknie
docker compose exec aegis aegis scan start --wait
docker compose exec aegis aegis changes
```

## DefectDojo

Własna testowa instancja jest w tym samym `docker compose` (profil `dojo`):

```bash
docker compose --profile dojo up -d          # pierwszy start DefectDojo trwa kilka minut
docker compose exec aegis aegis dojo check
docker compose exec aegis aegis dojo send --min-priority 4
docker compose exec aegis aegis dojo send --ids 3,7
```

Interfejs DefectDojo: http://127.0.0.1:8081 (login `admin`, hasło z `DOJO_ADMIN_PASSWORD`,
domyślnie `aegis-lab-dojo`; zmień je przed użyciem poza laboratorium).
Znaleziska trafiają jako findingi informacyjne (severity Info) z dowodami (URL, tytuł, nagłówek
Server, certyfikat, powody i notatka analityka). Ponowna wysyłka aktualizuje ten sam test
(`reimport-scan`), więc nie tworzy duplikatów. `aegis settings dojo_auto=true` wysyła nowe
ważne znaleziska automatycznie po każdym skanie.

Inna instancja (np. od organizatorów):

```bash
docker compose exec aegis aegis settings dojo_url=https://dojo.example dojo_token=TOKEN
```

## Eksport

```bash
docker compose exec aegis aegis export --format csv > znaleziska.csv
docker compose exec aegis aegis export --format json > znaleziska.json
```

## Szyfrowanie

Wrażliwe pola są w bazie zaszyfrowane AES-256-GCM: notatki, banery usług (produkt, wersja),
dane HTTP (nagłówki, ciasteczka, treść strony, tytuł), technologie i opisy znalezisk, opisy zmian
oraz hasło i token DefectDojo. Jawne zostają tylko adresy, porty, priorytety, tagi i stany,
żeby listy dało się filtrować. Klucz leży w osobnym wolumenie `aegis-keys`
(`/keys/aegis.key`), a baza w `aegis-data`. Klucz można też podać zmienną `AEGIS_KEY` (base64).

## Testy

```bash
cd server
pip install -r requirements-dev.txt -e .
pytest -q                                          # testy jednostkowe (bez nmapa)
AEGIS_URL=http://127.0.0.1:8000 pytest -q -m lab   # pięć prawdziwych skanów sieci testowej ze zmianami
AEGIS_URL=http://127.0.0.1:8000 AEGIS_LAB_DOJO=1 pytest -q -m lab   # to samo + wysyłka do DefectDojo
```

CI na GitHubie (`.github/workflows/server.yml`) uruchamia oba zestawy, w tym `docker compose` z DefectDojo.
