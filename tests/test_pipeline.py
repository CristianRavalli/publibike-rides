"""Tests für die Aufbereitung — laufen ohne Netz und ohne Zugangsdaten.

    python3 -m unittest discover tests
"""

import unittest

from publibike_rides.enrich import enrich_rides, haversine_km
from publibike_rides.stations import _localized, _station_number, build_lookups

STATIONS = [
    {
        "station_id": "PIB:Station:1148",
        "station_number": "1148",
        "name": "Sihlpost - Zürich",
        "lat": "47.377365",
        "lon": "8.53586",
        "address": "",
        "post_code": "",
        "capacity": "",
    },
    {
        "station_id": "PIB:Station:1211",
        "station_number": "1211",
        "name": "Hardplatz - Zürich",
        "lat": "47.381916",
        "lon": "8.515247",
        "address": "",
        "post_code": "",
        "capacity": "",
    },
]


def ride(**overrides):
    base = {
        "id": "abc",
        "startDate": "2026-11-05 12:58:58",
        "endDate": "2026-11-05 13:05:53",
        "durationInMinute": 7,
        "usedDurationIncluded": 7,
        "creditUsed": 0,
        "startStation": "Sihlpost - Zürich",
        "endStation": "Hardplatz - Zürich",
        "endStationId": "1211",
        "vehicleType": 2,
        "vehicleNumber": "21705",
    }
    base.update(overrides)
    return base


class TestHaversine(unittest.TestCase):
    def test_gleicher_punkt_ist_null(self):
        self.assertEqual(haversine_km(47.0, 8.0, 47.0, 8.0), 0.0)

    def test_bekannte_distanz(self):
        # Sihlpost -> Hardplatz, rund 1.7 km Luftlinie
        km = haversine_km(47.377365, 8.53586, 47.381916, 8.515247)
        self.assertAlmostEqual(km, 1.63, delta=0.15)

    def test_symmetrisch(self):
        a = haversine_km(47.377365, 8.53586, 47.381916, 8.515247)
        b = haversine_km(47.381916, 8.515247, 47.377365, 8.53586)
        self.assertAlmostEqual(a, b, places=9)


class TestStationsHilfen(unittest.TestCase):
    def test_station_number_aus_gbfs_id(self):
        self.assertEqual(_station_number("PIB:Station:1148"), "1148")
        self.assertEqual(_station_number(""), "")

    def test_localized_nimmt_ersten_text(self):
        self.assertEqual(_localized([{"text": "Sihlpost", "language": "en"}]), "Sihlpost")
        self.assertEqual(_localized("Direkt"), "Direkt")
        self.assertEqual(_localized(None), "")

    def test_lookups(self):
        by_number, by_name = build_lookups(STATIONS)
        self.assertEqual(by_number["1148"]["name"], "Sihlpost - Zürich")
        self.assertEqual(by_name["Hardplatz - Zürich"]["station_number"], "1211")


class TestEnrich(unittest.TestCase):
    def test_koordinaten_werden_gesetzt(self):
        rows, report = enrich_rides([ride()], STATIONS)
        row = rows[0]
        self.assertEqual(report["mit_koordinaten"], 1)
        self.assertEqual(row["start_station_id"], "PIB:Station:1148")
        self.assertEqual(row["end_station_id"], "PIB:Station:1211")
        self.assertAlmostEqual(row["distance_km_luftlinie"], 1.63, delta=0.15)

    def test_zielstation_ueber_id_auch_bei_abweichendem_namen(self):
        # Die ID ist der stabilere Schluessel als der Name.
        rows, _ = enrich_rides(
            [ride(endStation="Hardplatz (umbenannt)", endStationId="1211")], STATIONS
        )
        self.assertEqual(rows[0]["end_station_id"], "PIB:Station:1211")

    def test_abgeleitete_zeitspalten(self):
        rows, _ = enrich_rides([ride()], STATIONS)
        row = rows[0]
        self.assertEqual(row["date"], "2026-11-05")
        self.assertEqual(row["month"], "2026-11")
        self.assertEqual(row["weekday"], "Donnerstag")
        self.assertEqual(row["hour"], 12)

    def test_dauer_kommt_aus_den_zeitstempeln(self):
        # Die API rundet auf ganze Minuten auf; gerechnet wird mit Sekunden.
        rows, _ = enrich_rides([ride(durationInMinute=99)], STATIONS)
        self.assertEqual(rows[0]["duration_seconds"], 415)
        self.assertEqual(rows[0]["duration_min"], 99)  # unveraendert mitgefuehrt

    def test_unplausibles_tempo_wird_markiert(self):
        # 1.6 km in 16 Sekunden sind offensichtlich keine gefahrene Strecke.
        rows, report = enrich_rides(
            [ride(endDate="2026-11-05 12:59:14", durationInMinute=1)], STATIONS
        )
        self.assertIn("tempo_unplausibel", rows[0]["quality_flag"])
        self.assertEqual(report["auffaellig"], 1)


