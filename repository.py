"""Persistence layer (SQLite for local/CI, Firestore for deployment).

Normalized model:

    users            accounts (password stored as a Werkzeug hash)
    classes
    class_members    (class_id, user_id, member_role) teacher/student membership
    sessions         personal self-study sessions
    class_sessions   one row per class run (mode: classroom | online)
    session_students per-student outcome of a class/personal session
    focus_events     behaviour episodes: student_id, class_session_id/session_id,
                     start/end, duration, confidence, source, metadata
    focus_snapshots  periodic focus score samples

Analytics never read ``history_json``; they are computed by
``focusguard.analytics`` from the tables above. No synthetic sessions are
seeded here. Only non-production environments seed demo *accounts*.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from focusguard.security import env_value, hash_password, is_hashed, verify_password


def load_dotenv(path=".env"):
    """Minimal .env loader (does not override variables already set)."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip().strip("'\""))
    except OSError as e:
        print(f"Error loading .env file: {e}")


load_dotenv()

VALID_ROLES = ("admin", "teacher", "student", "parent")

DEMO_CLASSES = [(1, "10A1"), (2, "10A2"), (3, "10A3")]
# Demo accounts for local development only (never seeded in production).
DEMO_USERS = [
    # id, username, display_name, role, class_id, student_id
    (1, "admin", "Quản trị viên", "admin", None, None),
    (2, "teacher", "Thầy Minh", "teacher", 1, None),
    (3, "hocsinh", "Nguyễn Văn An", "student", 1, None),
    (4, "diyasharma", "Trần Thị Bình", "student", 1, None),
    (5, "rohanverma", "Lê Hoàng Giang", "student", 1, None),
    (6, "ananyapatel", "Phạm Minh Hải", "student", 1, None),
    (7, "vivaankapoor", "Vũ Tiến Khoa", "student", 1, None),
    (8, "meeranair", "Ngô Khánh Linh", "student", 1, None),
    (9, "arjunsingh", "Hoàng Đức Minh", "student", 1, None),
    (10, "ishitayadav", "Bùi Thị Ngọc", "student", 1, None),
    (11, "krishjain", "Đỗ Minh Quân", "student", 1, None),
    (100, "phuhuynh", "Phụ huynh Nguyễn Văn An", "parent", None, 3),
]


