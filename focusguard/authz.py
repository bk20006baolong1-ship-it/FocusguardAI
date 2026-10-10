"""Ownership-aware authorization.

Being logged in is never enough. Every data access is decided by who owns the
data:

* student  -> only their own records;
* teacher  -> only classes they are assigned to (class_members / users.class_id);
* parent   -> only the linked student;
* admin    -> explicit admin scope (account/class management and read access
              for support); admin checks are spelled out, not implied.
"""
from dataclasses import dataclass, field
from typing import FrozenSet, Optional

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_PARENT = "parent"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STUDENT, ROLE_TEACHER, ROLE_PARENT, ROLE_ADMIN)


@dataclass(frozen=True)
class Principal:
    user_id: int
    role: str
    class_ids: FrozenSet[int] = field(default_factory=frozenset)
    linked_student_id: Optional[int] = None

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_principal(user_id, role, repo) -> Optional[Principal]:
    uid = _to_int(user_id)
    if uid is None or role not in ROLES:
        return None
    user = repo.get_user_by_id(uid)
    if not user or user.get("role") != role:
        return None
    class_ids = set()
    if role == ROLE_TEACHER:
        class_ids.update(_to_int(c) for c in repo.get_teacher_class_ids(uid))
    elif role == ROLE_STUDENT and user.get("class_id") is not None:
        class_ids.add(_to_int(user.get("class_id")))
    class_ids.discard(None)
    linked = _to_int(repo.get_student_id_for_parent(uid)) if role == ROLE_PARENT else None
    return Principal(uid, role, frozenset(class_ids), linked)


def can_manage_class(p: Optional[Principal], class_id) -> bool:
    cid = _to_int(class_id)
    if p is None or cid is None:
        return False
    if p.is_admin:
        return True
    return p.role == ROLE_TEACHER and cid in p.class_ids


def can_view_student(p: Optional[Principal], student_id, student_class_id=None) -> bool:
    sid = _to_int(student_id)
    if p is None or sid is None:
        return False
    if p.is_admin:
        return True
    if p.role == ROLE_STUDENT:
        return sid == p.user_id
    if p.role == ROLE_PARENT:
        return p.linked_student_id is not None and sid == p.linked_student_id
    if p.role == ROLE_TEACHER:
        return _to_int(student_class_id) in p.class_ids
    return False


def can_view_personal_session(p: Optional[Principal], session_row: Optional[dict], owner_class_id=None) -> bool:
    if p is None or not session_row:
        return False
    return can_view_student(p, session_row.get("user_id"), owner_class_id)


def can_view_class_session(p: Optional[Principal], class_session_row: Optional[dict]) -> bool:
    if p is None or not class_session_row:
        return False
    return can_manage_class(p, class_session_row.get("class_id"))


def can_view_video_feed(p: Optional[Principal], mode: str, class_id=None) -> bool:
    """Classroom feed: teachers of that class (or admin). Personal feed: the
    student who owns the session (the camera is theirs)."""
    if p is None:
        return False
    if mode == "classroom":
        return can_manage_class(p, class_id)
    return p.role == ROLE_STUDENT
