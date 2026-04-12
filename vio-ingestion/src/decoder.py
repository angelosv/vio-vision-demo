"""Video decoder supporting SRT, RTMP, HLS, MP4.

Uses OpenCV's cv2.VideoCapture which internally uses FFmpeg — this is enough
for the demo phase. For production we could switch to GStreamer for better
SRT handling, but FFmpeg through OpenCV handles all 4 protocols out of the box
when built with the right codecs.

Protocols:
- SRT:  srt://host:port?streamid=...
- RTMP: rtmp://host:port/app/stream
- HLS:  https://.../playlist.m3u8
- MP4:  https://.../file.mp4 or /local/path.mp4
"""

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

import cv2
import numpy as np


@dataclass
class StreamInfo:
    fps: float
    width: int
    height: int
    total_frames: int
    duration_sec: float
    source_type: str  # srt|rtmp|hls|mp4


def detect_source_type(url: str) -> str:
    u = url.lower()
    if u.startswith("srt://"):
        return "srt"
    if u.startswith("rtmp://") or u.startswith("rtmps://"):
        return "rtmp"
    if u.endswith(".m3u8") or "m3u8" in u:
        return "hls"
    if u.endswith(".mp4") or u.endswith(".mov") or u.endswith(".mkv"):
        return "mp4"
    # Default to HLS for generic https URLs (broadcast streams)
    scheme = urlparse(url).scheme
    if scheme in ("http", "https"):
        return "hls"
    return "mp4"


class VideoDecoder:
    """Wraps cv2.VideoCapture with downsample + reconnect semantics."""

    RECONNECT_DELAY_SEC = 2.0
    MAX_RECONNECT_ATTEMPTS = 5

    def __init__(self, url: str, target_size: tuple = (640, 360)):
        self.url = url
        self.target_size = target_size
        self.source_type = detect_source_type(url)
        self.cap: Optional[cv2.VideoCapture] = None
        self._open()

    def _open(self) -> None:
        # For live streams, set buffer to 1 to minimize latency
        self.cap = cv2.VideoCapture(self.url)
        if self.source_type in ("srt", "rtmp", "hls"):
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open stream: {self.url}")

    def info(self) -> StreamInfo:
        assert self.cap is not None
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total / fps if fps > 0 and total > 0 else 0.0
        return StreamInfo(
            fps=fps, width=width, height=height,
            total_frames=total, duration_sec=duration,
            source_type=self.source_type,
        )

    def read_frames(self, on_frame: Callable[[np.ndarray, int], None],
                    should_stop: Callable[[], bool]) -> None:
        """Read frames continuously, with reconnect for live streams."""
        idx = 0
        attempts = 0

        while not should_stop():
            ret, frame = self.cap.read() if self.cap else (False, None)

            if not ret:
                # For live streams, try to reconnect
                if self.source_type in ("srt", "rtmp", "hls"):
                    attempts += 1
                    if attempts > self.MAX_RECONNECT_ATTEMPTS:
                        print(f"[decoder] reconnect limit exceeded for {self.url}")
                        break
                    print(f"[decoder] stream read failed, reconnecting ({attempts}/{self.MAX_RECONNECT_ATTEMPTS})")
                    self.release()
                    time.sleep(self.RECONNECT_DELAY_SEC)
                    try:
                        self._open()
                        continue
                    except Exception as e:
                        print(f"[decoder] reconnect error: {e}")
                        continue
                else:
                    # File EOF
                    break

            attempts = 0

            if frame is None:
                continue

            # Downsample for inference (preserves aspect ratio from source)
            if self.target_size:
                frame = cv2.resize(frame, self.target_size)

            on_frame(frame, idx)
            idx += 1

    def release(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None
