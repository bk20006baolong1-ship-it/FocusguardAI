"""FocusGuard competition core.

Hardware-free domain logic for the classroom attention pipeline:

    Camera -> Detection/Tracking -> IdentityManager -> canonical student_id
    -> StudentRuntimeState -> BehaviorAnalyzer -> FocusEngine (single, time-based)
    -> SessionRuntime -> persisted events/snapshots -> class-isolated realtime
    -> analytics computed only from persisted data.

Nothing in this package imports OpenCV, MediaPipe, YOLO or InsightFace, so the
whole pipeline can be exercised in CI with synthetic observations.
"""