class TestAbgebrocheneMieten(unittest.TestCase):
    """Velo genommen, taugte nichts, sofort zurueck und das naechste genommen."""

    @staticmethod
    def _paar(erste_start, erste_ende, zweite_start, **erste):
        """Zwei aufeinanderfolgende Mieten; die erste ist der Kandidat."""
        a = ride(
            id="erste",
            startDate=erste_start,
            endDate=erste_ende,
            endStation="Sihlpost - Zürich",
            endStationId="1148",
            **erste,
        )
        b = ride(id="zweite", startDate=zweite_start, endDate="2026-11-05 12:30:00")
        return [a, b]

    def _flag(self, rides_):
        rows, report = enrich_rides(rides_, STATIONS)
        erste = next(r for r in rows if r["ride_id"] == "erste")
        return erste["quality_flag"], report

    def test_kurze_rundfahrt_mit_sofortiger_anschlussmiete(self):
        flag, report = self._flag(
            self._paar("2026-11-05 12:00:00", "2026-11-05 12:00:35", "2026-11-05 12:00:48")
        )
        self.assertIn("miete_abgebrochen", flag)
        self.assertEqual(report["abgebrochene_mieten"], 1)

    def test_ueberlappung_zaehlt_auch(self):
        # Die naechste Miete beginnt, bevor die vorherige verbucht ist.
        flag, _ = self._flag(
            self._paar("2026-11-05 12:00:00", "2026-11-05 12:01:00", "2026-11-05 12:00:19")
        )
        self.assertIn("miete_abgebrochen", flag)

    def test_anschlussmiete_ist_kein_abbruch(self):
        # Das Velo kam vom Fleck — also kein Defekt, sondern Weiterfahrt.
        rides_ = self._paar(
            "2026-11-05 12:00:00", "2026-11-05 12:00:35", "2026-11-05 12:00:48"
        )
        rides_[0]["endStation"] = "Hardplatz - Zürich"
        rides_[0]["endStationId"] = "1211"
        flag, report = self._flag(rides_)
        self.assertNotIn("miete_abgebrochen", flag)
        self.assertEqual(report["abgebrochene_mieten"], 0)

    def test_lange_rundfahrt_ist_kein_abbruch(self):
        # 20 Minuten im Kreis sind eine Ausfahrt, kein defektes Velo.
        flag, _ = self._flag(
            self._paar("2026-11-05 12:00:00", "2026-11-05 12:20:00", "2026-11-05 12:20:30")
        )
        self.assertNotIn("miete_abgebrochen", flag)

    def test_grosse_luecke_ist_kein_abbruch(self):
        flag, _ = self._flag(
            self._paar("2026-11-05 12:00:00", "2026-11-05 12:00:35", "2026-11-05 12:30:00")
        )
        self.assertNotIn("miete_abgebrochen", flag)

    def test_luecke_wird_eingetragen(self):
        rows, _ = enrich_rides(
            self._paar("2026-11-05 12:00:00", "2026-11-05 12:00:35", "2026-11-05 12:02:35"),
            STATIONS,
        )
        erste = next(r for r in rows if r["ride_id"] == "erste")
        letzte = next(r for r in rows if r["ride_id"] == "zweite")
        self.assertEqual(erste["gap_to_next_min"], 2.0)
        self.assertEqual(letzte["gap_to_next_min"], "")  # nichts folgt mehr

    def test_unbekannte_station_bleibt_leer_statt_geraten(self):
        rows, report = enrich_rides(
            [ride(startStation="Gibt Es Nicht", endStation="Auch Nicht", endStationId="99999")],
            STATIONS,
        )
        row = rows[0]
        self.assertEqual(row["start_lat"], "")
        self.assertEqual(row["distance_km_luftlinie"], "")
        self.assertEqual(row["quality_flag"], "station_unbekannt")
        self.assertEqual(report["ohne_koordinaten"], 1)
        self.assertIn("Gibt Es Nicht", report["unbekannte_startstationen"])

    def test_rundfahrt_wird_erkannt(self):
        rows, _ = enrich_rides(
            [ride(endStation="Sihlpost - Zürich", endStationId="1148")], STATIONS
        )
        self.assertTrue(rows[0]["is_round_trip"])
        self.assertEqual(rows[0]["distance_km_luftlinie"], 0.0)


if __name__ == "__main__":
    unittest.main()
