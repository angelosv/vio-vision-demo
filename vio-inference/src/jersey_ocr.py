"""Jersey number OCR via Azure AI Vision.

Strategy:
- Not every frame (expensive). Run every N frames on NEW/unknown track_ids.
- Crop torso/back region of each player bbox.
- Send batch to Azure AI Vision Read API.
- Apply temporal voting: a track_id's number is the mode of last N reads.

Fallback: if Azure credentials are missing, returns empty (no jersey numbers
shown but system still works).
"""

import os
from collections import Counter, defaultdict, deque
from typing import Dict, List, Optional

import cv2
import numpy as np

try:
    from azure.ai.vision.imageanalysis import ImageAnalysisClient
    from azure.ai.vision.imageanalysis.models import VisualFeatures
    from azure.core.credentials import AzureKeyCredential
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False


AZURE_AI_VISION_ENDPOINT = os.getenv("AZURE_AI_VISION_ENDPOINT", "")
AZURE_AI_VISION_KEY = os.getenv("AZURE_AI_VISION_KEY", "")

# Re-OCR a track_id every N seconds until confident
OCR_INTERVAL_SEC = float(os.getenv("JERSEY_OCR_INTERVAL", "2.0"))
# How many samples to vote on before locking a number
VOTE_WINDOW = 5


class JerseyOcr:
    """Per-session jersey number tracker."""

    def __init__(self):
        self.client: Optional[ImageAnalysisClient] = None
        if AZURE_AVAILABLE and AZURE_AI_VISION_ENDPOINT and AZURE_AI_VISION_KEY:
            self.client = ImageAnalysisClient(
                endpoint=AZURE_AI_VISION_ENDPOINT,
                credential=AzureKeyCredential(AZURE_AI_VISION_KEY),
            )
            print("[jersey_ocr] Azure AI Vision enabled")
        else:
            print("[jersey_ocr] Azure AI Vision disabled (no credentials)")

        # track_id -> deque of recent OCR results
        self.votes: Dict[int, deque] = defaultdict(lambda: deque(maxlen=VOTE_WINDOW))
        # Locked numbers per track (to stop re-OCR-ing)
        self.locked: Dict[int, str] = {}
        # Last OCR timestamp per track
        self.last_ocr: Dict[int, float] = {}

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def get_jersey(self, track_id: int) -> str:
        """Current best-guess jersey number for a track (empty if unknown)."""
        if track_id in self.locked:
            return self.locked[track_id]
        if track_id in self.votes and self.votes[track_id]:
            # Return mode of samples so far
            counter = Counter(self.votes[track_id])
            top, count = counter.most_common(1)[0]
            if count >= 3:  # lock if stable
                self.locked[track_id] = top
                return top
            return top
        return ""

    def process(self, frame: np.ndarray, detections: List[Dict], now: float) -> None:
        """Run OCR on a selection of player detections."""
        if not self.enabled:
            return

        h, w = frame.shape[:2]
        for det in detections:
            if det.get("label") not in ("Player", "Goalkeeper"):
                continue
            tid = det.get("track_id")
            if tid is None or tid in self.locked:
                continue

            last = self.last_ocr.get(tid, 0)
            if now - last < OCR_INTERVAL_SEC:
                continue

            px1, py1, px2, py2 = det.get("pixel_box", [0, 0, 0, 0])
            bw, bh = px2 - px1, py2 - py1
            if bw < 20 or bh < 40:
                continue  # too small for OCR

            # Upper back region (where numbers usually are)
            cy1 = py1 + int(bh * 0.2)
            cy2 = py1 + int(bh * 0.55)
            cx1 = px1 + int(bw * 0.15)
            cx2 = px2 - int(bw * 0.15)
            crop = frame[max(0, cy1):min(h, cy2), max(0, cx1):min(w, cx2)]
            if crop.size == 0:
                continue

            # Encode as JPEG and send to Azure Vision
            try:
                ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    continue
                result = self.client.analyze(
                    image_data=buf.tobytes(),
                    visual_features=[VisualFeatures.READ],
                )
                number = self._extract_number(result)
                if number:
                    self.votes[tid].append(number)
                self.last_ocr[tid] = now
            except Exception as e:
                # Silent — don't break the pipeline over OCR errors
                pass

    @staticmethod
    def _extract_number(result) -> str:
        """Pull the first short numeric string from OCR result."""
        if not result or not result.read:
            return ""
        for block in result.read.blocks:
            for line in block.lines:
                text = (line.text or "").strip()
                # Jersey numbers are 1-2 digits
                digits = "".join(c for c in text if c.isdigit())
                if digits and 1 <= len(digits) <= 2:
                    return digits
        return ""
