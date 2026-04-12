"""YOLO + ByteTrack for persistent player tracking.

Uses Ultralytics' built-in ByteTrack integration which gives us a real tracker
instead of the greedy nearest-neighbor we had before. Track IDs persist across
frames even through brief occlusions.
"""

import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO


YOLO_MODEL_PATH = os.getenv("YOLO_MODEL", "yolov8m.pt")  # medium — balance speed/accuracy
CONFIDENCE_THRESHOLD = float(os.getenv("YOLO_CONF", "0.35"))
IOU_THRESHOLD = float(os.getenv("YOLO_IOU", "0.5"))

# COCO classes we care about in football
_LABEL_MAP = {
    0: "Player",      # person
    32: "Ball",       # sports ball
}


class YoloTracker:
    """Run YOLO detection + ByteTrack on each frame.

    Returns list of detections with persistent track_id.
    """

    def __init__(self, model_path: str = YOLO_MODEL_PATH):
        print(f"[yolo_tracker] loading {model_path}")
        self.model = YOLO(model_path)

    def detect_and_track(self, frame: np.ndarray) -> List[Dict]:
        """Run detection + tracking. Returns normalized boxes with track IDs."""
        h, w = frame.shape[:2]

        # persist=True enables ByteTrack across calls
        results = self.model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=CONFIDENCE_THRESHOLD,
            iou=IOU_THRESHOLD,
            verbose=False,
            classes=[0, 32],  # person + sports ball only
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue

            boxes = result.boxes
            ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)

            for i, box in enumerate(boxes):
                cls_id = int(box.cls[0])
                if cls_id not in _LABEL_MAP:
                    continue

                raw_label = _LABEL_MAP[cls_id]
                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                track_id = ids[i] if i < len(ids) else None

                # Filter crowd: small players in upper 1/3 of frame
                box_h = (y2 - y1) / h
                if raw_label == "Player" and box_h < 0.06 and (y1 / h) < 0.35:
                    continue  # skip crowd detections

                detections.append({
                    "track_id": track_id,
                    "label": raw_label,
                    "confidence": round(conf, 3),
                    "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                    "pixel_box": [int(x1), int(y1), int(x2), int(y2)],
                })

        return detections


def classify_teams(frame: np.ndarray, detections: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """3-cluster k-means on player jersey color: team A, team B, referees.

    Mutates detections in-place adding `team` and `color` fields.
    Returns (detections, [team_a_hex, team_b_hex]).
    """
    h, w = frame.shape[:2]
    players = []
    hsv_features: List[Tuple[float, float]] = []

    for det in detections:
        if det["label"] != "Player":
            continue
        px1, py1, px2, py2 = det["pixel_box"]
        bw, bh = px2 - px1, py2 - py1
        if bw < 4 or bh < 4:
            continue

        # Torso region: 30-60% vertical, center 60% horizontal
        cy1 = py1 + int(bh * 0.3)
        cy2 = py1 + int(bh * 0.6)
        cx1 = px1 + int(bw * 0.2)
        cx2 = px2 - int(bw * 0.2)
        crop = frame[max(0, cy1):min(h, cy2), max(0, cx1):min(w, cx2)]
        if crop.size == 0:
            continue

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        avg_hue = float(np.median(hsv[:, :, 0]))
        avg_sat = float(np.median(hsv[:, :, 1]))
        avg_val = float(np.median(hsv[:, :, 2]))

        bgr = cv2.cvtColor(np.uint8([[[avg_hue, avg_sat, avg_val]]]), cv2.COLOR_HSV2BGR)
        b, g, r = int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])
        det["color"] = f"#{r:02x}{g:02x}{b:02x}"
        hsv_features.append((avg_hue, avg_sat))
        players.append(det)

    team_colors = ["#FE9330", "#2C7A94"]

    if len(players) >= 4:
        feat = np.array(hsv_features, dtype=np.float32)
        k = min(3, len(players))
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(feat, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)

        counts = [0] * k
        for lbl in labels:
            counts[int(lbl[0])] += 1

        # Smallest cluster = referees
        sorted_clusters = sorted(range(k), key=lambda c: counts[c])
        referee_cluster = sorted_clusters[0] if k == 3 else -1
        team_clusters = [c for c in sorted_clusters if c != referee_cluster][:2]

        for i, det in enumerate(players):
            cluster = int(labels[i][0])
            if cluster == referee_cluster:
                det["label"] = "Referee"
                det["team"] = "ref"
            else:
                det["team"] = "home" if cluster == team_clusters[0] else "away"

        for idx, tc in enumerate(team_clusters[:2]):
            ch, cs = float(centers[tc][0]), float(centers[tc][1])
            bgr = cv2.cvtColor(np.uint8([[[ch, cs, 200]]]), cv2.COLOR_HSV2BGR)
            b, g, r = int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])
            team_colors[idx] = f"#{r:02x}{g:02x}{b:02x}"

    return detections, team_colors
