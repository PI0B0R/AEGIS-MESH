# Notatka przekazania dla kolejnego Claude: AEGIS MESH

Ten plik opisuje cały projekt: po co powstał, jak jest zbudowany, jak doszliśmy do obecnego stanu, jak go uruchomić u właściciela i na co uważać. Napisany 22.11.2026, stan repo: `main` po PR #5 (commit `519c036`).

**W tym pliku nie ma żadnych haseł ani tokenów i tak ma zostać.** Hasła istnieją tylko na laptopie właściciela (plik `.env`, baza w wolumenie Dockera).

---

## 1. Kim jest użytkownik i jak z nim pracować

- Użytkownik (GURING) pisze po polsku. **Odpowiadaj po polsku**, prostymi zdaniami.
- Jest początkujący. Dawaj instrukcje krok po kroku, numerowane, z poleceniem do skopiowania w osobnej linijce. Mów, w którym programie wpisać (PowerShell albo Git Bash) i w jakim folderze (`C:\Users\<użytkownik>\Documents\AEGIS-MESH`).
- Gdy wklei błąd, wyjaśnij jednym zdaniem przyczynę i podaj jedno konkretne polecenie naprawcze.
- **Bez dopisków o Claude jako współtwórcy** w commitach, opisach PR i plikach (szczegóły w §11).
- **Scalanie PR tylko po wyraźnym „scal N”** (np. „scal 6”) dla danego PR. Ogólna zgoda z przeszłości nie wystarcza na nowy PR, a automatyczny klasyfikator i tak zablokuje scalenie bez zgody.
- Bezpośredni push na `main` jest blokowany. Każda zmiana idzie przez gałąź i PR.
- Nigdy nie commituj sekretów: `.env`, haseł, tokenów, kluczy, certyfikatu CA z sandboxa ani łatanego Dockerfile z sandboxa (patrz §11).

## 2. Cel: zadanie konkursowe AGH

Konkurs AGH (CBI), zadanie „Attack Surface Intelligence”. Treść zadania i regulamin to dwa PDF-y od użytkownika (w plikach projektu: `mesh agh/AGH - Pełna treść zadania konkursowego.pdf` i Regulamin). Mentorzy: Łagodowski, Skalny, Nal.

Co trzeba zbudować: system, który cyklicznie skanuje wskazane zakresy sieci, rozpoznaje usługi i technologie, porównuje skany, wskazuje zmiany i nadaje priorytety znaleziskom, z panelem WWW i integracją z DefectDojo.

Najważniejsze wymagania z PDF (16 punktów) w skrócie:
1. Zakresy celów: IP, CIDR, zakresy, import z pliku.
2. Wykrywanie aktywnych hostów i otwartych portów.
3. Rozpoznawanie usług i wersji.
4. Rozpoznawanie technologii WWW (nagłówki, HTML, favicon, ścieżki).
5. Własne sygnatury dodawane przez użytkownika.
6. Bezpieczne skanowanie: limity szybkości, tylko wykrywanie, bez exploitów i prób logowania.
7. Harmonogram skanów i okna czasowe.
8. Porównanie skanów: nowe i zniknięte hosty, zmiany usług.
9. Hosty niestabilne (raz są, raz nie).
10. Priorytety znalezisk i uzasadnienie „dlaczego”.
11. Ręczna edycja znalezisk, statusy, tagi, notatki.
12. Eksport do DefectDojo.
13. Role użytkowników i uprawnienia.
14. Bezpieczeństwo samego serwera (szyfrowanie danych, nagłówki, audyt).
15. Panel WWW, na którym jury testuje system.
16. API i dokumentacja.

Wymagania formalne: repozytorium, dokumentacja, prezentacja do 10 slajdów z demo. Ocena: 30 / 15 / 20 / 15 / 10 / 10 punktów (funkcjonalność, jakość, bezpieczeństwo, innowacyjność, dokumentacja, prezentacja; dokładny podział jest w PDF). Jury testuje według 12 punktów testowych z PDF.

Ograniczenia ustalone z użytkownikiem:
- tylko darmowe i open source,
- **bez AI** w produkcie,
- **bez hostingu w chmurze**, serwer działa na laptopie użytkownika,
- serwer w Pythonie.

## 3. Architektura

