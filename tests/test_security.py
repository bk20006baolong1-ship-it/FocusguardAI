import json
import os

import pytest

from focusguard.security import (InsecureConfigurationError, build_flask_config, hash_password, is_hashed,
                                 verify_password)
from repository import SQLiteRepository

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------- production config
def test_production_requires_strong_secret():
    with pytest.raises(InsecureConfigurationError):
        build_flask_config({"FOCUSGUARD_ENV": "production"})
    with pytest.raises(InsecureConfigurationError):
        build_flask_config({"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "focusguard_ai_secret_key"})
    with pytest.raises(InsecureConfigurationError):
        build_flask_config({"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "short"})


def test_production_rejects_wildcard_cors():
    with pytest.raises(InsecureConfigurationError):
        build_flask_config({"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "k" * 48, "ALLOWED_ORIGINS": "*"})


def test_production_config_is_hardened():
    cfg = build_flask_config({"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "k" * 48,
                              "ALLOWED_ORIGINS": "https://a.example, https://b.example"})
    assert cfg["IS_PRODUCTION"]
    assert cfg["SESSION_COOKIE_SECURE"] and cfg["SESSION_COOKIE_HTTPONLY"]
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert cfg["SOCKETIO_CORS_ORIGINS"] == ["https://a.example", "https://b.example"]
    assert cfg["SEED_DEMO_ACCOUNTS"] is False


def test_development_never_uses_hardcoded_secret():
    a = build_flask_config({"FOCUSGUARD_ENV": "development"})
    b = build_flask_config({"FOCUSGUARD_ENV": "development"})
    assert a["SECRET_KEY"] != "focusguard_ai_secret_key" and len(a["SECRET_KEY"]) >= 32
    assert a["SECRET_KEY"] != b["SECRET_KEY"]
    assert a["SOCKETIO_CORS_ORIGINS"] is None   # same-origin by default


def test_app_source_has_no_hardcoded_secret_or_wildcard_cors():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "focusguard_ai_secret_key" not in src
    assert 'cors_allowed_origins="*"' not in src


# --------------------------------------------------------------------- passwords
def test_password_hashing_roundtrip():
    h = hash_password("s3cret")
    assert is_hashed(h) and h != "s3cret"
    assert verify_password(h, "s3cret") == (True, False)
    assert verify_password(h, "wrong") == (False, False)
    assert verify_password(None, "x") == (False, False)
    assert verify_password(h, "") == (False, False)


def test_legacy_plaintext_verifies_once_and_flags_rehash():
    assert verify_password("123", "123") == (True, True)
    assert verify_password("123", "124") == (False, False)


def test_password_migration_on_init_and_login():
    repo = SQLiteRepository(":memory:")
    repo.init_db()
    # simulate a legacy database row with a plaintext password
    repo._execute("INSERT INTO users (id, username, password, display_name, role) VALUES (50, 'legacy', 'oldpw', 'L', 'student')")
    assert repo.get_password_hash(50) == "oldpw"
    assert repo.migrate_plaintext_passwords() == 1
    stored = repo.get_password_hash(50)
    assert is_hashed(stored) and "oldpw" not in stored
    assert repo.authenticate_user("legacy", "oldpw")[0] == 50
    assert repo.authenticate_user("legacy", "wrong") is None


def test_login_rehashes_legacy_row_immediately():
    repo = SQLiteRepository(":memory:")
    repo.init_db()
    repo._execute("INSERT INTO users (id, username, password, display_name, role) VALUES (51, 'legacy2', 'pw2', 'L', 'student')")
    assert repo.authenticate_user("legacy2", "pw2") is not None
    assert is_hashed(repo.get_password_hash(51))


def test_new_and_updated_passwords_are_never_plaintext(repo):
    repo.init_db()
    ok, _ = repo.create_user("newuser", "plain-pw", "N", "student", 1)
    assert ok
    uid = repo.get_user_by_username("newuser")["id"]
    assert is_hashed(repo.get_password_hash(uid))
    repo.update_user(uid, "newuser", "another-pw", "N", "student", 1)
    stored = repo.get_password_hash(uid)
    assert is_hashed(stored) and "another-pw" not in stored
    assert repo.authenticate_user("newuser", "another-pw") is not None
    rows = repo._execute("SELECT password FROM users", fetch="all")
    assert all(is_hashed(r["password"]) for r in rows if r["password"])


def test_admin_user_list_never_exposes_passwords(login_as):
    users = login_as("admin").get("/api/admin/users").get_json()
    assert users and all("password" not in u for u in users)


def test_production_does_not_seed_demo_accounts():
    repo = SQLiteRepository(":memory:")
    cfg = build_flask_config({"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "k" * 48})
    repo.init_db(seed_demo_accounts=cfg["SEED_DEMO_ACCOUNTS"])
    assert repo.get_users_list() == []


# ----------------------------------------------------------------- firestore
def test_firestore_rules_deny_direct_client_access():
    rules = open(os.path.join(ROOT, "firestore.rules"), encoding="utf-8").read()
    assert "if true" not in rules
    assert "allow read, write: if false" in rules


def test_student_cannot_change_ai_thresholds(login_as, manager):
    before = manager.config.behavior.ear_threshold
    resp = login_as("hocsinh").post("/api/settings", json={"ear_threshold": 0.01})
    assert resp.get_json()["thresholds_changed"] is False
    assert manager.config.behavior.ear_threshold == before
    admin = login_as("admin")
    assert admin.post("/api/settings", json={"ear_threshold": 0.2}).get_json()["status"] == "success"
    assert manager.config.behavior.ear_threshold == 0.2


def test_register_face_rejects_non_student_target(login_as):
    admin = login_as("admin")
    resp = admin.post("/api/admin/register_face", json={"user_id": 2, "images": ["data:image/jpeg;base64,AAAA"]})
    assert resp.status_code == 400
