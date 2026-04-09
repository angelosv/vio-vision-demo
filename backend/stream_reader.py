import subprocess
import cv2
import numpy as np
from typing import Generator, Optional


def open_video_capture(url: str) -> Optional[cv2.VideoCapture]:
    """Try to open the URL directly with OpenCV.

    Many HLS/MP4 URLs work out-of-the-box on macOS if ffmpeg is available.
    Returns a cv2.VideoCapture or None if it fails.
    """
    cap = cv2.VideoCapture(url)
    if cap.isOpened():
        return cap
    cap.release()
    return None


def yt_dlp_stream(url: str) -> Optional[subprocess.Popen]:
    """Fallback: use yt-dlp to stream the best available format to stdout.

    This spawns a process that writes video bytes to stdout, which can be
    consumed by ffmpeg/OpenCV if needed. For the first prototype we keep it
    simple and rely primarily on cv2.VideoCapture.
    """
    try:
        proc = subprocess.Popen(
            [
                "yt-dlp",
                "-f",
                "best",
                "-o",
                "-",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except FileNotFoundError:
        # yt-dlp not installed or not in PATH
        return None


def get_video_metadata(url: str) -> dict:
    """Return FPS, total frame count, and duration for the given video URL."""
    cap = open_video_capture(url)
    if cap is None:
        return {"fps": 25.0, "total_frames": 0, "duration": 0.0}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps > 0 else 0.0
    cap.release()
    return {"fps": fps, "total_frames": total_frames, "duration": duration}


def frame_generator(url: str, max_frames: int = 0) -> Generator[np.ndarray, None, None]:
    """Yield frames (as numpy arrays) from the given video URL.

    - First tries direct OpenCV capture.
    - If that fails, attempts a yt-dlp pipe (future extension).
    - max_frames=0 means "no explicit limit".
    """
    cap = open_video_capture(url)
    if cap is None:
        # For now, we just stop; later we can add ffmpeg/pipe handling.
        raise RuntimeError(f"Could not open video stream: {url}")

    frame_count = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            yield frame
            frame_count += 1

            if max_frames and frame_count >= max_frames:
                break
    finally:
        cap.release()
