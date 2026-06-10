"""Unit tests for HydroVuClient (no real API calls; pytest-httpx mocks)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock

from aqueduct_cloud_functions.clients import (
    HydroVuApiError,
    HydroVuAuthError,
    HydroVuClient,
)

TOKEN_URL = "https://www.hydrovu.com/public-api/oauth/token"
API_BASE = "https://www.hydrovu.com/public-api/v1"


def make_client() -> HydroVuClient:
    """Build a client with test credentials and default endpoints."""
    return HydroVuClient(client_id="cid", client_secret="csecret")


def add_token_response(httpx_mock: HTTPXMock, expires_in: int = 3600) -> None:
    """Register a successful OAuth token response."""
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={"access_token": "tok-1", "expires_in": expires_in},
    )


def test_token_request_is_form_encoded_client_credentials(
    httpx_mock: HTTPXMock,
) -> None:
    """The token POST sends grant_type and credentials form-encoded."""
    add_token_response(httpx_mock)
    httpx_mock.add_response(method="GET", url=f"{API_BASE}/locations/list", json=[])
    with make_client() as client:
        client.list_locations()

    token_request = httpx_mock.get_requests()[0]
    form = parse_qs(token_request.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": ["cid"],
        "client_secret": ["csecret"],
    }
    api_request = httpx_mock.get_requests()[1]
    assert api_request.headers["Authorization"] == "Bearer tok-1"


def test_token_is_cached_across_calls(httpx_mock: HTTPXMock) -> None:
    """Two API calls within the token lifetime trigger only one token POST."""
    add_token_response(httpx_mock)
    httpx_mock.add_response(method="GET", url=f"{API_BASE}/locations/list", json=[])
    httpx_mock.add_response(
        method="GET", url=f"{API_BASE}/sispec/friendlynames", json={}
    )
    with make_client() as client:
        client.list_locations()
        client.get_friendly_names()

    token_posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(token_posts) == 1


def test_token_is_refreshed_after_expiry(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale token triggers a fresh token POST on the next API call."""
    fake_now = [1000.0]
    monkeypatch.setattr(
        "aqueduct_cloud_functions.clients.hydrovu_client.time.monotonic",
        lambda: fake_now[0],
    )
    add_token_response(httpx_mock, expires_in=120)
    httpx_mock.add_response(method="GET", url=f"{API_BASE}/locations/list", json=[])
    add_token_response(httpx_mock)
    httpx_mock.add_response(method="GET", url=f"{API_BASE}/locations/list", json=[])
    with make_client() as client:
        client.list_locations()
        fake_now[0] += 120.0  # past expires_in - 60s margin
        client.list_locations()

    token_posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(token_posts) == 2


def test_list_locations_follows_pagination_and_flattens(
    httpx_mock: HTTPXMock, sample_locations: list[dict[str, Any]]
) -> None:
    """X-ISI-Start-Page response headers chain requests; pages are flattened."""
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{API_BASE}/locations/list",
        json=[sample_locations[0]],
        headers={"X-ISI-Start-Page": "page-2"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{API_BASE}/locations/list",
        json=[sample_locations[1]],
    )
    with make_client() as client:
        locations = client.list_locations()

    assert locations == sample_locations
    page_requests = [r for r in httpx_mock.get_requests() if r.method == "GET"]
    assert "X-ISI-Start-Page" not in page_requests[0].headers
    assert page_requests[1].headers["X-ISI-Start-Page"] == "page-2"


def test_get_location_data_passes_window_and_returns_pages(
    httpx_mock: HTTPXMock, sample_data_page: dict[str, Any]
) -> None:
    """startTime/endTime land in the query string; pages return unmerged."""
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{API_BASE}/locations/123/data?startTime=100&endTime=200",
        json=sample_data_page,
    )
    with make_client() as client:
        pages = client.get_location_data(123, start_time=100, end_time=200)

    assert pages == [sample_data_page]


def test_token_failure_raises_auth_error(httpx_mock: HTTPXMock) -> None:
    """A non-200 token response raises HydroVuAuthError."""
    httpx_mock.add_response(method="POST", url=TOKEN_URL, status_code=401)
    with make_client() as client, pytest.raises(HydroVuAuthError):
        client.list_locations()


def test_api_failure_raises_api_error(httpx_mock: HTTPXMock) -> None:
    """A non-2xx API response raises HydroVuApiError."""
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{API_BASE}/locations/123/data?startTime=100&endTime=200",
        status_code=500,
    )
    with make_client() as client, pytest.raises(HydroVuApiError):
        client.get_location_data(123, start_time=100, end_time=200)
