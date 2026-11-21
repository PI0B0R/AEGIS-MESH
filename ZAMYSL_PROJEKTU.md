# AEGIS-MESH — zamysł projektu

**Wyzwanie AGH · Attack Surface Intelligence**

AEGIS-MESH to koncepcja systemu wczesnego ostrzegania dla administratora kampusowej sieci Wi-Fi. Model AI uczy się typowego zachowania sieci i wskazuje zmiany warte uwagi. Głównym ekranem jest lista ostrzeżeń z uzasadnieniem. Z wybranego ostrzeżenia administrator może otworzyć mapę, sprawdzić źródła obserwacji i zaplanować oględziny.

## Problem

Administrator ma spis uczelnianych punktów dostępowych. Może jednak nie wiedzieć, że w jednej z sal pojawiło się urządzenie rozgłaszające nazwę uczelnianej sieci z innymi zabezpieczeniami. Student widzi znajomą nazwę i może wybrać otwartą sieć. Z perspektywy administratora pierwszym sygnałem problemu jest rozbieżność między zatwierdzoną konfiguracją a tym, co rzeczywiście widać w sali.

## Jak ma działać pierwsza wersja

Projekt ma dwa warianty korzystające ze wspólnego serwera, historii i mapy kampusu.

- **Aplikacja mobilna na Androida.** Użytkownik wybiera „Rozpocznij pomiary” i udziela potrzebnych uprawnień. Podczas obchodu aplikacja zapisuje dostępne obserwacje Wi-Fi, czas ich wykonania, pozycję i dokładność lokalizacji. Sesja uruchomiona przy otwartej aplikacji może trwać po zgaszeniu ekranu dzięki usłudze pierwszoplanowej z widocznym powiadomieniem i przyciskiem „Zakończ”. Android ogranicza częstotliwość skanów. Aplikacja sprawdza wiek odczytu i pomija powtórnie zwrócone dane. [Zasady skanowania Wi-Fi w Androidzie](https://developer.android.com/develop/connectivity/wifi/wifi-scan)
- **Wersja webowa bez instalacji.** Użytkownik otwiera link, ogląda mapę i może wybrać „Zmierz tutaj”. Przeglądarka mierzy czas odpowiedzi na żądanie HTTPS do serwera projektu. Użytkownik może zezwolić na jednorazowy odczyt pozycji lub wskazać miejsce pinezką. Przed zapisaniem widzi wynik i lokalizację. [Geolokalizacja w przeglądarce](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API)

Każdy wpis na mapie ma źródło, rodzaj pomiaru, czas i miejsce. Dzięki temu w dymku widać np. „Przeglądarka · odpowiedź HTTPS · 85 ms” albo „Android · sygnał Wi-Fi · −62 dBm”. Obserwacje radiowe zasilają detekcję nieznanych punktów i estymację strefy nadajnika. Testy HTTPS informują o łączności z serwerem w danym miejscu. Datę i godzinę można odczytać w obu wariantach, najeżdżając na pinezkę lub jej dotykając.

1. **Inwentarz.** Administrator wczytuje plik CSV z nazwą sieci (SSID), identyfikatorem radiowym punktu dostępowego (BSSID), strefą i oczekiwanym sposobem zabezpieczenia.
2. **Obserwacje z kilku miejsc.** Telefony podczas obchodu oraz laptopy ustawione w znanych punktach zbierają informacje o widocznych sieciach. Każdy odczyt radiowy zawiera identyfikator czujnika, czas obserwacji, SSID, BSSID, rodzaj zabezpieczenia i siłę sygnału.
3. **Widok „Co się zmieniło?”.** Panel zestawia obserwacje z inwentarzem i wcześniejszymi pomiarami. Pokazuje nowe punkty dostępowe, zmiany zabezpieczeń oraz czas ostatniego odczytu z każdego czujnika. Gdy czujnik przestaje raportować, panel oznacza jego ostatni odczyt jako nieaktualny i aktualizuje stan pokrycia strefy.
4. **Weryfikacja alertu.** Administrator widzi oczekiwaną i zaobserwowaną konfigurację, czas pierwszego wykrycia oraz listę czujników, które zauważyły zmianę. Po sprawdzeniu urządzenia zapisuje wynik, np. „zatwierdzona wymiana sprzętu” albo „urządzenie przekazane do wyjaśnienia”.

## Co może przewidywać AI

**Pierwsza wersja — wczesne ostrzeżenie o anomalii.** Dla każdej chronionej nazwy sieci i strefy system zlicza m.in. nowe identyfikatory punktów, ich powroty i liczbę czujników potwierdzających obserwację. Prosty model wykrywania anomalii, np. Isolation Forest, uczy się na zapisach zwykłej pracy kampusu. Nietypowy przebieg trafia na listę ostrzeżeń razem z konkretnymi obserwacjami, np. „Nowy punkt powrócił w trzech kolejnych skanach. Widzą go oba aktywne czujniki”. Wynik opisuje nietypowość pomiarów. Pilny alert o zaobserwowanym obniżeniu zabezpieczeń działa niezależnie od modelu, także w pierwszym dniu zbierania danych.

**Docelowa prognoza — ryzyko konkretnego zdarzenia.** Przykładowe pytanie dla modelu brzmi „Czy w tej strefie w ciągu następnych pięciu minut po raz pierwszy zaobserwujemy niezatwierdzony punkt rozgłaszający chronioną nazwę jako sieć otwartą?”. Pięć minut to proponowany horyzont do sprawdzenia. Do uczenia potrzebne są oznaczone przebiegi zarówno z takim zdarzeniem, jak i bez niego, w tym legalne wymiany sprzętu. Model dostaje wyłącznie pomiary dostępne w chwili prognozy. Późniejsze oględziny dostarczają etykiety do oceny wyniku. Jeżeli urządzenie od razu pojawia się jako otwarte i nic wcześniej nie zapowiadało zmiany, system może zareagować przy pierwszej obserwacji.

**Sprawdzenie wartości prognozy.** Całe późniejsze sesje pomiarowe odkładamy do testu. Dla tego samego zapisu porównujemy ostrzeżenia modelu z prostą regułą „nowy punkt rozgłasza chronioną nazwę”. Przy podobnej liczbie fałszywych ostrzeżeń na godzinę sprawdzamy, ile zdarzeń każda metoda zapowiedziała i z jakim wyprzedzeniem względem pierwszego odczytu otwartej sieci. Ostrzeżenie wydane po tym odczycie liczy się jako wykrycie. Do czasu zebrania danych potwierdzających wyprzedzenie panel opisuje wyniki modelu jako anomalie. Pokaz na danych testowych pozwala ocenić działanie prototypu w warunkach próby.

## Geolokalizacja w wersji konkursowej

Geolokalizacja pomaga odpowiedzieć na pytanie administratora: „Skąd było widać tę usługę podczas danego przebiegu?”. Pinezka oznacza położenie czujnika w chwili obserwacji. Jedno stanowisko może mieć dostęp do kilku segmentów sieci, dlatego segment obserwacji wymaga osobnej informacji od operatora lub z konfiguracji czujnika.

**Działa w prototypie webowym.** Użytkownik udostępnia pozycję urządzenia lub wskazuje stanowisko na mapie. Po pomiarze mapa centruje się na tej pozycji. W serii z geolokalizacją aplikacja pobiera pozycję przy kolejnych odczytach. Przy połączeniu przez Ethernet można zapisać pojedynczy wynik bez współrzędnych i później przypisać miejsce z historii pomiarów.

**Planowane powiązanie z wynikami discovery.** Przy obserwacji usługi zapiszemy identyfikator przebiegu, czujnik, segment, czas, wynik sprawdzenia oraz dostępne współrzędne. Z karty zmiany administrator otworzy mapę i porówna wyniki dla tej samej usługi w tym samym oknie czasowym. Przykład pokazu: czujnik w akademiku odnotował odpowiedź HTTPS, a czujnik w laboratorium zakończył próbę bez odpowiedzi. Jeśli w danym segmencie pominięto próbę, widok pokaże „nie sprawdzono”. Obserwacje bez współrzędnych pozostaną dostępne w zestawieniu.

### Mapa kampusu z historią pomiarów

Po otwarciu mapy administrator widzi ulice, budynki i alejki kampusu AGH. Może przesuwać mapę i zmieniać powiększenie jak w Google Maps. Każdy zarejestrowany ping trafia na mapę jako pinezka we współrzędnych miejsca, z którego wykonano pomiar. Czujnik zapisuje razem z wynikiem czas pomiaru i swoją ówczesną pozycję, pobraną z urządzenia lub wskazaną na mapie przez operatora.

Najechanie kursorem na pinezkę otwiera dymek z datą i godziną w strefie Europe/Warsaw, nazwą miejsca lub czujnika oraz wynikiem pingu. Na telefonie dymek otwiera się po dotknięciu pinezki. Przykładowy dymek:

> **20.11.2026 · 14:32:08**  
> Kampus AGH · czujnik A  
> Ping do bramy testowej · 12 ms

Nowe pomiary pojawiają się na żywo. Gdy kilka pingów wykonano w tym samym miejscu, wspólna pinezka pokazuje ich liczbę. Dymek pozwala przejrzeć kolejne wyniki z datą i godziną każdego pomiaru.

Dla alertu Wi-Fi mapa może dodatkowo podświetlić szacowaną strefę nadajnika na podstawie siły sygnału radiowego. Administrator może sprawdzić, które pomiary uzasadniają ten obszar. Opóźnienie pingu opisuje łączność z celem. Dla incydentu widocznego wyłącznie w logach można oznaczyć znaną lokalizację monitorowanej usługi.

Rozszerzeniem mapy jest widok AR na telefonie. Operator staje w oznaczonym miejscu, kieruje telefon na znany punkt sali i kalibruje widok. Na obrazie z kamery pojawia się przybliżony kierunek do wskazanej strefy.

## Scenariusz pokazu

Na początku jury widzi listę ostrzeżeń dla zatwierdzonej sieci testowej `AGH-LAB`. Na oznaczonym zapisie wcześniejszej próby można pokazać chwilę ostrzeżenia modelu i odsłonić dalszy przebieg, w tym wynik oględzin. Następnie prowadzący przechodzi do obserwacji na żywo i włącza własny testowy punkt dostępowy o tej samej nazwie, działający w trybie otwartym.

W panelu pojawia się wpis: **„Nowy punkt rozgłasza AGH-LAB jako sieć otwartą”**. Administrator otwiera wpis, porównuje obie konfiguracje i sprawdza, skąd pochodzą obserwacje. Na mapie kampusu najeżdża na pinezkę, odczytuje datę i godzinę pomiaru, a następnie wybiera wskazaną strefę, odnajduje testowe urządzenie i zapisuje wynik oględzin. Historia zdarzenia łączy obserwacje czujników z decyzją administratora.

## Jak system oceni zmianę

Administrator określa, które nazwy sieci należą do organizacji i jakie zabezpieczenia są dla nich dozwolone. To pozwala oddzielić codzienne zmiany w otoczeniu radiowym od zdarzeń wymagających sprawdzenia.

| Co widzą czujniki | Co zobaczy administrator |
|---|---|
| W pobliżu pojawiła się sieć o obcej nazwie | Jest widoczna w ostatnim skanie. Samo jej pojawienie się nie otwiera sprawy. |
| Nowy BSSID rozgłasza uczelnianą nazwę z dozwolonym zabezpieczeniem | Wpis „sprawdź inwentarz”. Administrator może porównać go z planowaną wymianą urządzeń. |
| Punkt rozgłasza uczelnianą nazwę jako sieć otwartą, choć inwentarz wymaga 802.1X | Pilny alert z zestawieniem konfiguracji oczekiwanej i zaobserwowanej. Pierwszy odczyt otwiera sprawę, a obserwacja drugiego czujnika uzupełnia dowody. |
| Czujnik nie potrafił odczytać rodzaju zabezpieczenia | Pole „brak odczytu”. Reguła obniżenia zabezpieczeń czeka na pomiar z rozpoznanym trybem. |

Kolejne odczyty dotyczące tego samego BSSID, nazwy sieci i rozbieżności aktualizują jedną otwartą sprawę. Na jej osi czasu widać pierwszą obserwację, potwierdzenia z kolejnych miejsc, ostatnią obserwację i decyzję operatora. Chwilowy zanik sygnału pozostawia sprawę otwartą. Zatwierdzenie nowego urządzenia wymaga osobnej decyzji o aktualizacji inwentarza.

## Gdzie szukać urządzenia

Przed pokazem zespół dzieli salę na nazwane strefy, np. wejście, stanowiska i zaplecze. Umieszcza własny nadajnik kolejno w każdej strefie i zapisuje typowe odczyty z tych samych laptopów. Podczas pokazu system porównuje układ sił sygnału z ostatnich pomiarów z zapisanymi wzorcami. Na karcie zdarzenia można sprawdzić, które czujniki dostarczyły dane i kiedy.

Do prób sprawdzających należy wybrać inne miejsca niż przy kalibracji. Jeśli dwa miejsca mają podobny wzorzec albo jeden z potrzebnych czujników przestał raportować, mapa pokazuje szerszy obszar lub komunikat „za mało danych”. Gdy kilka czujników pokrywa tę samą strefę, utrata jednego z nich oznacza ograniczoną widoczność. Status „brak aktualnych danych” dotyczy strefy dopiero po utracie wszystkich świeżych odczytów.

## Kolejność budowy prototypu

| Etap | Co powstaje | Jak sprawdzimy działanie |
|---|---|---|
| 1. Inwentarz i pierwszy alert | Import CSV, reguły porównania i karta zdarzenia. Na wejściu zapis obserwacji z własnego sprzętu. | Odtworzenie tego samego zapisu dwukrotnie pozostawia jedną sprawę. Obca nazwa sieci nie generuje pilnego alertu. |
| 2. Obserwacje na żywo | Android, agent laptopowy i panel webowy wysyłają oznaczone typem pomiary do FastAPI. SQLite przechowuje pomiary i historię spraw. Panel odbiera zmiany przez WebSocket. | Operator rozpoczyna i kończy obchód z telefonu, a druga osoba wykonuje jednorazowy test przez link. Obie osoby widzą odpowiednio opisane wyniki na tej samej mapie. Po zakończeniu sesji telefon przestaje zbierać odczyty. |
| 3. Model anomalii i lista ostrzeżeń | Profil zwykłej pracy, wynik anomalii i karta z obserwacjami. | Na odłożonej sesji zespół porównuje ostrzeżenia z rzeczywistym przebiegiem i z prostą regułą. |
| 4. Mapa na żywo i decyzja operatora | Punkty pomiarowe, pingi do bramy testowej, szacowana strefa nadajnika i zapis wyniku oględzin. | Operator najeżdża na pinezkę na mapie kampusu i porównuje datę, godzinę oraz miejsce z zapisem pomiaru. Powtarza próbę dotknięciem na telefonie. W próbie z nadajnikiem w nieujawnionym miejscu porównuje wskazaną strefę z faktyczną. Po wygaśnięciu odczytów sprawdza czas ostatniego oszacowania. |
| 5. Widok AR | Telefon pokazuje wskazówkę kierunkową z oznaczonego stanowiska. | Po kalibracji operator obraca telefon w stronę strefy zaznaczonej na mapie i sprawdza zgodność wskazania. |

Pierwsze cztery etapy składają się na pokaz od ostrzeżenia po weryfikację. Osobny eksperyment z prognozą można rozpocząć po zebraniu oznaczonych sesji.
