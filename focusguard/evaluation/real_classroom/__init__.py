"""Real classroom evaluation mode (BTC feedback A/B/C).

A structured evaluation session that is kept apart from production sessions:
metadata about the camera set-up, teacher-chosen activity contexts, human
ground-truth annotations stored NEXT TO (never over) the AI output, and a
metrics engine that reports ``NOT_ENOUGH_DATA`` instead of a number when the
ground truth cannot support it.

Cloud CI exercises this code with REPLAY/SYNTHETIC packages only. A result is
real-world evidence only when ``dataset_kind == "REAL"``, consent was verified
and the ground truth thresholds are met.
"""
from .models import (ACTIVITY_CONTEXTS, DATASET_KINDS, GT_LABELS, LABEL_TO_BEHAVIOR,  # noqa: F401
                     ON_TASK_LABELS, PACKAGE_FORMAT)
