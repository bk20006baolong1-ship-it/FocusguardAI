from ultralytics import YOLO

PERSON_CLASS = 0
PHONE_CLASS = 67


class PersonTracker:
    """YOLOv8 + ByteTrack for people, and a SEPARATE YOLO instance for phones.

    ``model.track(persist=True)`` registers tracker callbacks on that model
    object. Running phone detection through the same object would feed
    phone-only detections into the person tracker every frame (tracks lost /
    re-found, id switches), so phones use their own detector instance.
    """

    def __init__(self, model_path="yolov8n.pt"):
        self.model = YOLO(model_path)            # person tracking only
        self.phone_model = YOLO(model_path)      # stateless detection only

    def track(self, frame):
        """Return ``[{'track_id': int, 'bbox': [x1, y1, x2, y2], 'conf': float}]``."""
        results = self.model.track(source=frame, persist=True, tracker="bytetrack.yaml",
                                   classes=[PERSON_CLASS], verbose=False)
        tracked = []
        if len(results) > 0 and results[0].boxes is not None and results[0].boxes.id is not None:
            for box in results[0].boxes:
                tracked.append({
                    "track_id": int(box.id[0].item()),
                    "bbox": list(map(int, box.xyxy[0].tolist())),
                    "conf": float(box.conf[0].item()),
                })
        return tracked

    def detect_phones(self, frame):
        """Return ``[((x1, y1, x2, y2), confidence)]`` for cell phones."""
        phones = []
        for r in self.phone_model.predict(source=frame, classes=[PHONE_CLASS], verbose=False):
            for box in r.boxes:
                phones.append((tuple(map(float, box.xyxy[0].tolist())), float(box.conf[0])))
        return phones