```
 telefon (aplikacja Android) ──HTTPS──> Tailscale Funnel (aegis.tail36df25.ts.net)
                                              │
 przeglądarka jury ──http://127.0.0.1:8000──> │
                                              ▼
                               kontener "aegis" (FastAPI + uvicorn)
                               ├─ SQLite (WAL) w wolumenie /data, pola szyfrowane AES-256-GCM
                               ├─ klucz szyfrowania w osobnym wolumenie /keys
                               ├─ nmap (skan) + sonda HTTP + sygnatury
                               ├─ harmonogram (szablony, okna czasowe)
                               └─ eksport ──> DefectDojo (profil compose "dojo")
```

### Serwer (`server/aegis/`)
- `api.py`, `api_admin.py`, `api_results.py`: FastAPI, trasy `/api/...`, dokumentacja pod `/docs`. `/api/health` zwraca m.in. `service: "aegis-mesh"` (aplikacja szuka po tym serwera w LAN).
- `auth.py`: role **czytelnik < analityk < edytor < admin**. Hasła scrypt. Blokada 15 min po 5 nieudanych logowaniach. Token ważny 12 h. Tabela `PERMISSIONS`: trasa bez wpisu wymaga admina. Token dla terminala w `/data/cli.token`. Dziennik audytu.
- `crypto.py`: AES-256-GCM, nazwa kolumny jako AAD, prefiks `enc1:`.
- `db.py`: SQLite w trybie WAL, model danych.
- `scanner.py`: nmap z `--max-rate 300`, 20 dozwolonych portów, `-sV --version-light`. Skan dzielony na podgrupy kalibrowane na ok. 60 s, wątki, pauza i wznowienie.
- `web.py`: sonda HTTP tylko GET, przekierowania tylko w obrębie hosta, info o TLS, hash favicony mmh3, próby znanych ścieżek.
- `signatures.py` + `signatures.json`: 48 wbudowanych sygnatur. Rodzaje: header, cookie, html, title, favicon, path, service, tls. Użytkownik dodaje własne.
- `findings.py`: priorytety 1–5, tagi, zdanie „dlaczego” po polsku. Reguły: logowanie po HTTP daje P5, wygasły certyfikat P3, certyfikat self-signed P2, flagi ciasteczek. Ręczna edycja oznacza znalezisko jako „edytowane” i skan go już nie nadpisuje.
- Statusy znalezisk: `nowe`, `potwierdzone`, `w-trakcie`, `rozwiązane`, `fałszywy-alarm`, `zaakceptowane-ryzyko`.
- `diff.py`: `new_host`, `gone_host` (po 3 kolejnych nieobecnościach, ustawienie `unstable_after`), `unstable_host`, `host_back`, zmiany usług, ponowne sprawdzenie (recheck chunk), obecność wg pory dnia.
- `scheduler.py`: szablony `nocny`, `weekend`, `co-6-godzin`, `co-godzine`, okna czasowe.
- `dojo.py`: `reimport-scan`, format Generic Findings Import, severity Info, `auto_create_context`.
- `cli.py`: polecenie `aegis` w kontenerze. Podpolecenia: `targets`, `scan` (`start --wait`, `estimate`), `hosts`, `host`, `changes`, `unstable`, `findings`, `signatures`, `schedule` (`list`, `add <szablon>`, `templates`), `dojo` (`test`, `send`), `users`, `role`, `settings`, `audit`, `export`, `import`, `summary` (`--json`), `serve`.
- `panel/`: panel WWW w czystym JS (bez frameworków), 5 ekranów: Pulpit, Cele, Hosty, Sygnatury, Ustawienia. Przyciski, do których użytkownik nie ma prawa, są ukryte. CSP blokuje kod inline, więc nie dodawaj `onclick=` ani `<script>` w HTML.

### docker-compose.yml
- Usługa `aegis` na `${AEGIS_BIND:-127.0.0.1}:8000`. Domyślnie tylko localhost, `AEGIS_BIND=0.0.0.0` otwiera na LAN.
- Hasło admina AEGIS przy pierwszym starcie: z `AEGIS_ADMIN_PASSWORD` albo losowe w `/data/admin-password.txt` (`docker compose exec aegis cat /data/admin-password.txt`).
- Sieć testowa `lab` 172.30.0.0/27 imituje laboratorium jury: nginx (.20), apache (.21), staging z `/admin` (.22), WordPress-podobna strona (.23), redis (.30), kamera z certyfikatem self-signed. Profil `zmiana` dodaje host .28 (pokazuje „nowy host”).
- Profil `dojo`: DefectDojo 3.5.103. AEGIS rozmawia z `http://dojo-uwsgi:8081`, UI jest na `http://127.0.0.1:8081`. `DD_ADMIN_PASSWORD` (z `DOJO_ADMIN_PASSWORD` w `.env`, a bez niego domyślne hasło laboratoryjne z compose) działa **tylko przy pierwszej inicjalizacji** bazy Dojo. Zmienne `AEGIS_DOJO_*` wypełniają ustawienia AEGIS tylko raz, potem obowiązuje to, co zapisano w Ustawieniach panelu.

