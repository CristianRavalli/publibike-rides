"""Auswertung der eigenen PubliBike-Fahrten, im Browser.

Läuft als PyScript auf der Seite daneben. Die CSV-Datei wird lokal gelesen und
verlässt das Gerät nicht — es gibt keinen Server, der sie entgegennehmen
könnte.

Bewusst nur Standardbibliothek: kein pandas, kein matplotlib. Das hält die
Ladezeit bei wenigen Sekunden statt bei einer Minute, und die Diagramme
entstehen als SVG direkt aus den Zahlen.
"""

import asyncio
import csv
import io
import math
from collections import Counter, defaultdict
from datetime import datetime

from js import L, document
from pyodide.http import pyfetch
from pyscript import when

BEISPIEL = "./beispiel.csv"

AKZENT = "#6e2c50"
AKZENT_HELL = "#c39bd3"

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

MARKIERUNGEN = [
    ("defekt_sofort", "Velo lief gar nicht erst"),
    ("defekt_unterwegs", "Velo fiel unterwegs aus"),
    ("neumiete_nach_langer_fahrt", "Lange Miete, sofort abgelöst"),
    ("tempo_unplausibel", "Buchungsartefakt"),
    ("station_unbekannt", "Station ohne Koordinaten"),
]

_karte = None
_ebenen = []


# ---------------------------------------------------------------- Hilfsmittel


def _zahl(wert, standard=0.0):
    try:
        return float(wert)
    except (TypeError, ValueError):
        return standard


def _text(knoten_id):
    return document.getElementById(knoten_id)


