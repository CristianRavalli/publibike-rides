#!/usr/bin/env python3
"""Erzeugt die Bilder fürs README aus dem Beispieldatensatz.

    python3 scripts/bilder.py

Liest data/beispiel/rides_geo.csv und schreibt nach docs/. Braucht pandas,
folium und matplotlib, also die Pakete aus requirements.txt — anders als der
Download, der ohne Installation läuft.

Bewusst auf den Beispieldaten und nicht auf echten Fahrten: Was im README
steht, ist öffentlich, und Bewegungsdaten gehören nicht dorthin.
"""

from __future__ import annotations

import os
import sys

import folium
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

QUELLE = os.path.join("data", "beispiel", "rides_geo.csv")
ZIEL = "docs"

FARBE = "#6E2C50"
HELL = "#C39BD3"
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def lade() -> pd.DataFrame:
    if not os.path.exists(QUELLE):
        raise SystemExit(
            f"{QUELLE} fehlt. Zuerst:\n    python3 scripts/beispieldaten.py"
        )
    return pd.read_csv(QUELLE, parse_dates=["start_date", "end_date"])


def stationsnutzung(rides: pd.DataFrame):
    starts = rides["start_station"].value_counts()
    ziele = rides["end_station"].value_counts()
    nutzung = starts.add(ziele, fill_value=0).astype(int)

    koordinaten = {}
    for _, z in rides.iterrows():
        if pd.notna(z["start_lat"]):
            koordinaten[z["start_station"]] = (z["start_lat"], z["start_lon"])
        if pd.notna(z["end_lat"]):
            koordinaten[z["end_station"]] = (z["end_lat"], z["end_lon"])
    return nutzung, koordinaten


def karte(rides: pd.DataFrame) -> str:
    nutzung, koordinaten = stationsnutzung(rides)
    mitte = (
        sum(p[0] for p in koordinaten.values()) / len(koordinaten),
        sum(p[1] for p in koordinaten.values()) / len(koordinaten),
    )
    # OpenStreetMap statt CartoDB: Letzteres verlangt seit 2026 einen
    # API-Schlüssel, die Kacheln blieben sonst leer.
    m = folium.Map(location=mitte, zoom_start=13, tiles="OpenStreetMap")

    strecken = (
        rides[rides["start_station"] != rides["end_station"]]
        .groupby(["start_station", "end_station"])
        .size()
        .sort_values(ascending=False)
        .head(20)
    )
    hoechst = strecken.max()
    for (von, nach), anzahl in strecken.items():
        if von in koordinaten and nach in koordinaten:
            folium.PolyLine(
                [koordinaten[von], koordinaten[nach]],
                weight=1 + 7 * anzahl / hoechst,
                color=FARBE,
                opacity=0.45,
                tooltip=f"{von} → {nach}: {anzahl} Fahrten",
            ).add_to(m)

    groesster = nutzung.max()
    for name, (lat, lon) in koordinaten.items():
        anzahl = int(nutzung.get(name, 0))
        folium.CircleMarker(
            location=(lat, lon),
            radius=4 + 14 * anzahl / groesster,
            color=FARBE,
            fill=True,
            fill_color=HELL,
            fill_opacity=0.85,
            weight=1,
            tooltip=f"{name}: {anzahl} mal genutzt",
        ).add_to(m)

    pfad = os.path.join(ZIEL, "karte.html")
    m.save(pfad)
    return pfad