### Aplikacja Android (`android-app/`)
Kotlin, zwykłe View, **bez bibliotek zewnętrznych** (bez play-services). Paleta czarno-złota.
- `Api.kt`: klient HTTP i `HttpFailure`.
- `Session.kt`: SharedPreferences „aegis” (adres, token, rola, `can_analityk`, zbiór `announced`). Logowanie: `/api/auth/login`, potem `/api/auth/me`. Wyszukiwanie serwera w LAN: /24 telefonu, port 8000, `/api/health`.
- `MainActivity.kt`: ekran logowania i zakładki. Ukryte „inny serwer”: długie przytrzymanie logo na ekranie logowania.
- `DashboardTab.kt`: kafelki (aktywne hosty, zmiany, priorytet 4–5), ostatni i następny skan, „Co nowego”, przycisk „Skanuj teraz” dla edytora i admina.
- `AlertsTab.kt`: znaleziska P4–5, oczekujące na górze; przycisk „Sprawdzone” ustawia status `potwierdzone` (analityk i wyżej).
- `RadarTab.kt`, `RadarView.kt`, `ConeView.kt`, `HotColdActivity.kt`, `Radio.kt`, `Store.kt`: radar Wi-Fi i tryb „ciepło-zimno”, działa lokalnie na telefonie.
- `AlertJob.kt`: JobScheduler co 15 min (minimum Androida), powiadomienie o nowych P4–5 ze statusem `nowe`.
- `BuildConfig.AEGIS_URL`: domyślnie `https://aegis.tail36df25.ts.net`, nadpisywane `-PaegisUrl=...` (CI czyta też `AEGIS_FUNNEL_URL`, ale ustawienie zmiennej Actions przez proxy nie działa, dlatego domyślna wartość jest w `build.gradle`).
- Build debug dopuszcza HTTP; aplikacja przyjmuje HTTP tylko dla prywatnych adresów IPv4.
- versionCode 3, versionName 0.5. `app/aegis-debug.keystore` to klucz **debug** (celowo w repo, żeby kolejne APK z CI dało się instalować jako aktualizację).

### Stary kod
- `aegis-web/`: pierwszy prototyp w Pythonie (radar, parowanie QR, mapy). Zostaje jako historia, nowy serwer go nie używa.
- `NOTATKA_DO_GEMINI.txt`, `OPIS_PROJEKTU.txt`, `ZAMYSL_PROJEKTU.md`: wczesne notatki użytkownika.

## 4. Układ repozytorium i ważne pliki

| Ścieżka | Co to |
|---|---|
| `README.md` | szybki start, tabela dokumentów, uwaga o Windowsie |
| `docs/DOKUMENTACJA.md` | pełna dokumentacja, 18 rozdziałów (§17 telefon i Funnel, §18 ograniczenia) |
| `docs/img/*.png` | zrzuty ekranu panelu do dokumentacji |
| `docker-compose.yml` | serwer, sieć testowa, profile `dojo` i `zmiana` |
| `server/` | serwer AEGIS (Dockerfile, `aegis/`, `tests/`) |
| `demo/pokaz.sh` | skrypt pokazu dla jury, krok po kroku z Enterem |
| `lab/` | pliki hostów testowych i przykładowe listy celów (`lab/cele/przyklad.csv`, `.json`) |
| `android-app/` | aplikacja na telefon |
| `.github/workflows/server.yml` | testy serwera + lab + demo |
| `.github/workflows/android-apk.yml` | testy i build APK, wydanie `apk-latest` |
| `.gitattributes` | `*.sh` i `gradlew` zawsze z końcami linii LF |
| `.gitignore` | m.in. `.env` |

## 5. Najważniejsze polecenia

