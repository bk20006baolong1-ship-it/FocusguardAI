"""Camera sources, LAN discovery and account camera settings.

    sources.py    CameraSource abstraction (WebcamSource, RTSPSource)
    reader.py     LatestFrameReader: dedicated reader thread, latest-frame slot,
                  health, bounded exponential reconnect
    health.py     connection / stream states and Vietnamese labels
    netguard.py   SSRF guard: only private-LAN camera hosts
    redact.py     central credential redaction for logs, errors, audit, API
    crypto.py     camera credential encryption at rest (FG_CAMERA_CREDENTIAL_KEY)
    rtsp.py       minimal RTSP client used only to probe a stream (OPTIONS/DESCRIBE)
    onvif.py      minimal ONVIF client (WS-Discovery probe, GetProfiles/GetStreamUri)
    discovery.py  user-triggered, bounded LAN discovery jobs
    connect.py    "easy connect": resolve a working stream from host + credentials
    service.py    CameraService: storage, authorization, defaults, class assignment,
                  connection tests, previews, opening the source for a pipeline

Nothing in this package scores students: it only delivers frames to the one
existing AI pipeline (classroom_ai.ClassroomAI / camera_ai.FocusAI).
"""
