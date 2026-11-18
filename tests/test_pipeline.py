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

    def test_unplausibles_tempo_wird_markiert(self):
        # 1.6 km in einer Minute waeren knapp 100 km/h.
        rows, report = enrich_rides([ride(durationInMinute=1)], STATIONS)
        self.assertEqual(rows[0]["quality_flag"], "tempo_unplausibel")
        self.assertEqual(report["auffaellig"], 1)

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