def karte_png(rides: pd.DataFrame) -> str:
    """Statische Karte fürs README.

    Ohne Strassenhintergrund, dafür als Bild direkt im Repository sichtbar.
    Die interaktive Fassung mit Karte liegt daneben als karte.html.
    """
    import math

    nutzung, koordinaten = stationsnutzung(rides)
    strecken = (
        rides[rides["start_station"] != rides["end_station"]]
        .groupby(["start_station", "end_station"])
        .size()
        .sort_values(ascending=False)
        .head(20)
    )

    fig, ax = plt.subplots(figsize=(9, 8))

    hoechst = strecken.max()
    for (von, nach), anzahl in strecken.items():
        if von in koordinaten and nach in koordinaten:
            (lat1, lon1), (lat2, lon2) = koordinaten[von], koordinaten[nach]
            ax.plot(
                [lon1, lon2], [lat1, lat2],
                color=FARBE, alpha=0.35, linewidth=0.6 + 5 * anzahl / hoechst,
                solid_capstyle="round", zorder=1,
            )

    groesster = nutzung.max()
    for name, (lat, lon) in koordinaten.items():
        anzahl = int(nutzung.get(name, 0))
        ax.scatter(
            lon, lat, s=60 + 900 * anzahl / groesster,
            color=HELL, edgecolor=FARBE, linewidth=1.2, zorder=2,
        )

    # Nur die meistgenutzten Stationen beschriften, sonst wird es unleserlich.
    for name in nutzung.sort_values(ascending=False).head(7).index:
        if name in koordinaten:
            lat, lon = koordinaten[name]
            ax.annotate(
                name.replace(" - Zürich", ""),
                (lon, lat), xytext=(0, 14), textcoords="offset points",
                ha="center", fontsize=9, color="#2C2C2C", zorder=3,
            )

    # Längengrade sind auf Zürichs Breite kürzer als Breitengrade.
    mittlere_breite = sum(p[0] for p in koordinaten.values()) / len(koordinaten)
    ax.set_aspect(1 / math.cos(math.radians(mittlere_breite)))

    ax.set_title(
        "Stationsnetz der Beispielfahrten\n"
        "Kreisgrösse = Nutzung, Linienstärke = Fahrten auf der Strecke",
        fontsize=11, pad=14,
    )
    ax.set_xlabel("Länge")
    ax.set_ylabel("Breite")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.15)

    plt.tight_layout()
    pfad = os.path.join(ZIEL, "karte.png")
    fig.savefig(pfad, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return pfad


def muster(rides: pd.DataFrame) -> str:
    rides = rides.copy()
    rides["minuten"] = rides["duration_seconds"] / 60

    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))

    pro_monat = rides.groupby("month").size()
    pro_monat.plot(kind="bar", ax=axes[0], color=FARBE)
    axes[0].set_title("Mieten pro Monat")
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Mieten")

    pro_tag = rides["weekday"].value_counts().reindex(WOCHENTAGE, fill_value=0)
    pro_tag.plot(kind="bar", ax=axes[1], color=FARBE)
    axes[1].set_title("Mieten pro Wochentag")
    axes[1].set_xlabel("")

    pro_stunde = rides["hour"].value_counts().sort_index().reindex(range(24), fill_value=0)
    pro_stunde.plot(kind="bar", ax=axes[2], color=FARBE)
    axes[2].set_title("Mieten pro Tagesstunde")
    axes[2].set_xlabel("Stunde")

    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    pfad = os.path.join(ZIEL, "muster.png")
    fig.savefig(pfad, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return pfad


def qualitaet(rides: pd.DataFrame) -> str:
    """Zeigt, dass beide Schwellen der Abbruch-Regel in einer Luecke liegen."""
    kette = rides[rides["gap_to_next_min"] <= 3].copy()
    kette["minuten"] = kette["duration_seconds"] / 60

    fig, axes = plt.subplots(1, 2, figsize=(13, 3.4))

    luecken = rides["gap_to_next_min"].dropna().clip(upper=12)
    axes[0].scatter(luecken, [0] * len(luecken), alpha=0.35, s=45, color=FARBE)
    axes[0].axvline(3, color="#C0392B", linestyle="--", label="Schwelle 3 Min")
    axes[0].set_xlabel("Lücke zur nächsten Miete (Minuten, bei 12 abgeschnitten)")
    axes[0].set_yticks([])
    axes[0].set_title("Gehören zwei Mieten zum selben Weg?")
    axes[0].legend(loc="upper right")

    gruppen = kette["is_round_trip"].map(
        {True: "kam nicht vom Fleck", False: "fuhr woanders hin"}
    )
    for name, teil in kette.groupby(gruppen):
        axes[1].scatter(teil["minuten"], [name] * len(teil), alpha=0.6, s=55, color=FARBE)
    axes[1].axvline(20, color="#C0392B", linestyle="--", label="Schwelle 20 Min")
    axes[1].set_xlabel("Fahrzeit der Miete (Minuten)")
    axes[1].set_title("Ab wann ist ein Defekt nicht mehr die naheliegende Erklärung?")
    axes[1].legend(loc="lower right")

    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    pfad = os.path.join(ZIEL, "qualitaet.png")
    fig.savefig(pfad, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return pfad


def main() -> int:
    os.makedirs(ZIEL, exist_ok=True)
    rides = lade()
    print(f"{len(rides)} Mieten aus {QUELLE}")
    for pfad in (karte(rides), karte_png(rides), muster(rides), qualitaet(rides)):
        print(f"  {pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