```bash
# start serwera i sieci testowej
docker compose up -d --build
# z DefectDojo (pierwszy start trwa kilka minut)
docker compose --profile dojo up -d
# hasło admina AEGIS z pierwszego startu
docker compose exec aegis cat /data/admin-password.txt
# przykładowy skan
docker compose exec aegis aegis targets add 172.30.0.0/27
docker compose exec aegis aegis scan start --wait
docker compose exec aegis aegis hosts
docker compose exec aegis aegis summary
# pokaz dla jury (Git Bash na Windowsie)
bash demo/pokaz.sh --dojo          # z pauzami, Enter po każdym kroku
bash demo/pokaz.sh --bez-pauz --dojo   # bez pauz (tak działa CI)
# testy serwera
cd server && pip install -r requirements-dev.txt && pytest            # jednostkowe
pytest -m lab                                                          # na żywej sieci testowej
```

Panel: http://127.0.0.1:8000, API: http://127.0.0.1:8000/docs, DefectDojo: http://127.0.0.1:8081.

## 6. Historia projektu i decyzje

### Przed planem
- Pierwszy był prototyp `aegis-web` w Pythonie i aplikacja Android jako „sensor” (radar Wi-Fi). Wcześniejsza wersja w Kivy została usunięta, doszło CI budujące APK.
- Commity: `8db6e54` prototyp, `cd45e5d` CI dla APK, `0167653` czarno-złoty wygląd, `8442719` poprawka ciepło-zimno, `b4bd239` uproszczenie QR i jego cofnięcie `af0e286` (na prośbę użytkownika).

### Plan
Plan powstał w osobnym wątku i leży w plikach projektu: `plan/PLAN_AEGIS_MESH.md`. Kluczowe decyzje:
- **Panel WWW wrócił jako rdzeń.** Na początku był mniej ważny niż telefon, ale punkt 15 zadania i to, że jury testuje w przeglądarce, przesądziły sprawę. Telefon jest dodatkiem (innowacyjność).
- **Bez parowania QR.** Zamiast tego adres Tailscale Funnel jest wbudowany w APK, a użytkownik po prostu się loguje. Mniej kroków dla jury i mniej kodu.
- **Serwer na laptopie, wystawiony przez Tailscale Funnel**, bo nie wolno używać hostingu w chmurze, a telefon musi się dostać do serwera spoza sieci lokalnej.
- Kopia zapasowa: wyszukiwanie serwera w Wi-Fi (`AEGIS_BIND=0.0.0.0`).

### Etapy
| Etap | Gdzie | Co |
|---|---|---|
| 1. Fundament | gałąź `etap-1-fundament`, commit `eadfb52` | serwer FastAPI, baza, szyfrowanie, cele, nmap, sieć testowa w compose |
| 2. Rozpoznawanie | bezpośrednio na `main` za zgodą, `0a80e2e` | sonda HTTP, sygnatury, znaleziska i priorytety, diff skanów, harmonogram |
| 3. Panel, role, Dojo | PR #1, `08d9b40` | panel WWW, role i uprawnienia, audyt, DefectDojo, testy lab i Playwright |
| 4. Dokumentacja i demo | PR #2, `c58d0df` | `docs/DOKUMENTACJA.md` z zrzutami, README, `demo/pokaz.sh`, `aegis summary`, krok demo w CI |
| 5. Telefon | PR #3, `cc6c4d9` | nowa aplikacja: logowanie, Pulpit, Alerty, Radar, powiadomienia; usunięte `SensorService`, `Transport`, QR; wersja 0.5.0 |
| poprawka | PR #4, `c21d271` | domyślny adres Funnel wbudowany w `build.gradle` |
| poprawka | PR #5, `519c036` | Windows: `.gitattributes` (LF), `MSYS_NO_PATHCONV` w `pokaz.sh`, `.env` w `.gitignore`, uwaga w README |

Od etapu 3 wszystko idzie przez PR, bo klasyfikator blokuje bezpośredni push na `main`. Użytkownik zgodził się na scalenie PR #1–#3 jednym zdaniem, a potem osobno napisał „scal 4” i „scalić 5”.

