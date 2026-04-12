"""Publish decoded video frames to Azure Redis cache.

Frames (BGR numpy arrays) are stored with a short TTL to avoid overflowing memory.
Metadata ("a frame is ready at this key") goes out on a pub/sub channel so that
inference workers can consume without polling.
"""

import json
import os
import time
from typing import Optional

import cv2
import numpy as np
import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FRAME_TTL_SEC = int(os.getenv("FRAME_TTL_SEC", "10"))
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))


class FramePublisher:
    """Writes frames to Redis and announces them on pub/sub."""

    def __init__(self, session_id: str, redis_url: str = REDIS_URL):
        self.session_id = session_id
        self.client = redis.from_url(redis_url, decode_responses=False)
        self.channel = f"session:{session_id}:frames"
        self.key_prefix = f"frames:{session_id}"

    def publish(self, frame: np.ndarray, frame_idx: int, fps: float) -> None:
        """Encode and push a single frame."""
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return

        key = f"{self.key_prefix}:{frame_idx}"
        # Store raw JPEG bytes with TTL
        self.client.setex(key, FRAME_TTL_SEC, buf.tobytes())

        # Announce via pub/sub (consumers read frames on demand)
        self.client.publish(self.channel, json.dumps({
            "session_id": self.session_id,
            "frame_idx": frame_idx,
            "redis_key": key,
            "timestamp_ms": int(time.time() * 1000),
            "fps": fps,
            "width": frame.shape[1],
            "height": frame.shape[0],
        }))

    def publish_metadata(self, metadata: dict) -> None:
        """One-shot session metadata (fps, duration, source info)."""
        self.client.set(f"session:{self.session_id}:meta", json.dumps(metadata),
                        ex=3600)
        self.client.publish(self.channel, json.dumps({
            "session_id": self.session_id,
            "type": "metadata",
            **metadata,
        }))

    def publish_end(self) -> None:
        self.client.publish(self.channel, json.dumps({
            "session_id": self.session_id,
            "type": "end",
        }))

    def close(self) -> None:
        self.client.close()
