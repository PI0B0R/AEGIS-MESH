# AEGIS MESH na Androidzie

Telefon to dodatek do serwera AEGIS MESH, przydatny na pokazie: te same dane co panel WWW,
powiadomienia o ważnych znaleziskach i radar Wi-Fi. Aplikacja korzysta z tego samego API
co panel i komenda `aegis`, z tym samym logowaniem i rolami.

## Trzy zakładki

| Zakładka | Co pokazuje |
|---|---|
| **Pulpit** | te same trzy kafelki co panel (aktywne hosty, zmiany, priorytet 4–5), ostatni i następny skan, „Co nowego”; edytor i admin mają też „Skanuj teraz” |
| **Alerty** | znaleziska z priorytetem 4–5, najpierw te, które czekają; przycisk **Sprawdzone** (analityk i wyżej) zmienia ich status na `potwierdzone`; dotknięcie pokazuje szczegóły i powody |
| **Radar Wi-Fi** | punkty dostępowe w pobliżu ze statusem *Podejrzany* / *Hotspot* / *Nowy* / *Znany* i radar ciepło-zimno; wszystko zostaje na telefonie |

Co 15 minut (najkrótszy okres w Androidzie) aplikacja sprawdza w tle, czy pojawiły się nowe
znaleziska 4–5 o statusie „nowe”, i pokazuje jedno powiadomienie. Dotknięcie otwiera zakładkę Alerty.

## Logowanie i adres serwera

- Konto zakłada administrator w panelu (Ustawienia → Użytkownicy) albo `aegis users add`.
  Samodzielnej rejestracji nie ma. Po 5 błędnych hasłach konto blokuje się na 15 minut,
  a token wygasa po 12 godzinach. Aplikacja zapisuje tylko token, nigdy hasła.
- **Tryb główny: Tailscale Funnel.** Adres laptopa (np. `https://aegis.twoja-siec.ts.net`) jest
  wbudowany w APK. Telefon nie potrzebuje Tailscale.
- **Tryb zapasowy: sieć Wi-Fi.** Gdy adres z APK nie odpowiada albo go nie ma, aplikacja sama
  szuka serwera na porcie 8000 w tej samej sieci /24 (pyta `/api/health` każdego adresu, kilka sekund).
  Serwer musi wtedy nasłuchiwać w sieci: `AEGIS_BIND=0.0.0.0 docker compose up -d`.
- **Inny serwer:** przytrzymaj logo na ekranie logowania, żeby wpisać adres ręcznie.

## Tailscale Funnel na laptopie (raz)

1. Zainstaluj Tailscale (darmowy plan osobisty, klient open source) i zaloguj się.
2. W panelu Tailscale, zakładka **DNS**: włącz **HTTPS Certificates**. Nazwij komputer `aegis`.
3. Uruchom serwer AEGIS (`docker compose up -d`), a potem w terminalu:
   ```bash
   tailscale funnel --bg 8000
   tailscale funnel status          # pokazuje adres https://aegis.<twoja-siec>.ts.net
   ```
   Przy pierwszym uruchomieniu Tailscale poda link do zatwierdzenia Funnel.
4. Wpisz ten adres do APK: w repozytorium na GitHubie **Settings → Secrets and variables →
   Actions → Variables** dodaj zmienną `AEGIS_FUNNEL_URL`. Kolejna kompilacja w CI wbuduje go w aplikację.
   Lokalnie: `./gradlew assembleDebug -PaegisUrl=https://aegis.twoja-siec.ts.net`.
5. Wyłączenie: `tailscale funnel --bg 8000 off`. Tunel jest tylko na pokaz; jury go nie potrzebuje.

Przez Funnel z internetu widać tylko ekran logowania panelu, `/api/health` (nazwa i wersja)
i dokumentację API. Wszystko inne wymaga tokenu.

## Bezpieczeństwo

- Wersja **release** łączy się tylko przez HTTPS z certyfikatem zaufanym systemowo (Funnel ma
  certyfikat Let's Encrypt). Wersja **debug**, którą buduje CI, pozwala też na HTTP, ale aplikacja
  przyjmuje HTTP tylko dla prywatnych adresów IPv4 (tryb zapasowy w LAN).
- Przyciski, do których rola nie ma prawa, są ukryte, a serwer i tak sprawdza rolę przy każdym zapytaniu.
- Radar Wi-Fi nie wysyła nic na serwer. Do pokazania sieci Android wymaga zgody na lokalizację.

## Kompilacja

GitHub Actions (`.github/workflows/android-apk.yml`) uruchamia testy i buduje APK po każdej zmianie
w `android-app/`. Z gałęzi `main` plik trafia do wydania **apk-latest**, z innych gałęzi do artefaktu
`aegis-mesh-apk` w zakładce Actions.

Lokalnie potrzebne są JDK 17 i Android SDK Platform 35:

```bash
./gradlew testDebugUnitTest      # logika radia, adresy, wyszukiwanie w LAN, wygasanie tokenu
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