### Szczegóły zmian w etapach 4–5
- `demo/pokaz.sh` jest powtarzalny: przed dodaniem sygnatury `blog-wydzialu` usuwa ją, harmonogram dodaje tylko gdy go nie ma (`aegis schedule list | grep -q "Codziennie w nocy" || run aegis schedule add nocny`), ma `export MSYS_NO_PATHCONV=1`.
- `server/tests/test_lab.py` akceptuje dla .28 zarówno `new_host`, jak i `host_back` (dane z poprzedniego uruchomienia), a sprawdzenie Dojo to `sent >= 1`. Test przeglądarkowy używa użytkownika `lab-czytelnik`.
- `/api/health` zwraca nowy zestaw pól, `test_auth` je sprawdza.
- W `AndroidManifest.xml`: usługa `AlertJob` z `BIND_JOB_SERVICE`, `singleTop`, bez usługi pierwszoplanowej.
- `build.gradle`:
  ```groovy
  def funnel = (project.findProperty('aegisUrl') ?: '').toString().trim()
  buildConfigField 'String', 'AEGIS_URL', '"' + (funnel ?: 'https://aegis.tail36df25.ts.net').replace('"', '') + '"'
  ```

## 7. Konfiguracja laptopa użytkownika (zrobiona 22.11.2026)

- Windows 11, Docker Desktop, Git Bash, PowerShell. Repo sklonowane w `C:\Users\<użytkownik>\Documents\AEGIS-MESH`.
- Tailscale: maszyna `aegis`, pełna domena **`aegis.tail36df25.ts.net`**.
  1. HTTPS włączone na https://login.tailscale.com/admin/dns („HTTPS Certificates” → Enable HTTPS).
  2. `tailscale funnel --bg 8000` w PowerShell. Za pierwszym razem odpowiada „Funnel is not enabled on your tailnet” z linkiem `https://login.tailscale.com/f/funnel?node=...`. Trzeba otworzyć ten link, kliknąć Enable i powtórzyć polecenie. Potem: „Funnel started and running in the background”, proxy na `http://127.0.0.1:8000`.
  3. Funnel działa w tle, ale po restarcie laptopa trzeba sprawdzić, czy działa Docker i Funnel (`tailscale funnel status`).
- `.env` w folderze repo zawiera jedną linię `DOJO_ADMIN_PASSWORD=<hasło>`. Tworzony w PowerShell:
  `Set-Content -Encoding ascii .env "DOJO_ADMIN_PASSWORD=<hasło>"`
  (`-Encoding ascii`, żeby nie było BOM, z którym Docker by sobie nie poradził).
- DefectDojo uruchomione `docker compose --profile dojo up -d`. Hasło admina Dojo zostało ustawione ręcznie (patrz §8), a to samo hasło jest wpisane w panelu AEGIS: Ustawienia → DefectDojo → Zapisz → Sprawdź połączenie.
- Harmonogram nocny ustawiony, eksport do Dojo działa.
- Pełny pokaz `bash demo/pokaz.sh --dojo` przeszedł w Git Bash.
- Aplikacja z wydania `apk-latest` zainstalowana na telefonie. Logowanie jako admin przez Funnel działa, zakładki Pulpit, Alerty i Radar działają (potwierdzone przez użytkownika).

## 8. Problemy, które już wystąpiły, i rozwiązania

