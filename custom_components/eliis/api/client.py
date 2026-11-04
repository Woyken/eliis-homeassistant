"""Async HTTP client for the unofficial Eliis API.

The client is deliberately narrow: only the endpoints used by the integration
are exposed, cookies are owned per account, redirects are disabled and every
response is bounded. It has no Home Assistant dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from ..const import (
    API_APP_VERSION,
    API_BASE_URL,
    MAX_CONCURRENT_REQUESTS,
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT,
)
from .errors import (
    EliisAuthError,
    EliisConnectionError,
    EliisError,
    EliisPermissionError,
    EliisRateLimitError,
    EliisResponseError,
    EliisSchemaError,
    EliisUnsupportedError,
)

_LOGGER = logging.getLogger(__name__)

_COOKIE_NAME = "eliis_csrf"
_CHALLENGE_KEYS = (
    "login_identifier",
    "two_factor",
    "two_factor_required",
    "requires_2fa",
    "2fa_required",
    "challenge",
)


def _platform_payload(version: str, *, login: str | None = None) -> dict[str, str]:
    """Build the client platform object observed in the Android client."""
    payload = {
        "platform": "Home Assistant",
        "browser": f"Eliis HA {version}",
    }
    if login is not None:
        payload["login"] = login
    return payload


def _extract_challenge(payload: Any) -> tuple[bool, str | None]:
    """Return whether the payload is a 2FA challenge and its identifier."""
    if not isinstance(payload, dict):
        return False, None
    for key in _CHALLENGE_KEYS:
        value = payload.get(key)
        if not value:
            continue
        if key == "login_identifier":
            return True, str(value)
        if isinstance(value, dict):
            identifier = value.get("login_identifier") or value.get("identifier")
            return True, str(identifier) if identifier else None
        return True, None
    nested = payload.get("data")
    if nested is not None and nested is not payload:
        return _extract_challenge(nested)
    return False, None


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header as seconds, ignoring HTTP dates."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60.0)


class EliisClient:
    """Cookie-scoped client for Eliis.

    One instance belongs to one account. The default session is owned by the
    client, uses a dummy cookie jar and therefore cannot leak Eliis cookies
    into Home Assistant's shared session or another account.
    """

    def __init__(
        self,
        *,
        cookie: str | None = None,
        app_version: str = API_APP_VERSION,
        base_url: str = API_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client."""
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Invalid Eliis API base URL")
        self._base_url = base_url.rstrip("/")
        self._app_version = app_version
        self._cookie = cookie
        self._session = session
        self._owns_session = session is None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    @property
    def cookie(self) -> str | None:
        """Return the serialized session cookie, never logged."""
        return self._cookie

    @property
    def app_version(self) -> str:
        """Return the configured app version header."""
        return self._app_version

    async def async_close(self) -> None:
        """Close only a session owned by this client."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _async_session(self) -> aiohttp.ClientSession:
        """Return or lazily create the HTTP session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                connector=aiohttp.TCPConnector(
                    limit=MAX_CONCURRENT_REQUESTS + 1,
                ),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Eliis-HA/0.1",
                },
            )
        return self._session

    def _headers(self) -> dict[str, str]:
        headers = {"App-version": self._app_version}
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def _capture_cookie(self, response: aiohttp.ClientResponse) -> None:
        """Capture only the eligible Eliis session cookie."""
        for raw in response.headers.getall("Set-Cookie", []):
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:
                continue
            if _COOKIE_NAME in jar:
                self._cookie = f"{_COOKIE_NAME}={jar[_COOKIE_NAME].value}"

    async def _async_request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> Any:
        """Perform one bounded JSON request with at most one safe GET retry."""
        session = await self._async_session()
        url = f"{self._base_url}{path}"
        attempts = 2 if method == "GET" and allow_retry else 1

        for attempt in range(attempts):
            try:
                async with (
                    self._semaphore,
                    session.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=self._headers(),
                        allow_redirects=False,
                    ) as response,
                ):
                    self._capture_cookie(response)
                    body = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise EliisSchemaError("Eliis response exceeded the size limit")

                    status = response.status
                    if status in {301, 302, 303, 307, 308}:
                        raise EliisResponseError(
                            "Eliis returned an unexpected redirect"
                        )
                    if status == 401:
                        raise EliisAuthError("Eliis rejected the session")
                    if status == 403:
                        raise EliisPermissionError(
                            "Eliis denied access to this resource"
                        )
                    if status == 429:
                        raise EliisRateLimitError(
                            _retry_after_seconds(response.headers.get("Retry-After"))
                        )
                    if status >= 500:
                        raise EliisConnectionError("Eliis returned a server error")
                    if status >= 400:
                        raise EliisResponseError(f"Eliis returned HTTP {status}")

                    content_type = response.headers.get("Content-Type", "")
                    charset = response.charset or "utf-8"
                    text = body.decode(charset, errors="replace").strip()
                    if "html" in content_type.lower() or text.startswith("<"):
                        raise EliisSchemaError(
                            "Eliis returned an HTML page instead of JSON"
                        )
                    if not text:
                        raise EliisSchemaError("Eliis returned an empty response")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as err:
                        raise EliisSchemaError("Eliis returned malformed JSON") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt + 1 >= attempts:
                    raise EliisConnectionError(
                        "Could not reach the Eliis service"
                    ) from err
                await asyncio.sleep(0.5)
            except EliisConnectionError:
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.5)

        raise EliisConnectionError("Could not reach the Eliis service")

    # Authentication -----------------------------------------------------

    async def async_login(self, username: str, password: str) -> Any:
        """Start a password login and return a LoginResult."""
        from .models import LoginResult

        payload = {
            "username": username,
            "password": password,
            "platform": _platform_payload(self._app_version, login="password"),
        }
        data = await self._async_request_json(
            "POST", "/api/auth/login", json_body=payload, allow_retry=False
        )
        required, identifier = _extract_challenge(data)
        if required:
            if not identifier:
                raise EliisUnsupportedError(
                    "Eliis requested a challenge without a usable identifier"
                )
            return LoginResult(None, True, identifier)
        if not self._cookie:
            raise EliisSchemaError("Eliis login did not establish a session")
        return LoginResult(self._cookie, False, None)

    async def async_login_2fa(self, code: str, login_identifier: str) -> Any:
        """Complete a two-factor login challenge."""
        from .models import LoginResult

        payload = {
            "code": code,
            "login_identifier": login_identifier,
            "platform": _platform_payload(self._app_version),
        }
        data = await self._async_request_json(
            "POST", "/api/auth/login-2fa", json_body=payload, allow_retry=False
        )
        required, _ = _extract_challenge(data)
        if required:
            raise EliisAuthError("Eliis rejected the two-factor code")
        if not self._cookie:
            raise EliisAuthError("Eliis did not establish a session")
        return LoginResult(self._cookie, False, None)

    # Discovery ----------------------------------------------------------

    async def async_get_init(self) -> Any:
        """Fetch the account discovery payload."""
        platform = json.dumps(_platform_payload(self._app_version))
        return await self._async_request_json(
            "GET",
            "/api/common/init",
            params={"platform": platform},
        )

    async def async_probe_child(self, institution_id: str, child_id: str) -> bool:
        """Check whether one institution/child pair is readable.

        A denial or missing profile is a negative pairing result. Authentication
        and transport failures are not hidden.
        """
        try:
            await self._async_request_json(
                "GET",
                f"/api/kindergartens/{quote(institution_id, safe='')}"
                f"/children/{quote(child_id, safe='')}/profile",
                allow_retry=False,
            )
        except EliisPermissionError, EliisResponseError, EliisSchemaError:
            return False
        return True

    # Read-only resources ------------------------------------------------

    async def async_get_institution_events(
        self, institution_id: str, date_from: str, date_to: str
    ) -> Any:
        """Fetch institution events for an inclusive source date range."""
        return await self._async_request_json(
            "GET",
            f"/api/kindergartens/{quote(institution_id, safe='')}/events",
            params={"dateFrom": date_from, "dateTo": date_to},
        )

    async def async_get_week_menu(self, institution_id: str, day: str) -> Any:
        """Fetch the weekly menu containing the given source date."""
        return await self._async_request_json(
            "GET",
            f"/api/kindergartens/{quote(institution_id, safe='')}/foodmenu/week",
            params={"date": day},
        )

    async def async_get_child_calendar(self, institution_id: str, child_id: str) -> Any:
        """Fetch the child calendar payload."""
        return await self._async_request_json(
            "GET",
            f"/api/kindergartens/{quote(institution_id, safe='')}"
            f"/children/{quote(child_id, safe='')}/calendar",
        )

    async def async_get_guardian_feed(
        self,
        institution_id: str,
        child_id: str,
        cursor: str | None = None,
    ) -> Any:
        """Fetch one guardian-feed page, passing an opaque cursor unchanged."""
        params = {"date": cursor} if cursor is not None else None
        return await self._async_request_json(
            "GET",
            f"/api/kindergartens/{quote(institution_id, safe='')}"
            f"/children/{quote(child_id, safe='')}/guardian-feed",
            params=params,
        )

    async def async_get_status_log(
        self, institution_id: str, child_id: str, day: str
    ) -> Any:
        """Fetch status-log records for one explicit source date."""
        return await self._async_request_json(
            "GET",
            f"/api/kindergartens/{quote(institution_id, safe='')}"
            f"/children/{quote(child_id, safe='')}/status-log",
            params={"date": day},
        )

    async def async_get_unseen_notification_count(self) -> Any:
        """Fetch the unseen notification count payload."""
        return await self._async_request_json("GET", "/api/common/notifications/unseen")


__all__ = ["EliisClient", "EliisError"]
