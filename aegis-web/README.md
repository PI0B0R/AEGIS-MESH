# AEGIS-MESH · prototyp webowy

W katalogu projektu uruchom `python server.py` (Python 3.10 lub nowszy). Serwer korzysta z biblioteki standardowej. Otwórz [lokalny panel](http://127.0.0.1:8765). Serwer zatrzymasz przez Ctrl+C.

## Aplikacja lokalna

Panel pokazuje zapisane pomiary przeglądarki, odczyty Wi-Fi i rozbieżności wynikające z porównania importów z inwentarzem. Wybierz **Zmierz tutaj**, wykonaj test, podaj pozycję urządzenia lub wskaż ją na mapie i zapisz. Wynik bez pozycji jest dostępny w historii. Na mapie i w licznikach pomiarów łączności panel uwzględnia do 500 najnowszych zapisów. W zakładce **Alerty** można importować własne pliki CSV z inwentarzem i obserwacjami.

### Lokalizacja pomiaru

Przy pracy w jednym budynku wybierz obszar nad mapą, następnie **Zmierz tutaj → Wskaż na mapie**. Kliknij rzeczywiste miejsce laptopa, wpisz opcjonalną nazwę i rozpocznij serię. Ten punkt obowiązuje do jej zakończenia. Przycisk „Sprawdź wskazanie przeglądarki” służy do diagnostyki. Zatwierdzenie proponowanego miejsca oznacza ręczny wybór stałego stanowiska. Kolejne odczyty przeglądarki mogą być zapisywane diagnostycznie po zaznaczeniu odpowiedniej opcji. Bez stanowiska lub świeżej pozycji sparowanego telefonu aplikacja zachowuje skan bez pinezki.

W historii i na mapie jeden raport laptopa ma wspólne miejsce i czas. Rozwiń raport, aby zobaczyć punkty dla wybranego SSID według siły sygnału, ich BSSID, zabezpieczenia, kanał i przybliżone dBm. Także pusty odczyt trafia do historii. „Raport bez zmian” oznacza identyczną treść dla tej sieci w kolejnych odczytach tej samej sesji. Czas ostatniej zmiany oznacza zmianę zawartości raportu.

Obszar pracy obejmuje promień 25 km od wskazanego miasta. Historyczne pozycje z przeglądarki, o nieznanym pochodzeniu lub spoza obszaru są domyślnie ukryte na mapie. Można je obejrzeć przełącznikiem **Pokaż historyczne pozycje niezweryfikowane**. Wcześniejsze obserwacje pozostają w bazie. W historii wpisy bez identyfikatora skanu są opisane jako archiwalne.

### Telefon przy laptopie

W lokalnym panelu rozwiń **Telefon jako źródło lokalizacji**. Wybierz adres interfejsu osiągalny z telefonu, potwierdź obecność telefonu przy laptopie i kliknij **Sparuj telefon**. Potrzebne są OpenSSL (w systemowym PATH lub z Git for Windows) oraz połączenie telefonu do wybranego adresu laptopa na porcie TCP 8767. Przy braku prywatnego adresu IPv4 parowanie jest niedostępne. Sieć akademicka może izolować klientów.

Panel udostępnia publiczny certyfikat lokalnego urzędu. Przenieś plik na swój telefon i włącz dla niego zaufanie (na iOS także pełne zaufanie dla zainstalowanego profilu). Następnie otwórz na telefonie jednorazowy link z panelu i wybierz **Udostępnij pozycję**. Link wygasa po dwóch minutach. Po zaakceptowaniu zgody pozostaw stronę otwartą i trzymaj telefon przy laptopie. W serii z telefonem pomiń wybór ręcznej pinezki. Ręczne stanowisko ma pierwszeństwo. Przycisk **Rozłącz telefon** kończy sesję. Maksymalny czas parowania wynosi godzinę.

Telefon wysyła nowe pozycje, gdy przeglądarka dostarczy odczyt. Serwer przyjmuje współrzędne o dokładności do 50 m i wieku do 15 s, sprawdza obszar pracy oraz odrzuca skoki powyżej 150 km/h z uwzględnieniem niepewności obu pozycji. Po utracie świeżej pozycji nowe pomiary są zapisywane bez współrzędnych. Wskaźnik ostatniej znanej pozycji wygasa po 60 s od odczytu. Rozpoznanie nazwy hotspotu nie uruchamia pozycjonowania.

Certyfikaty i klucz serwera powstają obok bazy w folderze phone-tls dla wybranego adresu. Na telefon przenosi się tylko publiczny plik AEGIS-phone-ca.crt. Po zakończeniu testów można usunąć ten certyfikat z telefonu. Klucz urzędu jest tymczasowy. Port telefonu obsługuje wyłącznie widok telefonu i komunikaty parowania oraz lokalizacji.

W opcji „Skoryguj miejsca pomiarów” wybierz przedział czasu spędzony na jednym stanowisku. Panel pokazuje liczbę zapisów do korekty. Wskazanie i zatwierdzenie pinezki tworzy osobny wpis korekty dla każdego wybranego wyniku. Pierwotne współrzędne pozostają w bazie.

## Demo AGH · Attack Surface Intelligence

Otwórz [pokaz konkursowy](http://127.0.0.1:8765/demo.html) i kliknij **Uruchom dwa przebiegi**. Na czas próby aplikacja tworzy dwie usługi na interfejsie 127.0.0.1 na portach przydzielonych przez system. Jeden serwer odpowiada przez HTTP, drugi przez HTTPS. Oba przebiegi sprawdzają te same adresy i porty. Domyślnie portal HTTPS zmienia tytuł strony i kod z 200 na 503. Można też sprawdzić opóźnioną odpowiedź lub powtórzyć przebieg bez zmian. Brak odpowiedzi w limicie czasu pozostawia dostępność nierozstrzygniętą.

Raport zestawia wyniki po protokole, hoście i porcie. Pokazuje czas przebiegu, kod odpowiedzi, tytuł strony, nagłówek Server, nazwy ciasteczek i wersję TLS. Ostatni pokaz trafia do tabeli discovery_demo. Eksport i ponowny import pliku JSON odtwarza zestawienie. Import raportu działa w przeglądarce. Klucz i certyfikat w demo-tls służą lokalnej usłudze testowej.

Pokaz realizuje część wymagań AGH. Otwarte pozostają m.in. obsługa zakresów ponad 10 000 adresów, konfiguracja harmonogramu, rozbudowany fingerprinting, własne sygnatury, trwała edycja znalezisk, role egzekwowane przez serwer i szyfrowanie danych w spoczynku.

## Własny odczyt z Windows

W zakładce **Pomiary → Sygnały i historia Wi-Fi** panel grupuje odczyty według pary SSID i BSSID. Własny hotspot można oznaczyć po sprawdzeniu go na telefonie i cofnąć oznaczenie z lokalnego panelu. Lista pokazuje najpierw własne hotspoty, następnie punkty pasujące do inwentarza, możliwe hotspoty rozpoznane po nazwie i sygnały o nieustalonym typie. Oznaczenie opisuje rozpoznanie nadawcy. Źródło współrzędnych i ocena zabezpieczeń pozostają osobnymi informacjami.

W podglądzie pod adresem `http://127.0.0.1:8765` na laptopie z Windows wybierz **Zmierz tutaj → Odczyty Wi-Fi laptopa**. Panel uzupełnia SSID połączonej sieci z Windows. Przy kilku połączeniach wybierz nazwę z listy. Możesz też podać ją ręcznie. Przy Ethernecie wpisz ręcznie interesujący Cię SSID. Odczyt raportu wymaga działającej karty Wi-Fi. Wybierz pozycję urządzenia lub stałe stanowisko na mapie i kliknij **Rozpocznij serię**. Pierwszy odczyt wykonuje się od razu, kolejne co około 30 sekund. Pasek sesji pokazuje wynik i liczbę zapisanych obserwacji. Przycisk **Zatrzymaj** kończy serię po bieżącym odczycie. Zamknięcie lub przeładowanie strony kończy planowanie kolejnych odczytów. Uśpienie urządzenia lub ograniczenia pracy karty w tle mogą opóźnić pomiary.

Sesja korzysta z raportu karty WLAN komputera, na którym działa serwer. Czas obserwacji oznacza moment pobrania raportu. Windows może zwrócić dane z pamięci podręcznej. Przy pustym inwentarzu nadal można zapisywać obserwacje; porównywanie zabezpieczeń wymaga wcześniejszego zatwierdzenia konfiguracji. Z telefonu połączonego z panelem można wybrać **Seria testów łączności**. Ta opcja mierzy czas żądania do serwera z przeglądarki telefonu.

Warstwa **Odczyty Wi-Fi** na mapie pokazuje fioletowe znaczniki wszystkich zaimportowanych skanów. Numer na znaczniku oznacza liczbę raportów lub grup archiwalnych w danym miejscu. Najedź na znacznik, by zobaczyć najnowszy odczyt, albo kliknij go, by przejrzeć historię z datą, godziną, czujnikiem i zabezpieczeniami. Przycisk **Pokaż miejsca skanów** dopasowuje widok również dla pomiarów poza kampusem.

W katalogu aplikacji uruchom np. poniższe polecenie po podstawieniu nazwy swojej sieci, nazwy czujnika i rzeczywistych współrzędnych laptopa.

```powershell
python export_wifi.py --ssid "AGH-LAB" --sensor "Laptop B-2" --lat 50.06621 --lng 19.91889 --output scan-01.csv
```

Eksporter odczytuje systemowy raport WLAN w języku polskim lub angielskim i zapisuje obserwacje wskazanego SSID. Czas w CSV oznacza moment pobrania raportu. Windows może zwrócić wcześniej zebrane wyniki. W panelu można również importować pliki z innych narzędzi po dopasowaniu kolumn do przykładów.

CSV używa UTF-8 i separatora przecinka lub średnika. Czas wymaga strefy (np. `2026-11-20T10:05:00+01:00`), a współrzędne kropki dziesiętnej. Tryby obejmują OPEN, WEP, OWE i WPA/WPA2/WPA3 z końcówką -PERSONAL lub -ENTERPRISE. UNKNOWN w skanie oznacza brak rozpoznanego trybu.

Przy imporcie znany BSSID jest porównywany z własnym wpisem. Dla nowego BSSID chronionej nazwy system dopuszcza tryby zapisane w inwentarzu dla tego SSID. Obce nazwy oraz odczyty UNKNOWN nie otwierają sprawy. Skany i dowody zachowują stan inwentarza z chwili importu. Kolejne obserwacje tej samej rozbieżności uzupełniają sprawę. Powtórny import identycznej obserwacji pomija ją także po restarcie serwera.

## Podgląd na telefonie

Na zaufanej sieci lokalnej uruchom serwer poleceniem `python server.py --host 0.0.0.0 --port 8766`. Na telefonie połączonym z tą samą siecią otwórz adres LAN komputera z portem 8766. Oba urządzenia korzystają z tej samej bazy. Przez lokalny HTTP pozycję można wskazać na mapie. Automatyczna geolokalizacja wymaga bezpiecznego kontekstu, np. HTTPS lub localhost. Panel LAN udostępnia pomiary i importy wszystkim urządzeniom mającym dostęp do portu. Domyślny adres uruchomienia ogranicza dostęp do tego komputera.

## Dane mapowe

Lokalny plik `static/campus.json` zawiera geometrię kampusu pozyskaną z OpenStreetMap, przefiltrowaną do budynków, dróg, ścieżek, parkingów i zieleni. Dane © OpenStreetMap contributors, [licencja ODbL](https://www.openstreetmap.org/copyright). Szerszy zasięg mapy korzysta z internetowych kafelków OpenStreetMap. Dołączona biblioteka Leaflet 1.9.4 ma licencję w `static/vendor/LEAFLET-LICENSE.txt`.

## MVP Wi-Fi · inspekcja sali

W panelu otwórz **Plan sali**. W zakładce **Alerty** zaimportuj inwentarz, na przykład:

```csv
ssid,bssid,zone,expected_security
AGH-LAB,02:aa:00:00:00:01,Strefa A,802.1X
```

Wartość 802.1X w inwentarzu akceptuje rozpoznane tryby Enterprise. Przy **Zmierz tutaj → Odczyty Wi-Fi laptopa** wybierz faktyczne stanowisko A–D. Przy przeniesieniu czujnika zakończ serię i wybierz kolejną strefę. Strefa czujnika opisuje jego miejsce w sali i może być zapisana bez współrzędnych geograficznych. Strefa podana w inwentarzu opisuje zatwierdzony punkt dostępowy. Na planie A i B leżą przy ścianie odniesienia, a D i C przy przeciwległej.

Plan pokazuje ostatni odczyt i czas ostatniej zmiany raportu. Identyczna treść może pochodzić z bufora Windows. Nowy BSSID znanej sieci wymaga sprawdzenia również przy zgodnym trybie zabezpieczeń. Porównanie mocy dotyczy tego samego BSSID i trybu zabezpieczeń. Wskazanie wymaga zmienionych raportów w co najmniej dwóch strefach w ostatnich 90 sekundach. Próg remisu to 4 dB. Przy remisie dwóch sąsiednich stref panel pokazuje obie. To parametry pokazu do sprawdzenia w konkretnej sali. Przeszkody i odbicia fal mogą zmienić ranking. Brak aktualnego porównania pozostawia alert otwarty.

Wybierz sprawę i zapisz wynik oględzin. Dziennik zachowuje czas, wynik i notatkę. Kolejna obserwacja tej samej rozbieżności po zamknięciu ponownie otwiera sprawę. Surowy zapis i decyzje pozostają dostępne. Osoba mająca administracyjny dostęp do pliku SQLite może go zmienić.

### Telefon online i oględziny

W lokalnym panelu **Telefon jako źródło lokalizacji** wybierz połączenie LAN lub **Publiczny tunel HTTPS (ngrok)**. Tryb online wymaga działającego programu ngrok w PATH, skonfigurowanego konta i dostępu do internetu. Agent udostępnia osobny serwer telefonu przez tymczasowy port na interfejsie loopback. Panel pobiera adres z lokalnego API ngrok na porcie 4040. Przycisk **Rozłącz telefon** zamyka tunel uruchomiony przez aplikację. W trybie online telefon używa certyfikatu publicznego dostawcy. Zeskanuj QR lokalnie wyświetlony przez laptop. Sekret jednorazowy znajduje się we fragmencie adresu. Jeśli po przejściu przez ekran powitalny ngrok telefon poprosi o PIN, wpisz osiem cyfr widocznych pod kodem QR na laptopie. Obie metody parują jeden telefon i wygasają po dwóch minutach.

Aby obejrzeć salę, na telefonie wybierz **Przejdź do oględzin sali**. Telefon przestaje podawać pozycję laptopa i wyświetla otwarte alerty. Z punktu na środku sali skieruj tylną kamerę na środek ściany A–B. Wybierz **Włącz kamerę i orientację**, zaakceptuj uprawnienia i kliknij **Kalibruj widok**. Wskaźnik wykorzystuje orientację telefonu względem kierunku kalibracji i schemat czterech ćwiartek. Po zmianie miejsca wróć do środka przed ponowną kalibracją. Obraz z kamery jest wyświetlany lokalnie. Gdy telefon nie dostarczy orientacji lub strefa wygaśnie, użyj tekstowego wskazania strefy.

### Oddzielny pokaz dla jury

Z katalogu aegis-web uruchom `python server.py --room-demo --port 8768` i otwórz [plan pokazowy](http://127.0.0.1:8768/room.html). Proces zakłada tymczasową bazę z zatwierdzoną siecią AGH-LAB i czterema fikcyjnymi czujnikami. Wybierz **Symuluj nowy punkt OPEN**, sprawdź przyczynę alertu i strefę B, następnie zapisz wynik oględzin. Przycisk odtwarza kolejny komplet próbek na potrzeby ponownej prezentacji. Do pokazu na telefonie rozpocznij parowanie z panelu tego samego procesu na porcie 8768. Publiczny dostęp wymaga łączności telefonu z domeną przydzieloną przez operatora tunelu. Pokaz konkursowy usług HTTP/HTTPS nadal jest dostępny w zakładce **Demo AGH**.

### Główny panel terenowy na telefonie

Otwórz lokalny panel laptopa i sekcję **Aplikacja telefonu · parowanie i kod QR**. Wybierz **Cloudflare · aplikacja na telefon** i sparuj urządzenie. Quick Tunnel wymaga zainstalowanego cloudflared (Windows: pakiet Cloudflare.cloudflared w winget). Link online jest jednorazowy i ważny przez 15 minut, sesja po sparowaniu działa do godziny. W tej samej sekcji można pokazać aktualny kod QR lub zamknąć tunel. Dla oddzielnej próby bez historycznych danych uruchom z katalogu aplikacji `python server.py --mobile-test --port 8770`. Laptop musi pracować przez cały test.

**Mapa** pokazuje geolokalizację udostępnioną przez telefon, czas i promień dokładności. Odczyt przyjęty przez laptop trafia do aktywnej sesji w pamięci. Włączenie lokalizacji wymaga zgody w przeglądarce. Stanowisko laptopa można oznaczyć na telefonie, stojąc przy nim i zaznaczając potwierdzenie przed rozpoczęciem odczytów Wi-Fi. Serwer zapisuje skany ze stałego stanowiska co około 30 sekund do SQLite. Można też uruchomić czujnik bez współrzędnych.

**Alerty** pokazują rozbieżności z inwentarzem wybranej bazy. Przycisk **Uruchom próbę** tworzy oddzielny przykład AGH-LAB w pamięci. **Usługi AGH** udostępniają dwa lokalne przebiegi HTTP/HTTPS, różnice odpowiedzi, czas, dowody i import/eksport JSON. Pełny zakres konkursowy obejmuje dodatkowo m.in. obsługę ponad 10 000 adresów, role i szyfrowanie danych w spoczynku.