| Problem | Przyczyna | Rozwiązanie |
|---|---|---|
| `pokaz.sh` na Windowsie: `$'\r': command not found` | Git na Windowsie zamienił końce linii na CRLF | `.gitattributes` z `*.sh text eol=lf` (PR #5). Doraźnie: `sed -i 's/\r$//' demo/pokaz.sh` |
| W Git Bash ścieżki typu `/data/...` w `docker compose exec` zamieniały się na `C:/Program Files/Git/data/...` | konwersja ścieżek MSYS | `export MSYS_NO_PATHCONV=1` w `pokaz.sh` (PR #5); doraźnie prefiks `MSYS_NO_PATHCONV=1` przed poleceniem |
| Docker nie czytał `.env` | użytkownik zapisał w pliku samo słowo zamiast `DOJO_ADMIN_PASSWORD=...` | wyjaśnić format `KLUCZ=wartość` i podać polecenie `Set-Content` w całości |
| Hasło z `.env` nie działało w Dojo | `DD_ADMIN_PASSWORD` działa tylko przy pierwszej inicjalizacji, a Dojo wystartował wcześniej z domyślnym hasłem | `docker compose exec dojo-uwsgi ./manage.py changepassword admin` (pyta dwa razy, nic nie widać przy pisaniu), polecenie w folderze repo |
| `tailscale funnel` odmawia | Funnel nie był włączony w tailnecie | otworzyć link z komunikatu, Enable, powtórzyć polecenie |
| Drugie uruchomienie demo: „Sygnatura blog-wydzialu już istnieje” | brak powtarzalności | usuwanie sygnatury przed dodaniem (PR #2) |
| Testy lab padały na danych z poprzedniego przebiegu | zbyt sztywne asercje, kolizja hasła użytkownika testowego | luźniejsze asercje, inny użytkownik testowy |
| Ustawienie zmiennej Actions `AEGIS_FUNNEL_URL` | proxy sandboxa zwraca 403 | domyślny adres wpisany w `build.gradle` (PR #4) |
| Scalenie PR #4 bez zgody | klasyfikator zablokował | zapytać użytkownika, czekać na „scal N” |

## 9. CI

- `server.yml`: testy jednostkowe, potem zadanie lab: `docker compose --profile dojo up`, Playwright, `pytest -m lab`, `demo/pokaz.sh --bez-pauz --dojo`. Filtr ścieżek obejmuje `server/**`, `lab/**`, `demo/**`, compose.
- `android-apk.yml`: testy jednostkowe i `assembleDebug` na każdej gałęzi i PR. Wydanie **`apk-latest`** (https://github.com/PI0B0R/AEGIS-MESH/releases/tag/apk-latest) publikowane tylko z `main`.
- Ostatni znany stan: testy jednostkowe 96 passed, 4 skipped; lab 4 passed; demo przechodzi dwa razy z rzędu; Android zielony. Po PR #5 całe CI na `main` zielone.

## 10. Co jeszcze otwarte

- **Prezentacja dla jury** jest gotowa jako prywatny Artifact w claude.ai. Użytkownik musi ją udostępnić (menu Share), zanim wyśle link jury.
- Niezrobione funkcje z planu (można dodać, jeśli zostanie czas):
  - grupowanie podobnych usług (np. wiele hostów z tym samym nginx),
  - pasywne źródła danych (np. certyfikaty z CT, DNS).
- Znane ograniczenia (opisane w `docs/DOKUMENTACJA.md` §18): blokada konta po 5 próbach może być użyta przez kogoś z internetu przez Funnel do zablokowania logowania (DoS); powiadomienia telefonu najwyżej co 15 minut.
- Przed oddaniem warto przypomnieć użytkownikowi: zmienić domyślne hasła, włączyć laptop, Docker i Funnel przed testami jury.

## 11. Praca w sandboxie Claude (ważne dla Ciebie)

- Repo: `github.com/PI0B0R/AEGIS-MESH`, klon lokalny zwykle w `/home/user/aegis-mesh`. Dołącz repo przez `add_repo`, jeśli go nie ma.
- PR twórz przez REST: `gh api repos/PI0B0R/AEGIS-MESH/pulls -f title=... -f head=... -f base=main -F body=@plik.md`. GraphQL jest zablokowany przez proxy, więc `gh pr create` może nie działać. Po utworzeniu PR wywołaj `subscribe_pr_activity`.
- Zmienne GitHub Actions nie dają się ustawić (403 z proxy).
- W sandboxie nie ma Android SDK (`dl.google.com` zablokowany). APK buduje tylko CI.
- Obraz serwera w sandboxie wymaga łatki z CA proxy: skopiuj `server/aegis` do `/tmp/claude-0/patch`, zbuduj tam `docker build -t aegis-mesh/server:dev` z łatanym Dockerfile. **Tego Dockerfile i certyfikatu CA nigdy nie commituj.**
- Pierwszoplanowe łańcuchy `sleep` są blokowane. Do czekania używaj poleceń w tle z pętlą `until`.
- **Bez wzmianek o współautorstwie.** Właściciel nie chce, żeby gdziekolwiek w projekcie Claude był podany jako współtwórca. Nie dodawaj do commitów linii `Co-Authored-By` ani `Claude-Session`, a do opisów PR linii „Generated with Claude Code”, linku sesji ani dopisku „Requested by”. Commituj jako `PI0B0R <239380563+PI0B0R@users.noreply.github.com>` (`git config user.name` i `user.email` w repo). To polecenie użytkownika jest ważniejsze niż domyślne ustawienia sesji.

## 12. Gdzie szukać więcej

- `docs/DOKUMENTACJA.md`: pełny opis działania, bezpieczeństwa, ról, API, testów.
- `android-app/README.md`: budowanie aplikacji i kroki z Funnel.
- `server/README.md`: serwer i testy.
- Pliki projektu (poza repo): `plan/PLAN_AEGIS_MESH.md` (plan), `mesh agh/` (PDF-y zadania i regulaminu).