def _zeit(wert):
    try:
        return datetime.strptime(wert, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def balken(paare, einheit="", hoehe_pro_zeile=26):
    """Ein waagrechtes Balkendiagramm als SVG.

    Erwartet eine Liste aus (Beschriftung, Wert). Breite in Prozent, damit es
    sich der Spalte anpasst; die Höhe wächst mit der Zahl der Zeilen.
    """
    if not paare:
        return "<p>Keine Daten.</p>"

    hoechst = max(w for _, w in paare) or 1
    breite = 300
    links = 92
    hoehe = len(paare) * hoehe_pro_zeile + 6

    teile = [
        f'<svg viewBox="0 0 {breite} {hoehe}" role="img" '
        f'preserveAspectRatio="xMinYMin meet">'
    ]
    for nummer, (name, wert) in enumerate(paare):
        y = nummer * hoehe_pro_zeile + 4
        laenge = (breite - links - 34) * (wert / hoechst)
        teile.append(
            f'<text x="{links - 8}" y="{y + 12}" text-anchor="end" '
            f'font-size="10" fill="currentColor" opacity="0.75">{name}</text>'
        )
        teile.append(
            f'<rect x="{links}" y="{y + 2}" width="{max(laenge, 0.8):.1f}" '
            f'height="13" rx="2.5" fill="{AKZENT}" opacity="0.85"/>'
        )
        teile.append(
            f'<text x="{links + max(laenge, 0.8) + 6:.1f}" y="{y + 12}" '
            f'font-size="10" fill="currentColor" opacity="0.6">'
            f"{wert:g}{einheit}</text>"
        )
    teile.append("</svg>")
    return "".join(teile)


# -------------------------------------------------------------------- Einlesen


def lies_csv(inhalt):
    zeilen = list(csv.DictReader(io.StringIO(inhalt)))
    if not zeilen:
        raise ValueError("Die Datei enthält keine Zeilen.")

    pflicht = {"start_date", "start_station", "end_station", "duration_seconds"}
    fehlend = pflicht - set(zeilen[0].keys())
    if fehlend:
        raise ValueError(
            "Das sieht nicht nach einer rides_geo.csv aus — es fehlen die "
            f"Spalten {', '.join(sorted(fehlend))}."
        )
    return zeilen


# ------------------------------------------------------------------ Auswertung


def kennzahlen(zeilen):
    dauern = [_zahl(z.get("duration_seconds")) for z in zeilen]
    stunden = sum(dauern) / 3600
    tage = {z.get("date") or (z.get("start_date") or "")[:10] for z in zeilen}
    wege = {z.get("journey_id") for z in zeilen if z.get("journey_id")}
    stationen = {z.get("start_station") for z in zeilen} | {z.get("end_station") for z in zeilen}
    sortiert = sorted(d for d in dauern if d > 0)
    median = sortiert[len(sortiert) // 2] / 60 if sortiert else 0

    return [
        (f"{len(zeilen)}", "Mieten"),
        (f"{len(wege) or len(zeilen)}", "Wege"),
        (f"{stunden:.0f} h", "unterwegs"),
        (f"{median:.0f} min", "Median je Miete"),
        (f"{len(tage - {''})}", "aktive Tage"),
        (f"{len(stationen - {None, ''})}", "Stationen"),
    ]


def stationsnutzung(zeilen):
    nutzung = Counter()
    koordinaten = {}
    for z in zeilen:
        for seite in ("start", "end"):
            name = z.get(f"{seite}_station")
            if not name:
                continue
            nutzung[name] += 1
            lat, lon = z.get(f"{seite}_lat"), z.get(f"{seite}_lon")
            if name not in koordinaten and lat and lon:
                koordinaten[name] = (_zahl(lat), _zahl(lon))
    return nutzung, koordinaten


def strecken(zeilen, anzahl=20):
    zaehler = Counter()
    for z in zeilen:
        von, nach = z.get("start_station"), z.get("end_station")
        if von and nach and von != nach:
            zaehler[(von, nach)] += 1
    return zaehler.most_common(anzahl)


# ------------------------------------------------------------------ Darstellung


def zeige_kacheln(zeilen):
    _text("kacheln").innerHTML = "".join(
        f'<div class="kachel"><b>{wert}</b><span>{was}</span></div>'
        for wert, was in kennzahlen(zeilen)
    )


def zeige_muster(zeilen):
    monate, tage, stunden = Counter(), Counter(), Counter()
    for z in zeilen:
        begonnen = _zeit(z.get("start_date"))
        if not begonnen:
            continue
        monate[begonnen.strftime("%Y-%m")] += 1
        tage[WOCHENTAGE[begonnen.weekday()]] += 1
        stunden[begonnen.hour] += 1

    nach_monat = [(m, monate[m]) for m in sorted(monate)]
    nach_tag = [(t, tage.get(t, 0)) for t in WOCHENTAGE]
    aktive = [s for s in range(24) if stunden.get(s)]
    spanne = range(min(aktive), max(aktive) + 1) if aktive else range(0)
    nach_stunde = [(f"{s:02d} Uhr", stunden.get(s, 0)) for s in spanne]

    _text("muster").innerHTML = "".join(
        f'<div class="diagramm"><h3>{titel}</h3>{balken(daten)}</div>'
        for titel, daten in (
            ("Mieten pro Monat", nach_monat),
            ("Mieten pro Wochentag", nach_tag),
            ("Mieten pro Tagesstunde", nach_stunde),
        )
    )


def zeige_qualitaet(zeilen):
    zaehler = Counter()
    for z in zeilen:
        for marke in (z.get("quality_flag") or "").split(";"):
            if marke:
                zaehler[marke] += 1

    markiert = sum(1 for z in zeilen if z.get("quality_flag"))
    reihen = "".join(
        f"<tr><td>{beschriftung}</td><td><code>{schluessel}</code></td>"
        f'<td class="zahl">{zaehler.get(schluessel, 0)}</td></tr>'
        for schluessel, beschriftung in MARKIERUNGEN
    )
    _text("qualitaet").innerHTML = (
        "<table><thead><tr><th>Was beobachtet wurde</th><th>Markierung</th>"
        '<th class="zahl">Mieten</th></tr></thead>'
        f"<tbody>{reihen}</tbody></table>"
        f'<p class="hinweis">{markiert} von {len(zeilen)} Mieten tragen mindestens '
        "eine Markierung. Mehrere pro Zeile sind möglich.</p>"
    )


def zeige_strecken(zeilen):
    oben = strecken(zeilen, 12)
    if not oben:
        _text("strecken").innerHTML = "<p>Keine Strecken zwischen zwei Stationen.</p>"
        return
    reihen = "".join(
        f"<tr><td>{von}</td><td>{nach}</td>"
        f'<td class="zahl">{anzahl}</td></tr>'
        for (von, nach), anzahl in oben
    )
    _text("strecken").innerHTML = (
        "<table><thead><tr><th>Von</th><th>Nach</th>"
        '<th class="zahl">Fahrten</th></tr></thead>'
        f"<tbody>{reihen}</tbody></table>"
    )


def zeige_karte(zeilen):
    global _karte, _ebenen

    nutzung, koordinaten = stationsnutzung(zeilen)
    if not koordinaten:
        _text("karte").innerHTML = (
            '<p style="padding:18px">Diese Datei enthält keine Koordinaten. '
            "Sie entstehen im Schritt <code>enrich</code>.</p>"
        )
        return

    mitte_lat = sum(p[0] for p in koordinaten.values()) / len(koordinaten)
    mitte_lon = sum(p[1] for p in koordinaten.values()) / len(koordinaten)

    if _karte is None:
        _karte = L.map("karte").setView([mitte_lat, mitte_lon], 13)
        L.tileLayer(
            "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            {"attribution": "&copy; OpenStreetMap", "maxZoom": 19},
        ).addTo(_karte)
    else:
        for ebene in _ebenen:
            _karte.removeLayer(ebene)
        _ebenen = []
        _karte.setView([mitte_lat, mitte_lon], 13)

    oben = strecken(zeilen, 20)
    hoechst = max((a for _, a in oben), default=1)
    for (von, nach), anzahl in oben:
        if von in koordinaten and nach in koordinaten:
            linie = L.polyline(
                [list(koordinaten[von]), list(koordinaten[nach])],
                {
                    "color": AKZENT,
                    "weight": 1 + 6 * anzahl / hoechst,
                    "opacity": 0.45,
                },
            )
            linie.bindTooltip(f"{von} → {nach}: {anzahl} Fahrten")
            linie.addTo(_karte)
            _ebenen.append(linie)

    groesster = max(nutzung.values()) if nutzung else 1
    for name, (lat, lon) in koordinaten.items():
        anzahl = nutzung.get(name, 0)
        kreis = L.circleMarker(
            [lat, lon],
            {
                "radius": 4 + 13 * anzahl / groesster,
                "color": AKZENT,
                "weight": 1,
                "fillColor": AKZENT_HELL,
                "fillOpacity": 0.85,
            },
        )
        kreis.bindTooltip(f"{name}: {anzahl} mal genutzt")
        kreis.addTo(_karte)
        _ebenen.append(kreis)

    _karte.invalidateSize()


# ------------------------------------------------------------------- Ablauf


def melde_fehler(nachricht):
    kasten = _text("fehler")
    kasten.innerHTML = nachricht
    kasten.style.display = "block"


def zeichne(zeilen, titel, zeile):
    _text("fehler").style.display = "none"
    _text("laden").hidden = True
    _text("inhalt").hidden = False
    _text("quelle-titel").textContent = titel
    _text("quelle-zeile").textContent = zeile

    zeige_kacheln(zeilen)
    zeige_karte(zeilen)
    zeige_muster(zeilen)
    zeige_qualitaet(zeilen)
    zeige_strecken(zeilen)


def spanne(zeilen):
    daten = sorted(z.get("start_date", "")[:10] for z in zeilen if z.get("start_date"))
    return f"{daten[0]} bis {daten[-1]}" if daten else "Zeitraum unbekannt"


async def starte():
    try:
        antwort = await pyfetch(BEISPIEL)
        zeilen = lies_csv(await antwort.string())
    except Exception as fehler:  # noqa: BLE001
        _text("laden").hidden = True
        melde_fehler(f"Beispieldaten nicht ladbar: {fehler}")
        return

    zeichne(
        zeilen,
        "Beispieldaten — erfundene Fahrten",
        f"{len(zeilen)} Mieten, {spanne(zeilen)}. Für die eigenen: Datei rechts öffnen.",
    )


@when("change", "#datei")
async def eigene_datei(event):
    dateien = event.target.files
    if not dateien or dateien.length == 0:
        return
    datei = dateien.item(0)
    try:
        zeilen = lies_csv(await datei.text())
    except Exception as fehler:  # noqa: BLE001
        melde_fehler(f"Datei nicht lesbar: {fehler}")
        return

    zeichne(
        zeilen,
        datei.name,
        f"{len(zeilen)} Mieten, {spanne(zeilen)}. Bleibt auf diesem Gerät.",
    )


asyncio.ensure_future(starte())
