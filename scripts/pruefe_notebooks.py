#!/usr/bin/env python3
"""Prüft, dass Notebooks ohne Ausgaben eingecheckt sind.

    python3 scripts/pruefe_notebooks.py

Ein ausgeführtes Notebook trägt die Daten, auf denen es lief, in seinen
Ausgaben mit sich — Tabellen, Achsenbeschriftungen, Stationsnamen, ganze
Karten als eingebettetes HTML. Wer es versehentlich mit Ausgaben committet,
veröffentlicht damit seine Bewegungsdaten, ohne es zu merken.

Das README verlangt deshalb, Notebooks vor dem Commit zu leeren. Diese Prüfung
sorgt dafür, dass die Regel nicht nur dort steht, sondern auch gilt. Sie läuft
in der CI bei jedem Push.
"""

from __future__ import annotations

import json
import pathlib
import sys

ORDNER = "notebooks"


def main() -> int:
    wurzel = pathlib.Path(__file__).resolve().parent.parent
    notebooks = sorted((wurzel / ORDNER).glob("*.ipynb"))

    if not notebooks:
        print(f"Keine Notebooks in {ORDNER}/ gefunden.")
        return 0

    beanstandet = []
    for pfad in notebooks:
        try:
            inhalt = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FEHLER  {pfad.name}: nicht lesbar ({exc})")
            return 1

        mit_ausgaben = [
            nummer
            for nummer, zelle in enumerate(inhalt.get("cells", []), start=1)
            if zelle.get("outputs") or zelle.get("execution_count") is not None
        ]
        if mit_ausgaben:
            beanstandet.append((pfad.name, mit_ausgaben))
        else:
            print(f"ok      {pfad.name} ({len(inhalt.get('cells', []))} Zellen, keine Ausgaben)")

    if beanstandet:
        print()
        for name, zellen in beanstandet:
            print(f"FEHLER  {name}: Ausgaben in Zelle(n) {', '.join(map(str, zellen))}")
        print()
        print("Ein ausgeführtes Notebook enthält die Daten, auf denen es lief.")
        print("Vor dem Commit leeren:")
        print("    jupyter nbconvert --clear-output --inplace notebooks/*.ipynb")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
