from __future__ import annotations

from typing import Iterable

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from orchestrator.server import app as app_runtime


RoutePair = tuple[str, str]


ADMIN_ROUTES: frozenset[RoutePair] = frozenset(
    {
        ("/api/auth/login", "POST"),
        ("/api/auth/forgot-password", "POST"),
        ("/api/conversations", "GET"),
        ("/api/conversations", "POST"),
        ("/api/conversations/{conv_id}/messages", "GET"),
        ("/api/conversations/{conv_id}", "DELETE"),
        ("/api/conversations/{conv_id}", "PATCH"),
        ("/api/conversations/{conv_id}/share", "POST"),
        ("/api/shares/{token}", "DELETE"),
        ("/api/tickets", "GET"),
        ("/api/tickets", "POST"),
        ("/api/tickets/{ticket_id}", "PATCH"),
        ("/api/tickets/{ticket_id}/events", "POST"),
        ("/api/plots/{filename}", "GET"),
        ("/api/analysis-artifacts/{filename}", "GET"),
        ("/api/conversations/{conv_id}/status", "GET"),
        ("/api/conversations/{conv_id}/cancel", "POST"),
        ("/api/conversations/{conv_id}/chat", "POST"),
        ("/api/streams/{stream_id}", "GET"),
        ("/api/streams/{stream_id}/poll", "GET"),
    }
)

SALES_ROUTES: frozenset[RoutePair] = frozenset(
    {
        ("/api/public/sales/conversations", "POST"),
        ("/api/public/sales/conversations", "GET"),
        ("/api/public/sales/conversations/{conv_id}", "GET"),
        ("/api/public/sales/conversations/{conv_id}", "PATCH"),
        ("/api/public/sales/conversations/{conv_id}", "DELETE"),
        ("/api/public/sales/conversations/{conv_id}/status", "GET"),
        ("/api/public/sales/conversations/{conv_id}/share", "POST"),
        ("/api/public/sales/shares/{token}", "DELETE"),
        ("/api/public/sales/conversations/{conv_id}/cancel", "POST"),
        ("/api/public/sales/conversations/{conv_id}/chat", "POST"),
        ("/api/public/sales/streams/{stream_id}", "GET"),
        ("/api/public/sales/streams/{stream_id}/poll", "GET"),
    }
)

SHARED_ROUTES: frozenset[RoutePair] = frozenset(
    {
        ("/api/config", "GET"),
        ("/api/public/shares/{token}", "GET"),
        ("/api/logo", "GET"),
    }
)

SPA_ROUTES: frozenset[RoutePair] = frozenset(
    {
        ("/", "GET"),
        ("/{full_path:path}", "GET"),
    }
)


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "host_modes.db"))
    monkeypatch.delenv("BLUEBOT_HOST_MODE", raising=False)
    monkeypatch.delenv("BLUEBOT_SERVE_SPA", raising=False)

    spa_dist = tmp_path / "dist"
    spa_dist.mkdir()
    (spa_dist / "index.html").write_text(
        "<!doctype html><title>bluebot</title>",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_runtime, "_FRONTEND_DIST", spa_dist)

    app_runtime.store._bootstrapped.clear()
    app_runtime.store._ensure_ready()
    yield app_runtime.create_app
    app_runtime.store._bootstrapped.clear()


def _route_pairs(app) -> frozenset[RoutePair]:
    return frozenset(
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    )


def _paths(app) -> frozenset[str]:
    return frozenset(path for path, _method in _route_pairs(app))


def _assert_surface(app, expected: Iterable[RoutePair]) -> None:
    assert _route_pairs(app) == frozenset(expected)


def _assert_spa_fallback_last(app) -> None:
    assert app.routes[-2].path == "/"
    assert app.routes[-1].path == "/{full_path:path}"

    client = TestClient(app)
    response = client.get("/api/not-a-real-route")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_combined_mode_exposes_full_surface(app_factory):
    app = app_factory(mode="combined")

    _assert_surface(app, ADMIN_ROUTES | SALES_ROUTES | SHARED_ROUTES | SPA_ROUTES)
    _assert_spa_fallback_last(app)


def test_admin_mode_exposes_admin_and_shared_only(app_factory):
    app = app_factory(mode="admin")

    _assert_surface(app, ADMIN_ROUTES | SHARED_ROUTES | SPA_ROUTES)
    _assert_spa_fallback_last(app)

    client = TestClient(app)
    assert (
        client.get("/api/conversations", params={"user_id": "test"}).status_code
        == 200
    )
    assert client.get("/api/public/sales/conversations").status_code == 404
    assert client.get("/api/config").status_code == 200

    share_response = client.get("/api/public/shares/not-a-token")
    assert share_response.status_code == 404
    assert "detail" in share_response.json()

    assert client.get("/").status_code == 200


def test_sales_mode_exposes_sales_and_shared_only(app_factory):
    app = app_factory(mode="sales")

    _assert_surface(app, SALES_ROUTES | SHARED_ROUTES)
    assert "/" not in _paths(app)
    assert "/{full_path:path}" not in _paths(app)

    client = TestClient(app)
    assert client.get("/api/conversations").status_code in (404, 405)
    assert client.get("/api/auth/login").status_code == 404
    assert (
        client.get("/api/public/sales/conversations", params={"ids": ""}).status_code
        == 200
    )
    assert client.get("/api/config").status_code == 200
    assert client.get("/").status_code == 404


def test_serve_spa_override(app_factory):
    sales_app = app_factory(mode="sales", serve_spa=True)
    _assert_surface(sales_app, SALES_ROUTES | SHARED_ROUTES | SPA_ROUTES)
    _assert_spa_fallback_last(sales_app)
    assert TestClient(sales_app).get("/").status_code == 200

    admin_app = app_factory(mode="admin", serve_spa=False)
    _assert_surface(admin_app, ADMIN_ROUTES | SHARED_ROUTES)
    assert "/" not in _paths(admin_app)
    assert "/{full_path:path}" not in _paths(admin_app)
    assert TestClient(admin_app).get("/").status_code == 404


def test_invalid_mode_raises(app_factory):
    with pytest.raises(RuntimeError):
        app_factory(mode="bogus")


def test_env_var_resolution(app_factory, monkeypatch):
    monkeypatch.setenv("BLUEBOT_HOST_MODE", "admin")
    _assert_surface(app_factory(), ADMIN_ROUTES | SHARED_ROUTES | SPA_ROUTES)

    monkeypatch.setenv("BLUEBOT_HOST_MODE", "sales")
    _assert_surface(app_factory(), SALES_ROUTES | SHARED_ROUTES)

    monkeypatch.delenv("BLUEBOT_HOST_MODE", raising=False)
    _assert_surface(
        app_factory(),
        ADMIN_ROUTES | SALES_ROUTES | SHARED_ROUTES | SPA_ROUTES,
    )


def test_explicit_mode_beats_env_var(app_factory, monkeypatch):
    monkeypatch.setenv("BLUEBOT_HOST_MODE", "sales")

    app = app_factory(mode="admin")

    _assert_surface(app, ADMIN_ROUTES | SHARED_ROUTES | SPA_ROUTES)
    assert "/api/conversations" in _paths(app)
    assert "/api/public/sales/conversations" not in _paths(app)
