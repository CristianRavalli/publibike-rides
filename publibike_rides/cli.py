"""Kommandozeile: Fahrten herunterladen, Stationen holen, beides verbinden.

Aufruf ohne Argument macht alles der Reihe nach — das ist der Weg für Leute,
die nur ihr CSV wollen:

    python3 -m publibike_rides

Einzelne Schritte:

    python3 -m publibike_rides login      Anmelden (Telefonnummer + SMS-Code)
    python3 -m publibike_rides download   Fahrten holen -> data/rides.csv
    python3 -m publibike_rides stations   Stationen holen -> data/stations.csv
    python3 -m publibike_rides enrich     Verbinden -> data/rides_geo.csv
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import stat
import sys
from typing import Any, Dict, List, Optional

from .api import ApiError, PublibikeClient, discover_api_key
from .enrich import enrich_rides, read_rides_json, write_rides_csv
from .stations import fetch_stations, read_stations_csv, write_stations_csv

DATA_DIR = "data"
CREDENTIALS_FILE = ".credentials.json"

RIDES_JSON = os.path.join(DATA_DIR, "rides_raw.json")
RIDES_CSV = os.path.join(DATA_DIR, "rides.csv")
STATIONS_CSV = os.path.join(DATA_DIR, "stations.csv")
RIDES_GEO_CSV = os.path.join(DATA_DIR, "rides_geo.csv")

RAW_CSV_FIELDS = [
    "id",
    "startDate",
    "endDate",
    "durationInMinute",
    "usedDurationIncluded",
    "creditUsed",
    "invoiceNumber",
    "usedSubscription",
    "startStation",
    "endStation",
    "endStationId",
    "status",
    "vehicleType",
    "vehicleNumber",
]


# --------------------------------------------------------------- Zugangsdaten


def load_credentials() -> Dict[str, Any]:
    if not os.path.exists(CREDENTIALS_FILE):
        return {}
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(data: Dict[str, Any]) -> None:
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    # Nur für die eigene Nutzerin lesbar.
    os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def resolve_api_key(stored: Dict[str, Any]) -> str:
    key = os.environ.get("PUBLIBIKE_API_KEY") or stored.get("api_key")
    if key:
        return key
    print("Hole den API-Schlüssel der App aus dem öffentlichen App-Bundle …")
    return discover_api_key()


def build_client(require_token: bool = True) -> PublibikeClient:
    stored = load_credentials()
    api_key = resolve_api_key(stored)
    token = os.environ.get("PUBLIBIKE_TOKEN") or stored.get("auth_token")

    if require_token and not token:
        raise SystemExit(
            "Noch nicht angemeldet. Führe zuerst aus:\n"
            "    python3 -m publibike_rides login"
        )
    return PublibikeClient(api_key=api_key, auth_token=token)


# ------------------------------------------------------------------- Befehle


def _prompt_hidden(prompt: str) -> str:
    """Fragt etwas ab, ohne es im Terminal sichtbar zu machen."""
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, getpass.GetPassWarning):
        # Kein Terminal verfuegbar — dann eben mit Echo, aber mit Warnung.
        print("  (Achtung: keine verdeckte Eingabe möglich, die Eingabe ist sichtbar)")
        return input(prompt).strip()


def cmd_login(_args: argparse.Namespace) -> None:
    stored = load_credentials()
    api_key = resolve_api_key(stored)
    client = PublibikeClient(api_key=api_key)

    existing = os.environ.get("PUBLIBIKE_TOKEN") or stored.get("auth_token")
    if existing:
        client.auth_token = existing
        if client.session_valid():
            print("Die gespeicherte Anmeldung gilt noch — nichts zu tun.")
            return
        print("Die gespeicherte Anmeldung ist abgelaufen, also neu anmelden.")

    print()
    print("Anmeldung bei PubliBike/Velospot.")
    print("Du bekommst gleich eine SMS mit einem Code.")
    print()

    # Beide Eingaben laufen ohne Echo: sonst stehen Telefonnummer und Code im
    # Verlauf des Terminals und damit in Logs, Screenshots oder geteilten
    # Sitzungen. Ohne Terminal (Pipe, CI) faellt getpass auf input zurueck.
    phone = _prompt_hidden("Telefonnummer mit Landesvorwahl (z. B. +41791234567): ")
    try:
        login_token = client.start_login(phone)
    except (ApiError, ValueError) as exc:
        raise SystemExit(f"Anmeldung fehlgeschlagen: {exc}")

    print("SMS ist unterwegs.")
    code = _prompt_hidden("Code aus der SMS: ")
    try:
        token = client.verify_otp(login_token, code)
    except (ApiError, ValueError) as exc:
        raise SystemExit(f"Code nicht akzeptiert: {exc}")

    stored["auth_token"] = token
    stored["api_key"] = api_key
    save_credentials(stored)
    print()
    print(f"Angemeldet. Der Token liegt in {CREDENTIALS_FILE} (nur für dich lesbar).")
    print("Diese Datei gehört nicht in ein Repository — sie steht in .gitignore.")


def _write_raw_csv(rides: List[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rides)


def cmd_download(_args: argparse.Namespace) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    client = build_client()

    def progress(offset: int, got: int, fresh: int, total: int) -> None:
        print(f"  Offset {offset:>4}: {got:>2} geliefert, {fresh:>2} neu, {total} gesamt")

    print("Hole die Fahrtenhistorie …")
    try:
        rides = client.all_rides(progress=progress)
    except ApiError as exc:
        raise SystemExit(
            f"{exc}\n\nFalls der Token abgelaufen ist:\n"
            "    python3 -m publibike_rides login"
        )

    if not rides:
        print("Keine Fahrten gefunden.")
        return

    with open(RIDES_JSON, "w", encoding="utf-8") as handle:
        json.dump(rides, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    _write_raw_csv(rides, RIDES_CSV)

    oldest = rides[-1].get("startDate", "?")[:10]
    newest = rides[0].get("startDate", "?")[:10]
    print()
    print(f"{len(rides)} Fahrten von {oldest} bis {newest}")
    print(f"  {RIDES_CSV}")
    print(f"  {RIDES_JSON}")


def cmd_stations(_args: argparse.Namespace) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Hole die Stationsdaten aus dem GBFS-Feed …")
    stations = fetch_stations()
    write_stations_csv(stations, STATIONS_CSV)
    print(f"{len(stations)} Stationen -> {STATIONS_CSV}")


def cmd_enrich(_args: argparse.Namespace) -> None:
    for path, hint in (
        (RIDES_JSON, "python3 -m publibike_rides download"),
        (STATIONS_CSV, "python3 -m publibike_rides stations"),
    ):
        if not os.path.exists(path):
            raise SystemExit(f"{path} fehlt. Führe zuerst aus:\n    {hint}")

    rides = read_rides_json(RIDES_JSON)
    stations = read_stations_csv(STATIONS_CSV)
    rows, report = enrich_rides(rides, stations)
    write_rides_csv(rows, RIDES_GEO_CSV)

    print(f"{report['rides']} Mieten verarbeitet -> {RIDES_GEO_CSV}")
    print(f"  zusammenhängende Wege: {report['wege']}")
    print(f"  mit Koordinaten:  {report['mit_koordinaten']}")
    print(f"  ohne Koordinaten: {report['ohne_koordinaten']}")
    print()
    print("  Vorzeitig beendete Mieten:")
    print(f"    Abbruch an der Startstation:{report['abbruch_sofort']}")
    print(f"    Abbruch unterwegs:          {report['abbruch_unterwegs']}")
    print(f"    Lange Miete, sofort abgelöst: {report['neumiete_nach_langer_fahrt']}")
    for label, key in (
        ("Unbekannte Startstationen", "unbekannte_startstationen"),
        ("Unbekannte Zielstationen", "unbekannte_zielstationen"),
    ):
        if report[key]:
            print(f"  {label}: {', '.join(report[key])}")

    if report["auffaellig"]:
        print()
        print(f"  {report['auffaellig']} auffällige Fahrten (Spalte quality_flag):")
        for line in report["auffaellige_fahrten"]:
            print(f"    {line}")


def cmd_all(args: argparse.Namespace) -> None:
    stored = load_credentials()
    if not (os.environ.get("PUBLIBIKE_TOKEN") or stored.get("auth_token")):
        cmd_login(args)
        print()
    cmd_download(args)
    print()
    cmd_stations(args)
    print()
    cmd_enrich(args)
    print()
    print("Fertig. Für die Analyse: notebooks/01_analyse.ipynb")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m publibike_rides",
        description="Eigene PubliBike-/Velospot-Fahrten herunterladen und aufbereiten.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="Anmelden mit Telefonnummer und SMS-Code")
    sub.add_parser("download", help="Fahrtenhistorie holen")
    sub.add_parser("stations", help="Stationen mit Koordinaten holen")
    sub.add_parser("enrich", help="Fahrten und Stationen verbinden")
    sub.add_parser("all", help="Alle Schritte nacheinander (Standard)")

    args = parser.parse_args(argv)
    commands = {
        "login": cmd_login,
        "download": cmd_download,
        "stations": cmd_stations,
        "enrich": cmd_enrich,
        "all": cmd_all,
        None: cmd_all,
    }

    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
