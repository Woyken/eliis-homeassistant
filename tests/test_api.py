"""Transport tests using an in-memory aiohttp-compatible session."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import pytest
from custom_components.eliis.api import (
    EliisAuthError,
    EliisClient,
    EliisPermissionError,
    EliisSchemaError,
)
from multidict import CIMultiDict

from .fixtures import EVENTS_PAYLOAD, INIT_PAYLOAD


class _Content:
    """Minimal response content reader."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, size: int = -1) -> bytes:
        """Return at most ``size`` bytes."""
        if size < 0:
            return self._body
        return self._body[:size]


class FakeResponse:
    """Stand-in for ``aiohttp.ClientResponse`` within a request context."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: dict[str, Any] | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = CIMultiDict(headers or {})
        self.charset = "utf-8"
        if body is not None:
            payload = json.dumps(body).encode()
        elif text is not None:
            payload = text.encode()
        else:
            payload = b""
        self.content = _Content(payload)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    """In-memory session that routes requests without opening sockets."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Record and serve one request."""
        self.requests.append({"method": method, "url": url, **kwargs})
        path = urlparse(url).path
        params = kwargs.get("params") or {}

        if path == "/api/auth/login":
            self.calls["login"] = kwargs.get("json")
            if kwargs["json"]["username"] == "challenge":
                return FakeResponse(body={"login_identifier": "challenge-token"})
            return FakeResponse(
                body={"ok": True},
                headers={"Set-Cookie": "eliis_csrf=session-cookie"},
            )
        if path == "/api/auth/login-2fa":
            self.calls["login_2fa"] = kwargs.get("json")
            return FakeResponse(
                body={"ok": True},
                headers={"Set-Cookie": "eliis_csrf=session-cookie"},
            )
        if path == "/api/common/init":
            self.calls["init_params"] = params
            self.calls["init_cookie"] = kwargs["headers"].get("Cookie")
            self.calls["app_version"] = kwargs["headers"].get("App-version")
            return FakeResponse(body=INIT_PAYLOAD)
        if path == "/api/kindergartens/1001/events":
            self.calls["event_params"] = params
            return FakeResponse(body=EVENTS_PAYLOAD)
        if path == "/api/kindergartens/1001/children/2002/guardian-feed":
            self.calls["feed_params"] = params
            return FakeResponse(body={"data": [], "next_date": None})
        if path == "/api/kindergartens/1001/children/2002/profile":
            return FakeResponse(status=403)
        if path == "/api/unauthorized":
            return FakeResponse(status=401)
        if path == "/api/forbidden":
            return FakeResponse(status=403)
        if path == "/api/html":
            return FakeResponse(
                text="<html>login</html>", headers={"Content-Type": "text/html"}
            )
        raise AssertionError(f"Unexpected request: {method} {url}")


@pytest.fixture
def api_session() -> FakeSession:
    """Return an in-memory transport session."""
    return FakeSession()


def make_client(session: FakeSession) -> EliisClient:
    """Build a client over the in-memory session."""
    return EliisClient(base_url="https://api.test", session=session)


@pytest.mark.asyncio
async def test_login_captures_cookie_and_sends_headers(api_session) -> None:
    """Login stores only the Eliis cookie and reuses it for reads."""
    client = make_client(api_session)

    result = await client.async_login("user@example.test", "secret")
    assert result.requires_2fa is False
    assert result.cookie == "eliis_csrf=session-cookie"
    assert api_session.calls["login"]["platform"]["login"] == "password"

    await client.async_get_init()
    assert api_session.calls["init_cookie"] == "eliis_csrf=session-cookie"
    assert api_session.calls["app_version"] == "2.69"
    platform = json.loads(api_session.calls["init_params"]["platform"])
    assert platform["platform"] == "Home Assistant"
    assert "login" not in platform


@pytest.mark.asyncio
async def test_two_factor_challenge(api_session) -> None:
    """A challenge identifier is required and the code completes login."""
    client = make_client(api_session)

    result = await client.async_login("challenge", "secret")
    assert result.requires_2fa is True
    assert result.login_identifier == "challenge-token"

    completed = await client.async_login_2fa("123456", result.login_identifier)
    assert completed.requires_2fa is False
    assert api_session.calls["login_2fa"]["code"] == "123456"
    assert api_session.calls["login_2fa"]["login_identifier"] == "challenge-token"


@pytest.mark.asyncio
async def test_error_categories(api_session) -> None:
    """Auth, permission and HTML errors stay distinguishable."""
    client = make_client(api_session)

    with pytest.raises(EliisAuthError):
        await client._async_request_json("GET", "/api/unauthorized")
    with pytest.raises(EliisPermissionError):
        await client._async_request_json("GET", "/api/forbidden")
    with pytest.raises(EliisSchemaError):
        await client._async_request_json("GET", "/api/html")


@pytest.mark.asyncio
async def test_range_and_cursor_queries(api_session) -> None:
    """Source date and opaque cursor parameters are passed unchanged."""
    client = make_client(api_session)

    await client.async_get_institution_events("1001", "2026-11-01", "2026-11-30")
    assert api_session.calls["event_params"] == {
        "dateFrom": "2026-11-01",
        "dateTo": "2026-11-30",
    }

    await client.async_get_guardian_feed("1001", "2002", "cursor +/=")
    assert api_session.calls["feed_params"] == {"date": "cursor +/="}


@pytest.mark.asyncio
async def test_probe_denial_is_negative_but_auth_is_not(api_session) -> None:
    """A denied profile probe means "not paired", not "session expired"."""
    client = make_client(api_session)

    assert await client.async_probe_child("1001", "2002") is False
