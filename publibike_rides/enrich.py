"""Fahrten mit Stationskoordinaten anreichern und Kennzahlen ableiten.

Hier passiert der Join zwischen Fahrtenhistorie und Stationsstammdaten sowie
die Berechnung der abgeleiteten Spalten. Der Schritt ist idempotent: er liest
die Rohdaten, schreibt eine neue Datei und verändert die Eingabe nie.

Zur Distanz: berechnet wird die Luftlinie zwischen Start- und Zielstation.
Die tatsächlich gefahrene Strecke ist länger und steckt nicht in den Daten —
die API liefert keine GPS-Spur. Spalten, die darauf aufbauen, heissen deshalb
ausdrücklich "luftlinie".
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .stations import build_lookups, read_stations_csv

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

VEHICLE_LABELS = {1: "Velo", 2: "E-Bike"}

OUTPUT_FIELDS = [
    "ride_id",
    "start_date",
    "end_date",
    "date",
    "year",
    "month",
    "weekday",
    "weekday_num",
    "hour",
    "duration_min",
    "duration_seconds",
    "gap_to_next_min",
    "included_min",
    "credit_used",
    "vehicle_type",
    "vehicle_label",
    "vehicle_number",
    "start_station",
    "start_station_id",
    "start_lat",
    "start_lon",
    "end_station",
    "end_station_id",
    "end_lat",
    "end_lon",
    "distance_km_luftlinie",
    "speed_kmh_luftlinie",
    "is_round_trip",
    "quality_flag",
]

# Oberhalb dieser Luftliniengeschwindigkeit stimmt etwas nicht.
MAX_PLAUSIBLE_SPEED_KMH = 40.0

# Abgebrochene Miete: Velo genommen, es taugte nichts, sofort zurückgestellt
# und das nächste genommen. Erkennbar an drei Dingen zusammen — die Fahrt
# endet an der Startstation, sie dauerte fast nichts, und gleich darauf
# beginnt die nächste Miete.
#
# Entscheidend ist dabei, dass das Velo nicht vom Fleck kam. Eine kurze Lücke
# allein genügt nicht: wer irgendwo andockt und sofort weiterfährt, hat eine
# Anschlussmiete, keinen Defekt. In einem Testdatensatz mit 258 Fahrten
# trennte dieses Kriterium 11 Abbrüche sauber von 33 Anschlussmieten.
MAX_ABORTED_RIDE_SECONDS = 180
MAX_GAP_TO_NEXT_SECONDS = 180


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Luftlinie zwischen zwei Punkten in Kilometern."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _add_flag(row: dict, flag: str) -> None:
    vorhanden = [f for f in row["quality_flag"].split(";") if f]
    if flag not in vorhanden:
        vorhanden.append(flag)
    row["quality_flag"] = ";".join(vorhanden)


def _mark_aborted_rentals(
    rows: List[dict], zeiten: List[Tuple[Optional[datetime], Optional[datetime]]]
) -> None:
    """Trägt die Lücke zur nächsten Miete ein und markiert Abbrüche.

    Die Lücke kann negativ sein — dann beginnt die nächste Miete, bevor die
    vorherige verbucht wurde. Das kommt vor und ist für die Erkennung kein
    Sonderfall, sondern ein besonders deutlicher Hinweis.
    """
    reihenfolge = sorted(
        (i for i, (start, _) in enumerate(zeiten) if start is not None),
        key=lambda i: zeiten[i][0],
    )

    for i, j in zip(reihenfolge, reihenfolge[1:]):
        ende_vorher = zeiten[i][1]
        start_naechste = zeiten[j][0]
        if ende_vorher is None or start_naechste is None:
            continue

        luecke = (start_naechste - ende_vorher).total_seconds()
        rows[i]["gap_to_next_min"] = round(luecke / 60, 2)

        dauer = rows[i]["duration_seconds"]
        if (
            luecke <= MAX_GAP_TO_NEXT_SECONDS
            and rows[i]["is_round_trip"]
            and isinstance(dauer, int)
            and dauer <= MAX_ABORTED_RIDE_SECONDS
        ):
            _add_flag(rows[i], "miete_abgebrochen")


def enrich_rides(
    rides: List[dict], stations: List[Dict[str, str]]
) -> Tuple[List[dict], Dict[str, Any]]:
    """Verbindet Fahrten mit Stationen und rechnet Kennzahlen aus.

    Gibt die angereicherten Zeilen zurück sowie einen Report darüber, was
    nicht zugeordnet werden konnte. Fehlende Treffer werden nicht verschwiegen
    und nicht geraten — die Koordinatenspalten bleiben dann leer.
    """
    by_number, by_name = build_lookups(stations)

    rows: List[dict] = []
    zeiten: List[Tuple[Optional[datetime], Optional[datetime]]] = []
    unmatched_start = set()
    unmatched_end = set()

    for ride in rides:
        start_name = (ride.get("startStation") or "").strip()
        end_name = (ride.get("endStation") or "").strip()
        end_id = str(ride.get("endStationId") or "").strip()

        start_station = by_name.get(start_name)
        # Zielstation zuerst über die ID — das ist der stabilere Schlüssel.
        end_station = by_number.get(end_id) or by_name.get(end_name)

        if start_name and not start_station:
            unmatched_start.add(start_name)
        if end_name and not end_station:
            unmatched_end.add(f"{end_name} (id {end_id})")

        start_lat = _as_float(start_station["lat"]) if start_station else None
        start_lon = _as_float(start_station["lon"]) if start_station else None
        end_lat = _as_float(end_station["lat"]) if end_station else None
        end_lon = _as_float(end_station["lon"]) if end_station else None

        distance = None
        if None not in (start_lat, start_lon, end_lat, end_lon):
            distance = round(haversine_km(start_lat, start_lon, end_lat, end_lon), 3)

        started = _parse_timestamp(ride.get("startDate") or "")
        ended = _parse_timestamp(ride.get("endDate") or "")
        zeiten.append((started, ended))

        # durationInMinute ist auf ganze Minuten AUFGERUNDET — eine Fahrt von
        # 19 Sekunden steht dort als 1. Die Zeitstempel sind exakt, also wird
        # alles Gerechnete daraus abgeleitet und das API-Feld nur mitgeführt.
        duration = ride.get("durationInMinute")
        duration_seconds = None
        if started and ended:
            duration_seconds = int((ended - started).total_seconds())

        speed = None
        if distance is not None and duration_seconds and duration_seconds > 0:
            speed = round(distance / (duration_seconds / 3600), 2)

        vehicle_type = ride.get("vehicleType")

        flags = []
        if speed is not None and speed > MAX_PLAUSIBLE_SPEED_KMH:
            flags.append("tempo_unplausibel")
        if not start_station or not end_station:
            flags.append("station_unbekannt")

        rows.append(
            {
                "ride_id": ride.get("id", ""),
                "start_date": ride.get("startDate", ""),
                "end_date": ride.get("endDate", ""),
                "date": started.strftime("%Y-%m-%d") if started else "",
                "year": started.year if started else "",
                "month": started.strftime("%Y-%m") if started else "",
                "weekday": WEEKDAYS_DE[started.weekday()] if started else "",
                "weekday_num": started.weekday() if started else "",
                "hour": started.hour if started else "",
                "duration_min": duration,
                "duration_seconds": duration_seconds if duration_seconds is not None else "",
                "gap_to_next_min": "",  # zweiter Durchgang, siehe unten
                "included_min": ride.get("usedDurationIncluded"),
                "credit_used": ride.get("creditUsed"),
                "vehicle_type": vehicle_type,
                "vehicle_label": VEHICLE_LABELS.get(vehicle_type, "unbekannt"),
                "vehicle_number": ride.get("vehicleNumber", ""),
                "start_station": start_name,
                "start_station_id": start_station["station_id"] if start_station else "",
                "start_lat": start_lat if start_lat is not None else "",
                "start_lon": start_lon if start_lon is not None else "",
                "end_station": end_name,
                "end_station_id": end_station["station_id"] if end_station else "",
                "end_lat": end_lat if end_lat is not None else "",
                "end_lon": end_lon if end_lon is not None else "",
                "distance_km_luftlinie": distance if distance is not None else "",
                "speed_kmh_luftlinie": speed if speed is not None else "",
                "is_round_trip": start_name == end_name and bool(start_name),
                "quality_flag": ";".join(flags),
            }
        )

    _mark_aborted_rentals(rows, zeiten)

    geocoded = sum(1 for r in rows if r["distance_km_luftlinie"] != "")
    flagged = [r for r in rows if r["quality_flag"]]
    report = {
        "rides": len(rows),
        "mit_koordinaten": geocoded,
        "ohne_koordinaten": len(rows) - geocoded,
        "unbekannte_startstationen": sorted(unmatched_start),
        "unbekannte_zielstationen": sorted(unmatched_end),
        "auffaellig": len(flagged),
        "abgebrochene_mieten": sum(
            1 for r in rows if "miete_abgebrochen" in r["quality_flag"]
        ),
        "auffaellige_fahrten": [
            f"{r['start_date'][:16]} {r['start_station']} -> {r['end_station']} "
            f"({r['duration_seconds']} s, {r['speed_kmh_luftlinie']} km/h) "
            f"[{r['quality_flag']}]"
            for r in flagged
        ],
    }
    return rows, report


def read_rides_json(path: str) -> List[dict]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} enthält keine Liste von Fahrten.")
    return data


def write_rides_csv(rows: List[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
