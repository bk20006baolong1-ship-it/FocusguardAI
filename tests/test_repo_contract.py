"""BTC-feedback repository contract on SQLite (same functions run on the Firestore emulator)."""
import pytest

import repo_contract
from repository import SQLiteRepository


@pytest.fixture
def seeded():
    r = SQLiteRepository(":memory:")
    r.init_db(seed_demo_accounts=True, demo_password="pw")
    return r


@pytest.mark.parametrize("contract", repo_contract.ALL, ids=lambda f: f.__name__)
def test_sqlite_contract(seeded, contract):
    contract(seeded)


def test_old_sqlite_database_is_migrated(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT, display_name TEXT,
                         role TEXT, class_id INTEGER, student_id INTEGER, face_embedding TEXT);
                         CREATE TABLE focus_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, type TEXT,
                         timestamp TEXT);
                         CREATE TABLE consents (consent_id TEXT PRIMARY KEY, student_id INTEGER);""")
    con.commit()
    con.close()
    r = SQLiteRepository(path)
    r.init_db(seed_demo_accounts=True, demo_password="pw")
    for contract in (repo_contract.contract_focus_event_extra_fields, repo_contract.contract_consents,
                     repo_contract.contract_audit, repo_contract.contract_evaluation,
                     repo_contract.contract_face_enrollment, repo_contract.contract_cameras):
        contract(r)
