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


# --------------------------------------------------------------------------
# BTC feedback: teacher review, evidence, consent, export, evaluation.
# Each rule is explicit; "logged in" or "admin" is never assumed to imply it.
# --------------------------------------------------------------------------
def can_review_class_events(p: Optional[Principal], class_id) -> bool:
    """Confirm / mark false positive / uncertain: teachers of that class only."""
    cid = _to_int(class_id)
    return p is not None and cid is not None and p.role == ROLE_TEACHER and cid in p.class_ids


def can_view_evidence(p: Optional[Principal], clip: Optional[dict]) -> bool:
    """Evidence clips: teachers of the clip's class only. Parents, students and
    other teachers never; admins manage deletion but do not watch clips."""
    return bool(clip) and can_review_class_events(p, clip.get("class_id"))


def can_delete_evidence(p: Optional[Principal], clip: Optional[dict]) -> bool:
    return bool(clip) and p is not None and (p.is_admin or can_view_evidence(p, clip))


def can_manage_privacy(p: Optional[Principal]) -> bool:
    """Record consent, delete biometric data, run retention, read the audit log."""
    return p is not None and p.is_admin


def can_revoke_consent(p: Optional[Principal], student_id) -> bool:
    """The school (admin) or the linked guardian may revoke."""
    sid = _to_int(student_id)
    if p is None or sid is None:
        return False
    return p.is_admin or (p.role == ROLE_PARENT and p.linked_student_id == sid)


def can_export_student(p: Optional[Principal], student_id, student_class_id=None) -> bool:
    """Admin, the student's teachers and the linked guardian (students: no)."""
    if p is None or p.role == ROLE_STUDENT:
        return False
    return can_view_student(p, student_id, student_class_id)


def can_run_evaluation(p: Optional[Principal], class_id) -> bool:
    return can_review_class_events(p, class_id)


# --------------------------------------------------------------------------
# Account camera settings. Teachers own personal cameras; school (shared)
# cameras belong to the admin scope and are assigned to classes. Students and
# parents never see camera settings, credentials or camera IP addresses.
# --------------------------------------------------------------------------
def can_manage_cameras(p: Optional[Principal]) -> bool:
    return p is not None and p.role in (ROLE_TEACHER, ROLE_ADMIN)


def can_edit_camera(p: Optional[Principal], cam: Optional[dict]) -> bool:
    """Rename, credentials, delete, default: the owning teacher (personal camera) or an admin (school camera)."""
    if not cam or not can_manage_cameras(p):
        return False
    if cam.get("is_shared"):
        return p.is_admin
    return p.role == ROLE_TEACHER and _to_int(cam.get("owner_user_id")) == p.user_id


def can_view_camera(p: Optional[Principal], cam: Optional[dict], assigned_class_ids=()) -> bool:
    """See (not edit) a camera: its editors, and teachers of a class the school camera is assigned to."""
    if can_edit_camera(p, cam):
        return True
    return bool(cam) and p is not None and p.role == ROLE_TEACHER and bool(cam.get("is_shared")) and \
        any(_to_int(c) in p.class_ids for c in assigned_class_ids)


def can_use_camera(p: Optional[Principal], cam: Optional[dict], class_id=None, assigned_class_ids=()) -> bool:
    """Open the camera (preview, class session): personal camera of the teacher, or a school
    camera assigned to the class being taught (admins: any school camera)."""
    if not cam or not can_manage_cameras(p):
        return False
    if not cam.get("is_shared"):
        return p.role == ROLE_TEACHER and _to_int(cam.get("owner_user_id")) == p.user_id
    if p.is_admin:
        return True
    cid = _to_int(class_id)
    assigned = {_to_int(c) for c in assigned_class_ids}
    if cid is None:
        return bool(assigned & set(p.class_ids))
    return cid in assigned and can_manage_class(p, cid)


def can_assign_camera(p: Optional[Principal], cam: Optional[dict]) -> bool:
    """Only admins assign school cameras to classes."""
    return bool(cam) and p is not None and p.is_admin and bool(cam.get("is_shared"))
