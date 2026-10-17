import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quadra import api

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "0001_workspace.sql"


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Apply the workspace migration to the real Supabase database."""
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url.startswith(("postgresql://", "postgres://")):
        pytest.skip("SUPABASE_DB_URL is not a PostgreSQL connection string")
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_PUBLISHABLE_KEY")):
        pytest.skip("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")
    with psycopg.connect(db_url, autocommit=True) as connection:
        connection.execute(MIGRATION.read_text())


@pytest.fixture
def client():
    """TestClient over the real route: no fakes, create_session hits Supabase."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)
