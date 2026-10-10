import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_TYPE", "sqlite")

from repository import SQLiteRepository  # noqa: E402
from session_manager import SessionManager  # noqa: E402

DEV_ENV = {"FOCUSGUARD_ENV": "development", "SEED_DEMO_ACCOUNTS": "true", "FLASK_SECRET_KEY": "x" * 40}


class FakeClock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        return self.t


@pytest.fixture
def repo():
    r = SQLiteRepository(":memory:")
    return r


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(repo, clock):
    return SessionManager(repo=repo, seed_demo_accounts=True, clock=clock)


@pytest.fixture
def app_bundle(manager):
    from app import create_app
    app, socketio = create_app(env=DEV_ENV, async_mode="threading", start_background=False,
                               session_manager=manager)
    app.config["TESTING"] = True
    return app, socketio, manager


@pytest.fixture
def app(app_bundle):
    return app_bundle[0]


def login(app, username, password="123"):
    client = app.test_client()
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code == 302, f"login failed for {username}"
    return client


@pytest.fixture
def login_as(app):
    def _login(username, password="123"):
        return login(app, username, password)
    return _login
