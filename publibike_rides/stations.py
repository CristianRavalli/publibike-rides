"""Stationsstammdaten mit Koordinaten aus dem GBFS-Feed von Velospot.

Die Fahrtenhistorie enthält nur Stationsnamen und für die Zielstation eine ID.
Koordinaten kommen aus dem offenen GBFS-Feed (General Bikeshare Feed
Specification), den MobiData BW für Velospot bereitstellt — Lizenz CC-BY-4.0.

Der eigene Feed von PubliBike (api.publibike.ch/v1/gbfs/...) antwortet Stand
November 2026 mit einer leeren Stationsliste, vermutlich wegen der Migration
auf das neue Backend. Deshalb dieser Umweg.
"""

from __future__ import annotations

import csv
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

GBFS_STATION_INFORMATION = (
    "https://api.mobidata-bw.de/sharing/gbfs/v3/velospot_ch/station_information"
)

USER_AGENT = "publibike-rides/1.0 (+https://github.com/CristianRavalli/publibike-rides)"

STATION_FIELDS = [
    "station_id",
    "station_number",
    "name",
    "lat",
    "lon",
    "address",
    "post_code",
    "capacity",
]


def _localized(value: Any) -> str:
    """GBFS v3 liefert Namen als Liste von {text, language}."""
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict) and entry.get("text"):
                return entry["text"]
        return ""
    return value or ""


def _station_number(station_id: str) -> str:
    """Aus 'PIB:Station:1148' wird '1148' — das passt auf endStationId."""
    if not station_id:
        return ""
    return station_id.rsplit(":", 1)[-1]


def fetch_stations(timeout: int = 60) -> List[Dict[str, Any]]:
    req = urllib.request.Request(
        GBFS_STATION_INFORMATION, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GBFS-Feed nicht erreichbar: {exc.reason}") from exc

    raw = (payload.get("data") or {}).get("stations") or []
    if not raw:
        raise RuntimeError(
            "Der GBFS-Feed hat eine leere Stationsliste geliefert. Ohne "
            "Koordinaten kann die Karte nicht gebaut werden."
        )

    stations = []
    for entry in raw:
        station_id = entry.get("station_id") or ""
        stations.append(
            {
                "station_id": station_id,
                "station_number": _station_number(station_id),
                "name": _localized(entry.get("name")),
                "lat": entry.get("lat"),
                "lon": entry.get("lon"),
                "address": entry.get("address") or "",
                "post_code": entry.get("post_code") or "",
                "capacity": entry.get("capacity") or "",
            }
        )

    stations.sort(key=lambda s: s["name"])
    return stations


def write_stations_csv(stations: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATION_FIELDS)
        writer.writeheader()
        writer.writerows(stations)


def read_stations_csv(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_lookups(stations: List[Dict[str, str]]):
    """Zwei Nachschlagewerke: über die Nummer und über den Namen.

    Die Zielstation lässt sich über endStationId auflösen, die Startstation nur
    über den Namen — die Historie liefert dafür keine ID.
    """
    by_number: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    for station in stations:
        number = str(station.get("station_number") or "")
        name = (station.get("name") or "").strip()
        if number:
            by_number.setdefault(number, station)
        if name:
            by_name.setdefault(name, station)
    return by_number, by_name
