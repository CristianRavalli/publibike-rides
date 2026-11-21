#!/usr/bin/env python3
"""Erzeugt einen synthetischen Beispieldatensatz in data/beispiel/.

Damit lässt sich das Repository ausprobieren, ohne ein PubliBike-Konto zu
haben und ohne dass jemand seine Bewegungsdaten herausgeben muss. Die
Stationen und ihre Koordinaten sind echt — sie kommen aus dem offenen
GBFS-Feed. Die Fahrten sind erfunden.

Der Zufallsstartwert ist fest, der Lauf also reproduzierbar: zweimal
ausgeführt entsteht zweimal derselbe Datensatz.

    python3 scripts/beispieldaten.py

Eingebaut sind bewusst die Muster, die die Pipeline erkennen soll —
Pendelverkehr mit schwachem Mittwoch, abgebrochene Mieten beider Arten und
ein Zurücksetzen der Gratiszeit. Wer die Regeln prüfen will, hat damit einen
Datensatz, bei dem die richtige Antwort bekannt ist.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from publibike_rides.enrich import (  # noqa: E402
    enrich_rides,
    haversine_km,
    write_rides_csv,
)
from publibike_rides.stations import (  # noqa: E402
    fetch_stations,
    read_stations_csv,
    write_stations_csv,
)

ZIEL = os.path.join("data", "beispiel")
SEED = 20261121

START_TAG = datetime(2026, 3, 2)   # ein Montag
END_TAG = datetime(2026, 11, 6)

# Die Strecken, die eine erfundene Pendlerin fährt. Namen müssen im
# GBFS-Feed vorkommen; geprüft wird das beim Erzeugen.
#
# Bewusst das Quartier Oerlikon im Norden: Diese Stationen sind echt, aber
# sie stehen in keinem Zusammenhang mit tatsächlichen Fahrten. Ein
# Beispieldatensatz, der die eigene Pendelachse nachzeichnet, wäre keiner —
# er würde genau das verraten, was das Projekt schützen soll.
ZUHAUSE = "Schwamendingerplatz - Zürich"
ARBEIT = "Bahnhof Oerlikon - Zürich"
WEITERE = [
    "Marktplatz - Zürich",
    "Gubelstrasse - Zürich",
    "Messe/Hallenstadion - Zürich",
    "Berninaplatz - Zürich",
    "Wahlenpark - Zürich",
    "Oerliker Park - Zürich",
    "Bad Allenmoos - Zürich",
    "Kirche Unterstrass - Zürich",
    "Letzistrasse - Zürich",
    "Airgate - Leutschenbach - Zürich",
]

# Wie oft an einem Wochentag überhaupt gefahren wird. Der Freitag ist
# Homeoffice, am Wochenende fährt sie selten.
FAHRWAHRSCHEINLICHKEIT = {0: 0.80, 1: 0.85, 2: 0.78, 3: 0.75, 4: 0.15, 5: 0.10, 6: 0.08}

TEMPO_KMH = 12.0  # Luftlinie, daraus wird die Fahrzeit geschätzt

ABO = "BusinessBike Local Zürich - Stadt Zürich"


def _kennung(rng: random.Random) -> str:
    zeichen = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return "".join(rng.choice(zeichen) for _ in range(30)) + "=="


def _fahrzeit_sekunden(km: float, rng: random.Random) -> int:
    """Fahrzeit aus Luftlinie, mit Umwegen und Ampeln."""
    minuten = (km / TEMPO_KMH) * 60 * rng.uniform(1.15, 1.6)
    return max(60, int(minuten * 60))


def _miete(
    rng: random.Random,
    start_zeit: datetime,
    dauer_s: int,
    von: str,
    nach: str,
    stationen: Dict[str, dict],
    credit: int = 0,
) -> dict:
    ende = start_zeit + timedelta(seconds=dauer_s)
    # Die API rundet die Dauer auf ganze Minuten AUF — genau das bildet der
    # Beispieldatensatz nach, damit die Pipeline daran geprüft werden kann.
    minuten = -(-dauer_s // 60)
    ebike = rng.random() < 0.9
    return {
        "id": _kennung(rng),
        "startDate": start_zeit.strftime("%Y-%m-%d %H:%M:%S"),
        "endDate": ende.strftime("%Y-%m-%d %H:%M:%S"),
        "durationInMinute": minuten,
        "usedDurationIncluded": 0 if credit else minuten,
        "creditUsed": credit,
        "invoiceId": None,
        "invoiceNumber": "-",
        "usedSubscription": ABO,
        "startStation": von,
        "endStation": nach,
        "endStationId": stationen[nach]["station_number"],
        "status": "completed",
        "vehicleId": _kennung(rng),
        "vehicleType": 2 if ebike else 1,
        "vehicleNumber": str(rng.randint(21500, 23700) if ebike else rng.randint(40400, 41400)),
    }


def _km(a: str, b: str, stationen: Dict[str, dict]) -> float:
    return haversine_km(
        float(stationen[a]["lat"]), float(stationen[a]["lon"]),
        float(stationen[b]["lat"]), float(stationen[b]["lon"]),
    )


def _weg(
    rng: random.Random,
    abfahrt: datetime,
    von: str,
    nach: str,
    stationen: Dict[str, dict],
) -> List[dict]:
    """Ein Weg von A nach B — meist eine Miete, manchmal mehrere.

    Mit kleiner Wahrscheinlichkeit geht etwas schief: das Velo läuft gar
    nicht erst, oder es fällt unterwegs aus. Beides erzeugt eine zusätzliche
    Miete, die unmittelbar auf die vorherige folgt.
    """
    mieten: List[dict] = []
    jetzt = abfahrt

    # Velo am Dock defekt: kurze Miete, die an der Startstation endet.
    if rng.random() < 0.055:
        dauer = rng.randint(18, 90)
        mieten.append(_miete(rng, jetzt, dauer, von, von, stationen))
        jetzt += timedelta(seconds=dauer + rng.randint(6, 45))

    gesamt_km = _km(von, nach, stationen)
    gesamt_s = _fahrzeit_sekunden(gesamt_km, rng)

    # Velo fällt unterwegs aus: an einer Zwischenstation abstellen und das
    # nächste nehmen.
    if rng.random() < 0.11 and gesamt_s > 300:
        zwischen = rng.choice([s for s in WEITERE if s not in (von, nach)])
        teil1 = _fahrzeit_sekunden(_km(von, zwischen, stationen), rng)
        if teil1 < 1100:  # sonst wäre es das Zurücksetzen der Gratiszeit
            mieten.append(_miete(rng, jetzt, teil1, von, zwischen, stationen))
            jetzt += timedelta(seconds=teil1 + rng.randint(7, 60))
            teil2 = _fahrzeit_sekunden(_km(zwischen, nach, stationen), rng)
            mieten.append(_miete(rng, jetzt, teil2, zwischen, nach, stationen))
            return mieten

    mieten.append(_miete(rng, jetzt, gesamt_s, von, nach, stationen))
    return mieten


def erzeuge(stationen: Dict[str, dict]) -> List[dict]:
    rng = random.Random(SEED)
    mieten: List[dict] = []

    tag = START_TAG
    while tag <= END_TAG:
        if rng.random() < FAHRWAHRSCHEINLICHKEIT[tag.weekday()]:
            # Hinweg am Morgen
            los = tag.replace(hour=7, minute=0) + timedelta(minutes=rng.randint(0, 95))
            mieten += _weg(rng, los, ZUHAUSE, ARBEIT, stationen)

            # Mittags manchmal eine Besorgung
            if rng.random() < 0.35:
                ziel = rng.choice(WEITERE)
                mittag = tag.replace(hour=11, minute=0) + timedelta(minutes=rng.randint(0, 150))
                mieten += _weg(rng, mittag, ARBEIT, ziel, stationen)
                zurueck = mittag + timedelta(minutes=rng.randint(20, 90))
                mieten += _weg(rng, zurueck, ziel, ARBEIT, stationen)

            # Rückweg am Abend
            heim = tag.replace(hour=16, minute=0) + timedelta(minutes=rng.randint(0, 170))
            mieten += _weg(rng, heim, ARBEIT, ZUHAUSE, stationen)
        tag += timedelta(days=1)

    # Eine lange Fahrt, bei der die Gratiszeit vor den freien 30 Minuten
    # zurückgesetzt wird. Kein Defekt, aber derselbe Weg.
    umweg = WEITERE[2]
    lang = datetime(2026, 7, 14, 18, 12, 0)
    dauer1 = 1520
    mieten.append(_miete(rng, lang, dauer1, ARBEIT, umweg, stationen))
    weiter = lang + timedelta(seconds=dauer1 + 25)
    mieten.append(_miete(rng, weiter, 540, umweg, ZUHAUSE, stationen))

    # Zwei Fahrten, bei denen Guthaben verbraucht wurde.
    for wann in (datetime(2026, 5, 20, 19, 5, 0), datetime(2026, 9, 3, 13, 40, 0)):
        mieten.append(_miete(rng, wann, 2250, ARBEIT, WEITERE[5], stationen, credit=5))

    mieten.sort(key=lambda m: m["startDate"], reverse=True)
    return mieten


def main() -> int:
    print("Hole die Stationen aus dem GBFS-Feed …")
    alle = fetch_stations()
    nach_name = {s["name"]: s for s in alle}

    gebraucht = [ZUHAUSE, ARBEIT] + WEITERE
    fehlend = [n for n in gebraucht if n not in nach_name]
    if fehlend:
        print(f"Diese Stationen gibt es im Feed nicht mehr: {fehlend}")
        return 1

    stationen = {n: nach_name[n] for n in gebraucht}

    mieten = erzeuge(stationen)
    os.makedirs(ZIEL, exist_ok=True)

    with open(os.path.join(ZIEL, "rides_raw.json"), "w", encoding="utf-8") as f:
        json.dump(mieten, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Nur die verwendeten Stationen, damit das Beispiel ohne Netz läuft.
    write_stations_csv(
        sorted(stationen.values(), key=lambda s: s["name"]),
        os.path.join(ZIEL, "stations.csv"),
    )

    print(f"{len(mieten)} Mieten -> {ZIEL}/rides_raw.json")
    print(f"{len(stationen)} Stationen -> {ZIEL}/stations.csv")
    print(f"Zeitraum: {mieten[-1]['startDate'][:10]} bis {mieten[0]['startDate'][:10]}")

    # Gleich durch dieselbe Pipeline schicken wie echte Daten, damit der
    # Beispielordner vollständig ist und ohne Netz benutzbar.
    zeilen, bericht = enrich_rides(mieten, read_stations_csv(os.path.join(ZIEL, "stations.csv")))
    write_rides_csv(zeilen, os.path.join(ZIEL, "rides_geo.csv"))

    print()
    print(f"{bericht['rides']} Mieten -> {bericht['wege']} Wege -> {ZIEL}/rides_geo.csv")
    print(f"  Velo lief gar nicht erst:  {bericht['defekt_sofort']}")
    print(f"  Velo fiel unterwegs aus:   {bericht['defekt_unterwegs']}")
    print(f"  Gratiszeit zurückgesetzt:  {bericht['gratiszeit_zurueckgesetzt']}")
    print(f"  ohne Koordinaten:          {bericht['ohne_koordinaten']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
