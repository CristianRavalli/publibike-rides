# publibike-rides

Die eigene Fahrtenhistorie von **PubliBike / Velospot** als CSV herunterladen,
mit Stationskoordinaten anreichern und auf einer Karte auswerten.

Die App zeigt die Fahrten nur einzeln und bietet keinen Export. Dieses Projekt
holt sie über dieselbe Schnittstelle, die die App selbst benutzt, und macht
Zahlen und Karten daraus.

---

## Schnellstart

Du brauchst Python 3.9 oder neuer — mehr nicht. Der Download kommt ohne
zusätzliche Pakete aus.

```bash
git clone https://github.com/CristianRavalli/publibike-rides.git
cd publibike-rides
python3 -m publibike_rides
```

Das war es. Der Befehl fragt nach deiner Telefonnummer, schickt dir eine SMS,
fragt den Code ab und legt danach an:

| Datei | Inhalt |
|---|---|
| `data/rides.csv` | deine Fahrten, so wie die API sie liefert |
| `data/rides_raw.json` | dasselbe als JSON, mit allen Feldern |
| `data/stations.csv` | alle Stationen mit Koordinaten |
| `data/rides_geo.csv` | Fahrten + Koordinaten + abgeleitete Kennzahlen |

Für die Analyse reicht `data/rides_geo.csv` — die Datei lässt sich auch direkt
in Excel, Power BI oder R öffnen.

### Einzelne Schritte

```bash
python3 -m publibike_rides login      # anmelden
python3 -m publibike_rides download   # Fahrten holen
python3 -m publibike_rides stations   # Stationen holen
python3 -m publibike_rides enrich     # beides verbinden
```

Der Token wird in `.credentials.json` abgelegt (nur für dich lesbar) und gilt,
bis die App ihn verfallen lässt. Danach einfach `login` wiederholen.

---

## Analyse und Karte

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
jupyter notebook notebooks/01_analyse.ipynb
```

Das Notebook enthält:

- Überblick: Fahrten, Stunden, aktive Tage, genutzte Stationen
- Datenqualität: welche Fahrten unplausibel sind und warum
- Muster nach Monat, Wochentag und Tagesstunde
- Verteilung der Fahrtdauer, E-Bike gegen Velo
- die meistgenutzten Stationen und Strecken
- eine Karte mit Stationen (Grösse nach Nutzung) und Verbindungslinien
- eine Wärmekarte

Die Karten landen als HTML in `maps/`.

---

## Wie es funktioniert

Die App unter `web.publibike.ch` ist eine Flutter-Anwendung, die mit einem
Backend auf `velospot.info` spricht. Drei Aufrufe genügen:

| Schritt | Aufruf | Antwort |
|---|---|---|
| 1 | `POST /pbvsng/loginProcess` mit `{phoneNumber}` | `loginToken` |
| 2 | `POST /pbvsng/verifyOtpCode` mit `{otpCode, loginToken}` | `authenticationToken` |
| 3 | `GET /pbvsng/getUserRideHistory` mit Header `AuthenticationToken` | die Fahrten, seitenweise |

Jeder Aufruf braucht zusätzlich den Header `ApiAuthenticationKey`. Das ist ein
statischer Schlüssel, der die App identifiziert, nicht dich. Er steht im
öffentlich ausgelieferten JavaScript der App; `publibike_rides` liest ihn zur
Laufzeit von dort, damit er nicht im Repository liegen muss. Wer ihn lieber
selbst setzt, nutzt die Umgebungsvariable `PUBLIBIKE_API_KEY`.

### Stationskoordinaten

Die Fahrtenhistorie nennt Stationsnamen und für die Zielstation eine ID, aber
keine Koordinaten. Die kommen aus dem offenen **GBFS**-Feed für Velospot, den
MobiData BW bereitstellt (Lizenz CC-BY-4.0):

```
https://api.mobidata-bw.de/sharing/gbfs/v3/velospot_ch/station_information
```

Der Join läuft zweigleisig: die Zielstation über die ID (`endStationId` 1148
entspricht `PIB:Station:1148`), die Startstation über den Namen, weil die
Historie dafür keine ID liefert. Bei einem Testdatensatz mit 258 Fahrten über
56 verschiedene Stationen wurden alle zugeordnet.

Der eigene GBFS-Feed von PubliBike (`api.publibike.ch/v1/gbfs/...`) antwortet
Stand November 2026 mit einer leeren Stationsliste — vermutlich wegen der
Migration auf das neue Backend. Deshalb der Umweg.

---

## Zur Datenqualität

Ein paar Dinge, die beim Auswerten wichtig sind:

**Die Distanz ist Luftlinie.** Die API liefert keine GPS-Spur, nur Start- und
Zielstation. `distance_km_luftlinie` ist die gerade Verbindung zwischen beiden
Punkten — die gefahrene Strecke ist länger. Alle darauf aufbauenden Spalten
heissen deshalb ausdrücklich `luftlinie`.

**Die Dauer ist auf Minuten gerundet.** Eine Fahrt von 18 Sekunden steht als
`1` in den Daten. Endet sie an einer anderen Station, ergibt die
Geschwindigkeitsrechnung Unsinn. Solche Zeilen bekommen in `rides_geo.csv` die
Markierung `quality_flag = tempo_unplausibel` (Schwelle: 40 km/h Luftlinie) und
sollten aus Tempo-Auswertungen fliegen.

**Rundfahrten haben Distanz 0.** Start gleich Ziel heisst nicht, dass nichts
gefahren wurde — die Strecke dazwischen ist nur nicht rekonstruierbar. Die
Spalte `is_round_trip` macht sie auffindbar.

**Die Historie hat einen Anfang.** Die API lieferte im Test nur Fahrten ab
Februar 2026 zurück. Ob ältere Fahrten existieren und nicht ausgeliefert
werden, lässt sich von aussen nicht feststellen.

---

## Was nicht ins Repository gehört

`.gitignore` hält Folgendes draussen, und das ist Absicht:

- `data/` — deine Fahrten sind Bewegungsdaten. Wo du wann warst, über Monate.
- `.credentials.json` — dein Zugangstoken.
- `maps/` und ausgeführte Notebooks — sie enthalten dieselben Daten noch einmal.

Das Notebook wird **ohne Ausgaben** eingecheckt. Wenn du es ausgeführt hast und
committen willst, vorher leeren:

```bash
jupyter nbconvert --clear-output --inplace notebooks/01_analyse.ipynb
```

---

## Rechtliches und Fairness

Die Schnittstelle ist nicht dokumentiert und nicht für Dritte freigegeben. Sie
kann sich jederzeit ändern oder wegfallen; dann hört dieses Werkzeug auf zu
funktionieren.

Abgerufen werden ausschliesslich die eigenen Fahrten, mit der eigenen
Anmeldung — dieselben Daten, die die App ohnehin anzeigt. Zwischen den Seiten
liegt eine kurze Pause, damit der Abruf das Backend nicht belastet. Das
Werkzeug taugt nicht dazu, an fremde Daten zu kommen, und soll das auch nicht.

Die Stationsdaten stammen aus einem offenen Feed unter CC-BY-4.0.

---

## Projektstruktur

```
publibike_rides/
  api.py         Login und Fahrtenabruf (nur Standardbibliothek)
  stations.py    GBFS-Feed holen und normalisieren
  enrich.py      Join und abgeleitete Kennzahlen
  cli.py         Kommandozeile
notebooks/
  01_analyse.ipynb
data/            wird erzeugt, nicht eingecheckt
maps/            wird erzeugt, nicht eingecheckt
```
