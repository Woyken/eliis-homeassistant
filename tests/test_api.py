"""Transport tests using a local aiohttp test server."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from custom_components.eliis.api import (
    EliisAuthError,
    EliisClient,
    EliisPermissionError,
    EliisSchemaError,
)

from .fixtures import EVENTS_PAYLOAD, INIT_PAYLOAD


@pytest.fixture
async def api_server(aiohttp_client, socket_enabled):
    """Start a local Eliis-shaped HTTP server."""
    calls: dict[str, object] = {}

    async def login(request: web.Request) -> web.Response:
        payload = await request.json()
        calls["login"] = payload
        if payload.get("username") == "challenge":
            return web.json_response({"login_identifier": "challenge-token"})
        response = web.json_response({"ok": True})
        response.set_cookie("eliis_csrf", "session-cookie")
        return response

    async def login_challenge(request: web.Request) -> web.Response:
        return web.json_response({"login_identifier": "challenge-token"})

    async def login_2fa(request: web.Request) -> web.Response:
        calls["login_2fa"] = await request.json()
        response = web.json_response({"ok": True})
        response.set_cookie("eliis_csrf", "session-cookie")
        return response

    async def init(request: web.Request) -> web.Response:
        calls["init_params"] = dict(request.query)
        calls["init_cookie"] = request.headers.get("Cookie")
        calls["app_version"] = request.headers.get("App-version")
        return web.json_response(INIT_PAYLOAD)

    async def events(request: web.Request) -> web.Response:
        calls["event_params"] = dict(request.query)
        return web.json_response(EVENTS_PAYLOAD)

    async def feed(request: web.Request) -> web.Response:
        calls["feed_params"] = dict(request.query)
        return web.json_response({"data": [], "next_date": None})

    async def unauthorized(request: web.Request) -> web.Response:
        return web.Response(status=401)

    async def forbidden(request: web.Request) -> web.Response:
        return web.Response(status=403)

    async def html(request: web.Request) -> web.Response:
        return web.Response(text="<html>login</html>", content_type="text/html")

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/auth/challenge", login_challenge)
    app.router.add_post("/api/auth/login-2fa", login_2fa)
    app.router.add_get("/api/common/init", init)
    app.router.add_get("/api/kindergartens/1001/events", events)
    app.router.add_get("/api/kindergartens/1001/children/2002/guardian-feed", feed)
    app.router.add_get("/api/unauthorized", unauthorized)
    app.router.add_get("/api/forbidden", forbidden)
    app.router.add_get("/api/html", html)

    test_client = await aiohttp_client(app)
    base_url = str(test_client.make_url("")).rstrip("/")
    return test_client, base_url, calls


def make_client(test_client, base_url: str) -> EliisClient:
    """Build a client over the test session."""
    return EliisClient(base_url=base_url, session=test_client.session)


@pytest.mark.asyncio
async def test_login_captures_cookie_and_sends_headers(api_server) -> None:
    """Login stores only the Eliis cookie and reuses it for reads."""
    test_client, base_url, calls = api_server
    client = make_client(test_client, base_url)

    result = await client.async_login("user@example.test", "secret")
    assert result.requires_2fa is False
    assert result.cookie == "eliis_csrf=session-cookie"
    assert calls["login"]["platform"]["login"] == "password"

    await client.async_get_init()
    assert calls["init_cookie"] == "eliis_csrf=session-cookie"
    assert calls["app_version"] == "2.69"
    platform = json.loads(calls["init_params"]["platform"])
    assert platform["platform"] == "Home Assistant"
    assert "login" not in platform


@pytest.mark.asyncio
async def test_two_factor_challenge(api_server) -> None:
    """A challenge identifier is required and the code completes login."""
    test_client, base_url, calls = api_server
    client = make_client(test_client, base_url)

    result = await client.async_login("challenge", "secret")
    assert result.requires_2fa is True
    assert result.login_identifier == "challenge-token"

    completed = await client.async_login_2fa("123456", result.login_identifier)
    assert completed.requires_2fa is False
    assert calls["login_2fa"]["code"] == "123456"
    assert calls["login_2fa"]["login_identifier"] == "challenge-token"


@pytest.mark.asyncio
async def test_error_categories(api_server) -> None:
    """Auth, permission and HTML errors stay distinguishable."""
    test_client, base_url, _ = api_server
    client = make_client(test_client, base_url)

    with pytest.raises(EliisAuthError):
        await client._async_request_json("GET", "/api/unauthorized")
    with pytest.raises(EliisPermissionError):
        await client._async_request_json("GET", "/api/forbidden")
    with pytest.raises(EliisSchemaError):
        await client._async_request_json("GET", "/api/html")


@pytest.mark.asyncio
async def test_range_and_cursor_queries(api_server) -> None:
    """Source date and opaque cursor parameters are passed unchanged."""
    test_client, base_url, calls = api_server
    client = make_client(test_client, base_url)

    await client.async_get_institution_events("1001", "2026-11-01", "2026-11-30")
    assert calls["event_params"] == {
        "dateFrom": "2026-11-01",
        "dateTo": "2026-11-30",
    }

    await client.async_get_guardian_feed("1001", "2002", "cursor +/=")
    assert calls["feed_params"] == {"date": "cursor +/="}


@pytest.mark.asyncio
async def test_probe_denial_is_negative_but_auth_is_not(api_server) -> None:
    """A denied profile probe means "not paired", not "session expired"."""
    test_client, base_url, _ = api_server
    client = make_client(test_client, base_url)

    assert await client.async_probe_child("1001", "2002") is False
