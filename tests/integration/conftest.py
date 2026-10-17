import importlib
import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nicegui import core

from quadra import api

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Apply every migration, in name order, to the real Supabase database."""
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url.startswith(("postgresql://", "postgres://")):
        pytest.skip("SUPABASE_DB_URL is not a PostgreSQL connection string")
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_PUBLISHABLE_KEY")):
        pytest.skip("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")
    with psycopg.connect(db_url, autocommit=True) as connection:
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            connection.execute(migration.read_text())


@pytest.fixture
def client():
    """TestClient over the real route: no fakes, create_session hits Supabase."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_app_after_nicegui_simulation(request):
    """Re-mount quadra.app once a NiceGUI simulated user has reset NiceGUI's globals.

    The simulation wipes the routes and run configuration that `ui.run_with(app)`
    set up when quadra.app was imported, which would break every later test of it.
    """
    yield
    if "user" in request.fixturenames and "quadra.app" in sys.modules:
        core.app.middleware_stack = None  # the simulation started the app; allow re-mounting
        core.app.user_middleware.clear()
        importlib.reload(sys.modules["quadra.app"])
