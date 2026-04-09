"""Extract audio from video and compute crowd noise intensity using ffmpeg + numpy.

No extra Python dependencies required — uses ffmpeg (system) and numpy (already installed).
The full audio track is extracted once, RMS energy is pre-computed per 1-second window,
and normalized to a 0–10 scale.  Lookups are O(1) by timestamp.
"""

import subprocess
import numpy as np
from typing import List, Optional


class AudioAnalyzer:
    SAMPLE_RATE = 22050  # mono, 22 kHz — good enough for volume analysis

    def __init__(self, url: str, window_sec: float = 1.0):
        self.window_sec = window_sec
        self.rms_values: List[float] = []
        self.duration: float = 0.0
        self._extract_and_analyze(url)

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return len(self.rms_values) > 0

    def get_crowd_intensity(self, time_seconds: float) -> float:
        """Return crowd noise intensity (0–10) at the given timestamp.

        Returns -1 if audio is unavailable.
        """
        if not self.rms_values:
            return -1.0

        idx = int(time_seconds / self.window_sec)
        idx = max(0, min(idx, len(self.rms_values) - 1))
        return round(self.rms_values[idx], 1)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _extract_and_analyze(self, url: str) -> None:
        """Extract audio via ffmpeg and compute per-window RMS energy."""
        try:
            proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-i", url,
                    "-vn",                      # discard video
                    "-ac", "1",                 # mono
                    "-ar", str(self.SAMPLE_RATE),
                    "-f", "s16le",              # raw 16-bit signed PCM
                    "-loglevel", "error",
                    "pipe:1",                   # output to stdout
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw_audio, _ = proc.communicate(timeout=120)

            if not raw_audio:
                return

            # Convert raw PCM bytes → float32 array normalised to [-1, 1]
            audio = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
            self.duration = len(audio) / self.SAMPLE_RATE

            # Compute RMS per window
            window_samples = int(self.window_sec * self.SAMPLE_RATE)
            raw_rms: List[float] = []
            for i in range(0, len(audio), window_samples):
                chunk = audio[i : i + window_samples]
                if len(chunk) > 0:
                    raw_rms.append(float(np.sqrt(np.mean(chunk ** 2))))

            # Normalise to 0–10 scale (relative to this video's loudest moment)
            if raw_rms:
                max_rms = max(raw_rms) or 1.0
                self.rms_values = [(v / max_rms) * 10.0 for v in raw_rms]

        except FileNotFoundError:
            # ffmpeg not installed
            pass
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            # Graceful degradation — audio analysis is optional
            pass
