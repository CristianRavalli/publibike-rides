"""Client für die Fahrtenhistorie von PubliBike/Velospot.

Die App unter https://web.publibike.ch spricht mit einem Backend auf
velospot.info. Dieses Modul bildet den Login (Telefonnummer + SMS-Code) und
den Abruf der Fahrten nach. Bewusst nur Standardbibliothek: der Download soll
ohne Installation von Paketen laufen.

Die API ist nicht dokumentiert und nicht für Dritte freigegeben. Sie kann sich
jederzeit ändern. Jede Abfrage läuft mit den Zugangsdaten der Person, die sich
anmeldet, und liefert ausschliesslich deren eigene Fahrten.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

API_BASE = "https://velospot.info/customer/public/api"
BUNDLE_URL = "https://web.publibike.ch/main.dart.js"

# Im App-Bundle stehen Basis-URL und API-Key als Paar direkt nebeneinander.
_KEY_PATTERN = re.compile(
    r'"https://velospot\.info/customer/public/api"\s*,\s*"([^"]{8,64})"'
)

PAGE_SIZE = 50
PAGE_PAUSE_SECONDS = 0.3
MAX_PAGES = 200  # Reissleine gegen Endlosschleifen

USER_AGENT = "publibike-rides/1.0 (+https://github.com/CristianRavalli/publibike-rides)"


class ApiError(RuntimeError):
    """Die API hat etwas anderes geliefert als erwartet."""


def _request(
    method: str,
    url: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Any:
    data = None
    headers = dict(headers)
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept", "application/json")
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise ApiError(
                "Die API lehnt die Anmeldung ab (HTTP 401). Der Token ist "
                "abgelaufen — melde dich neu an."
            ) from exc
        raise ApiError(f"HTTP {exc.code} bei {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Keine Verbindung zu {url}: {exc.reason}") from exc

    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Antwort von {url} ist kein JSON: {body[:200]}") from exc


def discover_api_key(timeout: int = 30) -> str:
    """Liest den statischen API-Key aus dem öffentlichen App-Bundle.

    Der Key identifiziert die App, nicht die Nutzerin. Er steht im öffentlich
    ausgelieferten JavaScript und wird hier zur Laufzeit gelesen, damit er
    nirgends im Repository liegt.
    """
    req = urllib.request.Request(BUNDLE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            bundle = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise ApiError(
            f"App-Bundle nicht erreichbar ({exc.reason}). Setze den Key von "
            "Hand über die Umgebungsvariable PUBLIBIKE_API_KEY."
        ) from exc

    match = _KEY_PATTERN.search(bundle)
    if not match:
        raise ApiError(
            "Im App-Bundle war kein API-Key zu finden — vermutlich hat sich "
            "die App geändert. Setze ihn über PUBLIBIKE_API_KEY."
        )
    return match.group(1)


class PublibikeClient:
    """Minimaler Client für Login und Fahrtenhistorie."""

    def __init__(
        self,
        api_key: str,
        auth_token: Optional[str] = None,
        language: str = "de",
        timezone: str = "Central European Standard Time",
    ) -> None:
        self.api_key = api_key
        self.auth_token = auth_token
        self.language = language
        self.timezone = timezone

    def _headers(self, authenticated: bool = False) -> Dict[str, str]:
        headers = {
            "ApiAuthenticationKey": self.api_key,
            "Accept-Language": self.language,
            "X-TIMEZONE": self.timezone,
        }
        if authenticated:
            if not self.auth_token:
                raise ApiError("Kein Token vorhanden — bitte zuerst anmelden.")
            headers["AuthenticationToken"] = self.auth_token
        return headers

    # ------------------------------------------------------------------ Login

    def start_login(self, phone_number: str) -> str:
        """Löst die SMS aus und gibt das loginToken für Schritt zwei zurück."""
        phone_number = phone_number.strip().replace(" ", "")
        if not phone_number.startswith("+"):
            raise ValueError(
                "Die Telefonnummer braucht die Landesvorwahl, z. B. +41791234567."
            )

        data = _request(
            "POST",
            f"{API_BASE}/pbvsng/loginProcess",
            self._headers(),
            {"phoneNumber": phone_number},
        )
        token = (data or {}).get("loginToken")
        if not token:
            raise ApiError(f"Keine loginToken in der Antwort: {data}")
        return token

    def verify_otp(self, login_token: str, otp_code: str) -> str:
        """Tauscht den SMS-Code gegen den AuthenticationToken."""
        digits = "".join(ch for ch in str(otp_code) if ch.isdigit())
        if not digits:
            raise ValueError("Der SMS-Code besteht aus Ziffern.")

        data = _request(
            "POST",
            f"{API_BASE}/pbvsng/verifyOtpCode",
            self._headers(),
            {"otpCode": int(digits), "loginToken": login_token},
        )
        token = (data or {}).get("authenticationToken")
        if not token:
            raise ApiError(
                "Kein authenticationToken in der Antwort — war der Code richtig? "
                f"Antwort: {data}"
            )
        self.auth_token = token
        return token

    def session_valid(self) -> bool:
        """Prüft, ob der gespeicherte Token noch gilt."""
        if not self.auth_token:
            return False
        try:
            _request("GET", f"{API_BASE}/pbvsng/session", self._headers(True))
            return True
        except ApiError:
            return False

    # ------------------------------------------------------------- Fahrtdaten

    def ride_history_page(self, offset: int, limit: int = PAGE_SIZE) -> List[dict]:
        url = (
            f"{API_BASE}/pbvsng/getUserRideHistory"
            f"?paginationOffset={offset}&paginationLimit={limit}"
        )
        data = _request("GET", url, self._headers(True))

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Falls das Backend die Liste irgendwann einpackt.
            for value in data.values():
                if isinstance(value, list):
                    return value
        raise ApiError(f"Unerwartete Antwort auf die Fahrtenabfrage: {data!r}")

    def all_rides(self, progress=None) -> List[dict]:
        """Holt alle Fahrten, dedupliziert über die Fahrt-ID.

        Der Abruf ist idempotent: gleiche Eingabe, gleiches Ergebnis, egal wie
        oft er läuft. Doppelte Seiten brechen die Schleife ab, damit ein
        ignorierter Offset nicht zur Endlosschleife wird.
        """
        rides: List[dict] = []
        seen = set()

        for page_no in range(MAX_PAGES):
            offset = page_no * PAGE_SIZE
            page = self.ride_history_page(offset, PAGE_SIZE)
            if not page:
                break

            fresh = 0
            for ride in page:
                key = ride.get("id") or json.dumps(ride, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                rides.append(ride)
                fresh += 1

            if progress:
                progress(offset, len(page), fresh, len(rides))

            if fresh == 0:
                # Der Offset wird ignoriert — weiterblättern bringt nichts mehr.
                break
            if len(page) < PAGE_SIZE:
                break
            time.sleep(PAGE_PAUSE_SECONDS)
        else:
            raise ApiError(
                f"Nach {MAX_PAGES} Seiten immer noch Daten — das sieht nach "
                "einer Endlosschleife aus. Bitte melden."
            )

        rides.sort(key=lambda r: r.get("startDate") or "", reverse=True)
        return rides
