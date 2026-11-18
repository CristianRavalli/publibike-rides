"""Eigene PubliBike-/Velospot-Fahrten herunterladen, anreichern, analysieren."""

__version__ = "1.0.0"

from .api import ApiError, PublibikeClient, discover_api_key
from .enrich import enrich_rides, haversine_km
from .stations import fetch_stations

__all__ = [
    "ApiError",
    "PublibikeClient",
    "discover_api_key",
    "enrich_rides",
    "haversine_km",
    "fetch_stations",
]
