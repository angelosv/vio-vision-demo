"""Azure Video Indexer integration for OCR, transcripts, and scene detection.

Runs asynchronously alongside the main analysis. Results are delivered
via callback to enrich the analysis context over time.
"""

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

ACCOUNT_ID = os.getenv("AZURE_VIDEO_INDEXER_ACCOUNT_ID", "")
API_KEY = os.getenv("AZURE_VIDEO_INDEXER_API_KEY", "")
LOCATION = os.getenv("AZURE_VIDEO_INDEXER_LOCATION", "trial")
API_BASE = f"https://api.videoindexer.ai/{LOCATION}/Accounts/{ACCOUNT_ID}"


@dataclass
class VideoIndexerInsights:
    """Parsed insights from Video Indexer."""

    ocr_texts: List[Dict[str, Any]] = field(default_factory=list)
    transcript_segments: List[Dict[str, Any]] = field(default_factory=list)
    scenes: List[Dict[str, Any]] = field(default_factory=list)
    faces: List[Dict[str, Any]] = field(default_factory=list)
    scoreboard: Optional[Dict[str, Any]] = None
    ready: bool = False


class VideoIndexerClient:
    POLL_INTERVAL = 30  # seconds between status checks

    def __init__(self):
        self.access_token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=60)

    @property
    def available(self) -> bool:
        return bool(ACCOUNT_ID and API_KEY)

    async def get_access_token(self) -> str:
        """Get or refresh the API access token."""
        resp = await self._client.get(
            f"https://api.videoindexer.ai/Auth/{LOCATION}/Accounts/{ACCOUNT_ID}/AccessToken",
            params={"allowEdit": "true"},
            headers={"Ocp-Apim-Subscription-Key": API_KEY},
        )
        resp.raise_for_status()
        self.access_token = resp.json()
        return self.access_token

    async def submit_video(self, video_url: str, name: str = "vio-analysis") -> Optional[str]:
        """Submit video for indexing. Returns video_id or None."""
        if not self.available:
            return None
        try:
            token = await self.get_access_token()
            resp = await self._client.post(
                f"{API_BASE}/Videos",
                params={
                    "accessToken": token,
                    "name": name,
                    "videoUrl": video_url,
                    "language": "auto",
                    "indexingPreset": "Default",
                },
            )
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception as e:
            print(f"Video Indexer submit error: {e}")
            return None

    async def poll_and_extract(
        self,
        video_id: str,
        on_ready: Optional[Callable] = None,
    ) -> VideoIndexerInsights:
        """Poll until indexing completes, then extract insights."""
        insights = VideoIndexerInsights()
        try:
            while True:
                token = await self.get_access_token()
                resp = await self._client.get(
                    f"{API_BASE}/Videos/{video_id}/Index",
                    params={"accessToken": token},
                )
                resp.raise_for_status()
                data = resp.json()

                state = data.get("state", "")
                if state == "Processed":
                    insights = self._parse_insights(data)
                    insights.ready = True
                    if on_ready:
                        await on_ready(insights)
                    break
                elif state == "Failed":
                    print(f"Video Indexer processing failed for {video_id}")
                    break

                await asyncio.sleep(self.POLL_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Video Indexer poll error: {e}")

        return insights

    def _parse_insights(self, data: Dict) -> VideoIndexerInsights:
        """Extract structured insights from raw Video Indexer response."""
        insights = VideoIndexerInsights()
        vi = data.get("videos", [{}])[0].get("insights", {})

        # OCR
        for ocr in vi.get("ocr", []):
            for instance in ocr.get("instances", []):
                insights.ocr_texts.append({
                    "text": ocr.get("text", ""),
                    "time_sec": self._time_to_sec(instance.get("start", "0:00:00")),
                    "confidence": ocr.get("confidence", 0),
                })

        # Transcript
        for seg in vi.get("transcript", []):
            for instance in seg.get("instances", []):
                insights.transcript_segments.append({
                    "text": seg.get("text", ""),
                    "start_sec": self._time_to_sec(instance.get("start", "0:00:00")),
                    "end_sec": self._time_to_sec(instance.get("end", "0:00:00")),
                    "confidence": seg.get("confidence", 0),
                })

        # Scenes
        for scene in vi.get("scenes", []):
            for instance in scene.get("instances", []):
                insights.scenes.append({
                    "start_sec": self._time_to_sec(instance.get("start", "0:00:00")),
                    "end_sec": self._time_to_sec(instance.get("end", "0:00:00")),
                })

        # Faces
        for face in vi.get("faces", []):
            appearances = []
            for instance in face.get("instances", []):
                appearances.append({
                    "start_sec": self._time_to_sec(instance.get("start", "0:00:00")),
                    "end_sec": self._time_to_sec(instance.get("end", "0:00:00")),
                })
            insights.faces.append({
                "name": face.get("name", "Unknown"),
                "appearances": appearances,
            })

        # Parse scoreboard from OCR
        insights.scoreboard = self._parse_scoreboard(insights.ocr_texts)

        return insights

    def _parse_scoreboard(self, ocr_texts: List[Dict]) -> Optional[Dict]:
        """Attempt to extract score information from OCR texts."""
        for entry in ocr_texts:
            text = entry.get("text", "")
            match = re.search(r"(\d+)\s*[-:]\s*(\d+)", text)
            if match:
                return {
                    "home_score": int(match.group(1)),
                    "away_score": int(match.group(2)),
                    "source_text": text,
                    "time_sec": entry.get("time_sec", 0),
                }
        return None

    @staticmethod
    def _time_to_sec(time_str: str) -> float:
        """Convert 'H:MM:SS.fff' or 'MM:SS' to seconds."""
        parts = time_str.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return 0.0

    async def close(self):
        await self._client.aclose()