def _now_str(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d %H:%M:%S")


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _json_or_empty(raw):
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


class BaseRepository:
    """Interface shared by both backends (see SQLiteRepository for semantics)."""


# =============================================================================
# SQLite
# =============================================================================
class SQLiteRepository(BaseRepository):
    def __init__(self, db_path=None):
        self.db_path = db_path or os.environ.get("SQLITE_PATH", "focusguard.db")
        self._lock = threading.RLock()
        self._memory_conn = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row

    # ----------------------------------------------------------- connection
    def _conn(self):
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn):
        if conn is not self._memory_conn:
            conn.close()

    def _execute(self, sql, params=(), fetch=None, many=False):
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.cursor()
                if many:
                    cur.executemany(sql, params)
                else:
                    cur.execute(sql, params)
                result = None
                if fetch == "one":
                    row = cur.fetchone()
                    result = dict(row) if row is not None else None
                elif fetch == "all":
                    result = [dict(r) for r in cur.fetchall()]
                elif fetch == "lastrowid":
                    result = cur.lastrowid
                conn.commit()
                return result
            finally:
                self._close(conn)

    def _columns(self, table):
        rows = self._execute(f"PRAGMA table_info({table})", fetch="all")
        return {r["name"] for r in rows}

    def _ensure_column(self, table, column, decl):
        if column not in self._columns(table):
            self._execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # --------------------------------------------------------------- schema
    def init_db(self, seed_demo_accounts=False, demo_password=None):
        if self.db_path != ":memory:":
            try:
                self._execute("PRAGMA journal_mode=WAL;")
            except sqlite3.DatabaseError:
                pass
        stmts = [
            """CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_name TEXT UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                display_name TEXT,
                role TEXT,
                class_id INTEGER,
                student_id INTEGER,
                face_embedding TEXT)""",
            """CREATE TABLE IF NOT EXISTS class_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                member_role TEXT NOT NULL,
                UNIQUE(class_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                start_time TEXT,
                end_time TEXT,
                duration_seconds INTEGER,
                final_score INTEGER,
                total_distractions INTEGER,
                history_json TEXT)""",
            """CREATE TABLE IF NOT EXISTS class_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER,
                started_at TEXT,
                ended_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS session_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_session_id INTEGER,
                session_id INTEGER,
                class_id INTEGER,
                student_id INTEGER NOT NULL,
                attendance_status TEXT,
                first_seen_at REAL,
                last_seen_at REAL,
                visible_seconds REAL,
                away_count INTEGER,
                away_seconds REAL,
                final_focus_score REAL,
                avg_focus_score REAL,
                measured_seconds REAL,
                identity_confidence REAL,
                event_counts_json TEXT)""",
            """CREATE TABLE IF NOT EXISTS focus_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                type TEXT,
                timestamp TEXT)""",
            """CREATE TABLE IF NOT EXISTS focus_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                class_session_id INTEGER,
                session_id INTEGER,
                class_id INTEGER,
                timestamp REAL,
                focus_score REAL,
                focus_state TEXT,
                visibility_status TEXT,
                attendance_status TEXT,
                identity_confidence REAL)""",
        ]
        for s in stmts:
            self._execute(s)
        # Forward migrations for databases created by older versions.
        self._ensure_column("users", "face_embedding", "TEXT")
        self._ensure_column("users", "student_id", "INTEGER")
        self._ensure_column("users", "roll", "TEXT")
        self._ensure_column("sessions", "user_id", "INTEGER")
        self._ensure_column("sessions", "subject", "TEXT")
        self._ensure_column("sessions", "avg_focus_score", "REAL")
        self._ensure_column("sessions", "class_session_id", "INTEGER")
        self._ensure_column("sessions", "source", "TEXT")
        self._ensure_column("class_sessions", "mode", "TEXT")
        self._ensure_column("class_sessions", "started_by", "INTEGER")
        for col, decl in (("student_id", "INTEGER"), ("class_session_id", "INTEGER"), ("class_id", "INTEGER"),
                          ("start_time", "REAL"), ("end_time", "REAL"), ("duration_seconds", "REAL"),
                          ("confidence", "REAL"), ("source", "TEXT"), ("metadata_json", "TEXT")):
            self._ensure_column("focus_events", col, decl)
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_events_student ON focus_events(student_id)",
            "CREATE INDEX IF NOT EXISTS ix_events_cs ON focus_events(class_session_id)",
            "CREATE INDEX IF NOT EXISTS ix_snap_class ON focus_snapshots(class_id)",
            "CREATE INDEX IF NOT EXISTS ix_ss_student ON session_students(student_id)",
            "CREATE INDEX IF NOT EXISTS ix_ss_cs ON session_students(class_session_id)",
        ):
            self._execute(idx)

        self._migrate_class_members()
        self.migrate_plaintext_passwords()
        if seed_demo_accounts:
            self.seed_demo_accounts(demo_password or env_value(os.environ, "DEMO_ACCOUNT_PASSWORD") or "123")

    def _migrate_class_members(self):
        rows = self._execute("SELECT id, role, class_id FROM users WHERE class_id IS NOT NULL", fetch="all")
        for r in rows:
            if r["role"] in ("teacher", "student"):
                self._execute("INSERT OR IGNORE INTO class_members (class_id, user_id, member_role) VALUES (?, ?, ?)",
                              (r["class_id"], r["id"], r["role"]))

    def migrate_plaintext_passwords(self):
        """Hash every legacy plaintext password in place. Returns count."""
        rows = self._execute("SELECT id, password FROM users", fetch="all")
        n = 0
        for r in rows:
            pw = r["password"]
            if pw and not is_hashed(pw):
                self._execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(pw), r["id"]))
                n += 1
        return n

    def seed_demo_accounts(self, password):
        for cid, name in DEMO_CLASSES:
            self._execute("INSERT OR IGNORE INTO classes (id, class_name) VALUES (?, ?)", (cid, name))
        hashed = hash_password(password)
        for uid, username, display, role, class_id, student_id in DEMO_USERS:
            self._execute(
                "INSERT OR IGNORE INTO users (id, username, password, display_name, role, class_id, student_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (uid, username, hashed, display, role, class_id, student_id))
        self._migrate_class_members()

    # --------------------------------------------------------------- classes
    def get_classes_list(self):
        rows = self._execute("""
            SELECT c.id, c.class_name, COUNT(u.id) AS student_count
            FROM classes c LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            GROUP BY c.id ORDER BY c.class_name""", fetch="all")
        return [{"id": r["id"], "class_name": r["class_name"], "student_count": r["student_count"]} for r in rows]

    def get_class_name(self, class_id):
        if class_id is None:
            return "Chưa gán lớp"
        row = self._execute("SELECT class_name FROM classes WHERE id = ?", (class_id,), fetch="one")
        return row["class_name"] if row else "Chưa gán lớp"

    def create_class(self, class_name):
        try:
            self._execute("INSERT INTO classes (class_name) VALUES (?)", (class_name,))
            return True, "Tạo lớp học thành công"
        except sqlite3.IntegrityError:
            return False, "Tên lớp học đã tồn tại"

    def update_class(self, class_id, new_name):
        try:
            self._execute("UPDATE classes SET class_name = ? WHERE id = ?", (new_name, class_id))
            return True, "Cập nhật tên lớp học thành công"
        except sqlite3.IntegrityError:
            return False, "Tên lớp học đã tồn tại"

    def delete_class(self, class_id):
        self._execute("UPDATE users SET class_id = NULL WHERE class_id = ?", (class_id,))
        self._execute("DELETE FROM class_members WHERE class_id = ?", (class_id,))
        self._execute("DELETE FROM classes WHERE id = ?", (class_id,))
        return True, "Xóa lớp học thành công"

    def add_class_member(self, class_id, user_id, member_role):
        self._execute("INSERT OR IGNORE INTO class_members (class_id, user_id, member_role) VALUES (?, ?, ?)",
                      (class_id, user_id, member_role))

    def get_teacher_class_ids(self, user_id):
        rows = self._execute("SELECT class_id FROM class_members WHERE user_id = ? AND member_role = 'teacher'",
                             (user_id,), fetch="all")
        ids = {r["class_id"] for r in rows}
        u = self.get_user_by_id(user_id)
        if u and u.get("role") == "teacher" and u.get("class_id") is not None:
            ids.add(u["class_id"])
        return sorted(i for i in ids if i is not None)

    # ----------------------------------------------------------------- users
    def _user_row(self, r):
        if not r:
            return None
        return {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"],
                "class_id": r["class_id"], "student_id": r.get("student_id"), "roll": r.get("roll")}

    def get_user_by_id(self, user_id):
        return self._user_row(self._execute("SELECT * FROM users WHERE id = ?", (user_id,), fetch="one"))

    def get_user_by_username(self, username):
        return self._user_row(self._execute("SELECT * FROM users WHERE username = ?", (username,), fetch="one"))

    def get_users_list(self):
        rows = self._execute("""
            SELECT u.id, u.username, u.display_name, u.role, c.class_name, u.class_id, u.student_id,
                   u.face_embedding IS NOT NULL AND u.face_embedding != '' AS has_face
            FROM users u LEFT JOIN classes c ON u.class_id = c.id
            ORDER BY u.role, u.display_name""", fetch="all")
        return [{"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"],
                 "class_name": r["class_name"] or "N/A", "class_id": r["class_id"], "student_id": r["student_id"],
                 "has_face": bool(r["has_face"])} for r in rows]

    def create_user(self, username, password, display_name, role, class_id, student_id=None):
        if role not in VALID_ROLES:
            return False, "Vai trò không hợp lệ"
        if not password:
            return False, "Mật khẩu không được để trống"
        c_id = _as_int(class_id) if role in ("teacher", "student") else None
        s_id = _as_int(student_id) if role == "parent" else None
        try:
            new_id = self._execute(
                "INSERT INTO users (username, password, display_name, role, class_id, student_id) VALUES (?, ?, ?, ?, ?, ?)",
                (username, hash_password(password), display_name, role, c_id, s_id), fetch="lastrowid")
        except sqlite3.IntegrityError:
            return False, "Tên đăng nhập đã tồn tại"
        if c_id is not None:
            self.add_class_member(c_id, new_id, role)
        return True, "Tạo tài khoản thành công"

    def update_user(self, user_id, username, password, display_name, role, class_id, student_id=None):
        if role not in VALID_ROLES:
            return False, "Vai trò không hợp lệ"
        c_id = _as_int(class_id) if role in ("teacher", "student") else None
        s_id = _as_int(student_id) if role == "parent" else None
        old = self.get_user_by_id(user_id)
        try:
            if password and password.strip():
                self._execute("""UPDATE users SET username=?, password=?, display_name=?, role=?, class_id=?, student_id=?
                                 WHERE id=?""", (username, hash_password(password), display_name, role, c_id, s_id, user_id))
            else:
                self._execute("""UPDATE users SET username=?, display_name=?, role=?, class_id=?, student_id=?
                                 WHERE id=?""", (username, display_name, role, c_id, s_id, user_id))
        except sqlite3.IntegrityError:
            return False, "Tên đăng nhập đã tồn tại"
        if old and (old.get("role") != role or old.get("class_id") != c_id):
            # Membership granted through the previous role/class assignment is revoked.
            self._execute("DELETE FROM class_members WHERE user_id = ? AND (member_role != ? OR class_id = ?)",
                          (user_id, role, old.get("class_id")))
        if c_id is not None:
            self.add_class_member(c_id, user_id, role)
        return True, "Cập nhật tài khoản thành công"

    def update_display_name(self, user_id, display_name):
        self._execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
        return True, "Cập nhật thành công"

    def delete_user(self, user_id):
        self._execute("DELETE FROM class_members WHERE user_id = ?", (user_id,))
        self._execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True, "Xóa tài khoản thành công"

    def authenticate_user(self, username, password):
        """Return ``(id, username, display_name, role, class_id)`` or None.

        Legacy plaintext rows are verified once and re-hashed immediately.
        """
        if not username or not password:
            return None
        row = self._execute("SELECT * FROM users WHERE username = ?", (username,), fetch="one")
        if not row:
            return None
        ok, needs_rehash = verify_password(row["password"], password)
        if not ok:
            return None
        if needs_rehash:
            self._execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), row["id"]))
        return (row["id"], row["username"], row["display_name"], row["role"], row["class_id"])

    def get_password_hash(self, user_id):
        row = self._execute("SELECT password FROM users WHERE id = ?", (user_id,), fetch="one")
        return row["password"] if row else None

    def get_student_id_for_parent(self, parent_id):
        row = self._execute("SELECT student_id FROM users WHERE id = ? AND role = 'parent'", (parent_id,), fetch="one")
        return row["student_id"] if row else None

    def get_students_in_class(self, class_id):
        rows = self._execute("""SELECT id, username, display_name, class_id, roll FROM users
                                WHERE role = 'student' AND class_id = ? ORDER BY display_name""",
                             (class_id,), fetch="all")
        return [{"student_id": r["id"], "username": r["username"], "display_name": r["display_name"],
                 "class_id": r["class_id"], "roll": r["roll"]} for r in rows]

    def save_face_embedding(self, user_id, embedding_json):
        self._execute("UPDATE users SET face_embedding = ? WHERE id = ? AND role = 'student'", (embedding_json, user_id))
        return True

    def get_student_embeddings_by_class(self, class_id):
        rows = self._execute("""SELECT id, display_name, username, face_embedding FROM users
                                WHERE role = 'student' AND class_id = ?""", (class_id,), fetch="all")
        out = []
        for r in rows:
            if not r["face_embedding"]:
                continue
            try:
                out.append({"student_id": r["id"], "full_name": r["display_name"], "username": r["username"],
                            "embedding": json.loads(r["face_embedding"])})
            except (TypeError, ValueError):
                continue
        return out

    # ------------------------------------------------------ personal sessions
    def create_session(self, user_id, start_time, subject=None, class_session_id=None, source="personal_camera"):
        return self._execute("""INSERT INTO sessions (user_id, start_time, subject, class_session_id, source)
                                VALUES (?, ?, ?, ?, ?)""",
                             (user_id, start_time, subject, class_session_id, source), fetch="lastrowid")

    def update_session(self, session_id, end_time, duration_seconds, final_score, total_distractions,
                       history_json=None, avg_focus_score=None):
        self._execute("""UPDATE sessions SET end_time=?, duration_seconds=?, final_score=?, total_distractions=?,
                         avg_focus_score=? WHERE id=?""",
                      (end_time, duration_seconds, final_score, total_distractions, avg_focus_score, session_id))
        return True

    def get_session(self, session_id):
        return self._execute("SELECT * FROM sessions WHERE id = ?", (session_id,), fetch="one")

    def get_sessions_for_user(self, user_id):
        return self._execute("""SELECT id, user_id, start_time, end_time, duration_seconds, final_score,
                                avg_focus_score, total_distractions, subject, class_session_id, source
                                FROM sessions WHERE user_id = ? ORDER BY id DESC""", (user_id,), fetch="all")

    def get_sessions_for_users(self, user_ids):
        ids = [i for i in user_ids if i is not None]
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return self._execute(f"""SELECT id, user_id, start_time, end_time, duration_seconds, final_score,
                                 avg_focus_score, total_distractions, subject, class_session_id, source
                                 FROM sessions WHERE user_id IN ({q}) ORDER BY id DESC""", tuple(ids), fetch="all")

    # --------------------------------------------------------- class sessions
    def create_class_session(self, class_id, started_at, mode="classroom", started_by=None):
        return self._execute("INSERT INTO class_sessions (class_id, started_at, mode, started_by) VALUES (?, ?, ?, ?)",
                             (class_id, started_at, mode, started_by), fetch="lastrowid")

    def end_class_session(self, class_session_id, ended_at):
        self._execute("UPDATE class_sessions SET ended_at = ? WHERE id = ?", (ended_at, class_session_id))
        return True

    def get_class_session(self, class_session_id):
        return self._execute("SELECT * FROM class_sessions WHERE id = ?", (class_session_id,), fetch="one")

    def get_class_sessions(self, class_id):
        return self._execute("SELECT * FROM class_sessions WHERE class_id = ? ORDER BY id DESC", (class_id,), fetch="all")

    # ------------------------------------------------------ session_students
    def save_session_student(self, row):
        self._execute("""INSERT INTO session_students (class_session_id, session_id, class_id, student_id,
                         attendance_status, first_seen_at, last_seen_at, visible_seconds, away_count, away_seconds,
                         final_focus_score, avg_focus_score, measured_seconds, identity_confidence, event_counts_json)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (row.get("class_session_id"), row.get("session_id"), row.get("class_id"), row["student_id"],
                       row.get("attendance_status"), row.get("first_seen_at"), row.get("last_seen_at"),
                       row.get("visible_seconds"), row.get("away_count"), row.get("away_seconds"),
                       row.get("final_focus_score"), row.get("avg_focus_score"), row.get("measured_seconds"),
                       row.get("identity_confidence"), json.dumps(row.get("event_counts") or {})))
        return True

    def get_session_students(self, class_session_id=None, student_id=None, class_id=None):
        where, params = [], []
        if class_session_id is not None:
            where.append("class_session_id = ?"); params.append(class_session_id)
        if student_id is not None:
            where.append("student_id = ?"); params.append(student_id)
        if class_id is not None:
            where.append("class_id = ?"); params.append(class_id)
        sql = "SELECT * FROM session_students" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        rows = self._execute(sql, tuple(params), fetch="all")
        for r in rows:
            r["event_counts"] = _json_or_empty(r.pop("event_counts_json", None))
        return rows

    # ----------------------------------------------------------- focus events
    def create_focus_event(self, row):
        start = row.get("start_time")
        self._execute("""INSERT INTO focus_events (session_id, type, timestamp, student_id, class_session_id, class_id,
                         start_time, end_time, duration_seconds, confidence, source, metadata_json)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (row.get("session_id"), row["type"], _now_str(start), row.get("student_id"),
                       row.get("class_session_id"), row.get("class_id"), start, row.get("end_time"),
                       row.get("duration_seconds"), row.get("confidence"), row.get("source"),
                       json.dumps(row.get("metadata") or {}, default=str)))
        return True

    def get_focus_events(self, student_id=None, session_id=None, class_session_id=None, class_id=None):
        where, params = [], []
        for col, val in (("student_id", student_id), ("session_id", session_id),
                         ("class_session_id", class_session_id), ("class_id", class_id)):
            if val is not None:
                where.append(f"{col} = ?"); params.append(val)
        sql = "SELECT * FROM focus_events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        rows = self._execute(sql, tuple(params), fetch="all")
        for r in rows:
            r["metadata"] = _json_or_empty(r.pop("metadata_json", None))
        return rows

    # -------------------------------------------------------- focus snapshots
    def create_focus_snapshot(self, row):
        self._execute("""INSERT INTO focus_snapshots (student_id, class_session_id, session_id, class_id, timestamp,
                         focus_score, focus_state, visibility_status, attendance_status, identity_confidence)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (row.get("student_id"), row.get("class_session_id"), row.get("session_id"), row.get("class_id"),
                       row.get("timestamp"), row.get("focus_score"), row.get("focus_state"),
                       row.get("visibility_status"), row.get("attendance_status"), row.get("identity_confidence")))
        return True

    def get_focus_snapshots(self, class_id=None, student_id=None, class_session_id=None, session_id=None):
        where, params = [], []
        for col, val in (("class_id", class_id), ("student_id", student_id),
                         ("class_session_id", class_session_id), ("session_id", session_id)):
            if val is not None:
                where.append(f"{col} = ?"); params.append(val)
        sql = "SELECT * FROM focus_snapshots" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        return self._execute(sql, tuple(params), fetch="all")


# =============================================================================
# Firestore (server-side only; firestore.rules deny all direct client access)
# =============================================================================
class FirestoreRepository(BaseRepository):
    def __init__(self, client=None):
        self.db = client
        if self.db is None:
            self._connect()

    def _connect(self):
        from google.cloud import firestore
        use_emulator = os.environ.get("USE_EMULATOR", "false").lower() == "true"
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or "focusguard-ai"
        if use_emulator:
            from google.auth import credentials as auth_credentials
            self.db = firestore.Client(project=project, credentials=auth_credentials.AnonymousCredentials())
        else:
            self.db = firestore.Client(project=project)

    # ------------------------------------------------------------- helpers
    def _next_id(self, name):
        from google.cloud import firestore
        ref = self.db.collection("_counters").document(name)

        @firestore.transactional
        def bump(tx):
            snap = ref.get(transaction=tx)
            current = (snap.to_dict() or {}).get("value", 0) if snap.exists else 0
            if current == 0:
                # Initialise from existing numeric ids (older databases).
                for doc in self.db.collection(name).select([]).stream():
                    i = _as_int(doc.id)
                    if i is not None and i > current:
                        current = i
            tx.set(ref, {"value": current + 1})
            return current + 1

        return bump(self.db.transaction())

    def _col(self, name):
        return self.db.collection(name)

    def _docs(self, name, **equals):
        q = self._col(name)
        for k, v in equals.items():
            if v is not None:
                q = q.where(k, "==", v)
        return [d.to_dict() for d in q.stream()]

    def _add(self, name, data):
        new_id = self._next_id(name)
        data = dict(data)
        data["id"] = new_id
        self._col(name).document(str(new_id)).set(data)
        return new_id

    # --------------------------------------------------------------- schema
    def init_db(self, seed_demo_accounts=False, demo_password=None):
        self.migrate_plaintext_passwords()
        if seed_demo_accounts:
            self.seed_demo_accounts(demo_password or env_value(os.environ, "DEMO_ACCOUNT_PASSWORD") or "123")

    def migrate_plaintext_passwords(self):
        n = 0
        for d in self._col("users").stream():
            pw = (d.to_dict() or {}).get("password")
            if pw and not is_hashed(pw):
                d.reference.update({"password": hash_password(pw)})
                n += 1
        return n

    def seed_demo_accounts(self, password):
        for cid, name in DEMO_CLASSES:
            ref = self._col("classes").document(str(cid))
            if not ref.get().exists:
                ref.set({"id": cid, "class_name": name})
        hashed = hash_password(password)
        for uid, username, display, role, class_id, student_id in DEMO_USERS:
            ref = self._col("users").document(str(uid))
            if not ref.get().exists:
                ref.set({"id": uid, "username": username, "password": hashed, "display_name": display,
                         "role": role, "class_id": class_id, "student_id": student_id})
            if class_id is not None:
                self.add_class_member(class_id, uid, role)

    # --------------------------------------------------------------- classes
    def get_classes_list(self):
        classes = {c["id"]: {"id": c["id"], "class_name": c.get("class_name"), "student_count": 0}
                   for c in self._docs("classes") if "id" in c}
        for u in self._docs("users", role="student"):
            if u.get("class_id") in classes:
                classes[u["class_id"]]["student_count"] += 1
        return sorted(classes.values(), key=lambda c: c["class_name"] or "")

    def get_class_name(self, class_id):
        if class_id is None:
            return "Chưa gán lớp"
        doc = self._col("classes").document(str(class_id)).get()
        return (doc.to_dict() or {}).get("class_name", "Chưa gán lớp") if doc.exists else "Chưa gán lớp"

    def create_class(self, class_name):
        if self._docs("classes", class_name=class_name):
            return False, "Tên lớp học đã tồn tại"
        self._add("classes", {"class_name": class_name})
        return True, "Tạo lớp học thành công"

    def update_class(self, class_id, new_name):
        for c in self._docs("classes", class_name=new_name):
            if c.get("id") != int(class_id):
                return False, "Tên lớp học đã tồn tại"
        self._col("classes").document(str(class_id)).update({"class_name": new_name})
        return True, "Cập nhật tên lớp học thành công"

    def delete_class(self, class_id):
        cid = int(class_id)
        for d in self._col("users").where("class_id", "==", cid).stream():
            d.reference.update({"class_id": None})
        for d in self._col("class_members").where("class_id", "==", cid).stream():
            d.reference.delete()
        self._col("classes").document(str(cid)).delete()
        return True, "Xóa lớp học thành công"

    def add_class_member(self, class_id, user_id, member_role):
        doc_id = f"{int(class_id)}_{int(user_id)}"
        self._col("class_members").document(doc_id).set(
            {"class_id": int(class_id), "user_id": int(user_id), "member_role": member_role})

    def get_teacher_class_ids(self, user_id):
        ids = {m["class_id"] for m in self._docs("class_members", user_id=int(user_id), member_role="teacher")}
        u = self.get_user_by_id(user_id)
        if u and u.get("role") == "teacher" and u.get("class_id") is not None:
            ids.add(u["class_id"])
        return sorted(ids)

    # ----------------------------------------------------------------- users
    def _user_public(self, d):
        if not d:
            return None
        return {"id": d.get("id"), "username": d.get("username"), "display_name": d.get("display_name"),
                "role": d.get("role"), "class_id": d.get("class_id"), "student_id": d.get("student_id"),
                "roll": d.get("roll")}

    def _user_doc(self, user_id):
        doc = self._col("users").document(str(user_id)).get()
        return doc.to_dict() if doc.exists else None

    def get_user_by_id(self, user_id):
        return self._user_public(self._user_doc(user_id))

    def get_user_by_username(self, username):
        docs = self._docs("users", username=username)
        return self._user_public(docs[0]) if docs else None

    def get_users_list(self):
        names = {c["id"]: c.get("class_name") for c in self._docs("classes") if "id" in c}
        users = []
        for d in self._docs("users"):
            users.append({"id": d.get("id"), "username": d.get("username"), "display_name": d.get("display_name"),
                          "role": d.get("role"), "class_name": names.get(d.get("class_id"), "N/A"),
                          "class_id": d.get("class_id"), "student_id": d.get("student_id"),
                          "has_face": bool(d.get("face_embedding"))})
        return sorted(users, key=lambda u: (u["role"] or "", u["display_name"] or ""))

    def create_user(self, username, password, display_name, role, class_id, student_id=None):
        if role not in VALID_ROLES:
            return False, "Vai trò không hợp lệ"
        if not password:
            return False, "Mật khẩu không được để trống"
        if self._docs("users", username=username):
            return False, "Tên đăng nhập đã tồn tại"
        c_id = _as_int(class_id) if role in ("teacher", "student") else None
        s_id = _as_int(student_id) if role == "parent" else None
        new_id = self._add("users", {"username": username, "password": hash_password(password),
                                     "display_name": display_name, "role": role, "class_id": c_id,
                                     "student_id": s_id})
        if c_id is not None:
            self.add_class_member(c_id, new_id, role)
        return True, "Tạo tài khoản thành công"

    def update_user(self, user_id, username, password, display_name, role, class_id, student_id=None):
        if role not in VALID_ROLES:
            return False, "Vai trò không hợp lệ"
        for d in self._docs("users", username=username):
            if d.get("id") != int(user_id):
                return False, "Tên đăng nhập đã tồn tại"
        c_id = _as_int(class_id) if role in ("teacher", "student") else None
        s_id = _as_int(student_id) if role == "parent" else None
        data = {"username": username, "display_name": display_name, "role": role, "class_id": c_id,
                "student_id": s_id}
        if password and password.strip():
            data["password"] = hash_password(password)
        old = self.get_user_by_id(user_id)
        self._col("users").document(str(user_id)).update(data)
        if old and (old.get("role") != role or old.get("class_id") != c_id):
            for m in self._col("class_members").where("user_id", "==", int(user_id)).stream():
                md = m.to_dict() or {}
                if md.get("member_role") != role or md.get("class_id") == old.get("class_id"):
                    m.reference.delete()
        if c_id is not None:
            self.add_class_member(c_id, int(user_id), role)
        return True, "Cập nhật tài khoản thành công"

    def update_display_name(self, user_id, display_name):
        self._col("users").document(str(user_id)).update({"display_name": display_name})
        return True, "Cập nhật thành công"

    def delete_user(self, user_id):
        for m in self._col("class_members").where("user_id", "==", int(user_id)).stream():
            m.reference.delete()
        self._col("users").document(str(user_id)).delete()
        return True, "Xóa tài khoản thành công"

    def authenticate_user(self, username, password):
        if not username or not password:
            return None
        docs = list(self._col("users").where("username", "==", username).limit(1).stream())
        if not docs:
            return None
        d = docs[0].to_dict()
        ok, needs_rehash = verify_password(d.get("password"), password)
        if not ok:
            return None
        if needs_rehash:
            docs[0].reference.update({"password": hash_password(password)})
        return (d.get("id"), d.get("username"), d.get("display_name"), d.get("role"), d.get("class_id"))

    def get_password_hash(self, user_id):
        d = self._user_doc(user_id)
        return d.get("password") if d else None

    def get_student_id_for_parent(self, parent_id):
        d = self._user_doc(parent_id)
        return d.get("student_id") if d and d.get("role") == "parent" else None

    def get_students_in_class(self, class_id):
        out = [{"student_id": d.get("id"), "username": d.get("username"), "display_name": d.get("display_name"),
                "class_id": d.get("class_id"), "roll": d.get("roll")}
               for d in self._docs("users", role="student", class_id=int(class_id))]
        return sorted(out, key=lambda s: s["display_name"] or "")

    def save_face_embedding(self, user_id, embedding_json):
        ref = self._col("users").document(str(user_id))
        doc = ref.get()
        if not doc.exists or (doc.to_dict() or {}).get("role") != "student":
            return False
        ref.update({"face_embedding": embedding_json})
        return True

    def get_student_embeddings_by_class(self, class_id):
        out = []
        for d in self._docs("users", role="student", class_id=int(class_id)):
            emb = d.get("face_embedding")
            if not emb:
                continue
            try:
                out.append({"student_id": d.get("id"), "full_name": d.get("display_name"),
                            "username": d.get("username"),
                            "embedding": json.loads(emb) if isinstance(emb, str) else emb})
            except (TypeError, ValueError):
                continue
        return out

    # ------------------------------------------------------ personal sessions
    def create_session(self, user_id, start_time, subject=None, class_session_id=None, source="personal_camera"):
        return self._add("sessions", {"user_id": user_id, "start_time": start_time, "end_time": None,
                                      "duration_seconds": None, "final_score": None, "avg_focus_score": None,
                                      "total_distractions": None, "subject": subject,
                                      "class_session_id": class_session_id, "source": source})

    def update_session(self, session_id, end_time, duration_seconds, final_score, total_distractions,
                       history_json=None, avg_focus_score=None):
        self._col("sessions").document(str(session_id)).update({
            "end_time": end_time, "duration_seconds": duration_seconds, "final_score": final_score,
            "total_distractions": total_distractions, "avg_focus_score": avg_focus_score})
        return True

    def get_session(self, session_id):
        doc = self._col("sessions").document(str(session_id)).get()
        return doc.to_dict() if doc.exists else None

    def get_sessions_for_user(self, user_id):
        return sorted(self._docs("sessions", user_id=int(user_id)), key=lambda s: -(s.get("id") or 0))

    def get_sessions_for_users(self, user_ids):
        out = []
        for uid in {i for i in user_ids if i is not None}:
            out.extend(self.get_sessions_for_user(uid))
        return sorted(out, key=lambda s: -(s.get("id") or 0))

    # --------------------------------------------------------- class sessions
    def create_class_session(self, class_id, started_at, mode="classroom", started_by=None):
        return self._add("class_sessions", {"class_id": int(class_id), "started_at": started_at, "ended_at": None,
                                            "mode": mode, "started_by": started_by})

    def end_class_session(self, class_session_id, ended_at):
        self._col("class_sessions").document(str(class_session_id)).update({"ended_at": ended_at})
        return True

    def get_class_session(self, class_session_id):
        doc = self._col("class_sessions").document(str(class_session_id)).get()
        return doc.to_dict() if doc.exists else None

    def get_class_sessions(self, class_id):
        return sorted(self._docs("class_sessions", class_id=int(class_id)), key=lambda s: -(s.get("id") or 0))

    # ------------------------------------------------------ session_students
    def save_session_student(self, row):
        data = dict(row)
        data["event_counts"] = dict(row.get("event_counts") or {})
        self._add("session_students", data)
        return True

    def get_session_students(self, class_session_id=None, student_id=None, class_id=None):
        return self._docs("session_students", class_session_id=class_session_id,
                          student_id=student_id, class_id=class_id)

    # -------------------------------------------------- events and snapshots
    def create_focus_event(self, row):
        data = dict(row)
        data["timestamp"] = _now_str(row.get("start_time"))
        self._add("focus_events", data)
        return True

    def get_focus_events(self, student_id=None, session_id=None, class_session_id=None, class_id=None):
        return self._docs("focus_events", student_id=student_id, session_id=session_id,
                          class_session_id=class_session_id, class_id=class_id)

    def create_focus_snapshot(self, row):
        self._add("focus_snapshots", dict(row))
        return True

    def get_focus_snapshots(self, class_id=None, student_id=None, class_session_id=None, session_id=None):
        return self._docs("focus_snapshots", class_id=class_id, student_id=student_id,
                          class_session_id=class_session_id, session_id=session_id)


def get_repository(db_type=None):
    db_type = (db_type or os.environ.get("DATABASE_TYPE") or "sqlite").lower()
    if db_type == "firestore":
        use_emulator = os.environ.get("USE_EMULATOR", "false").lower() == "true"
        if use_emulator:
            os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
        else:
            os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
        print(f"[DATABASE] Using Firestore Repository (Emulator={use_emulator})")
        return FirestoreRepository()
    print("[DATABASE] Using SQLite Repository")
    return SQLiteRepository()
