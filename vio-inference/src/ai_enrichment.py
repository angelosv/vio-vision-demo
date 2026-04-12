"""GPT-4o enrichment for significant events.

Called by the inference loop when the heuristic event detector fires something
interesting, or when the accumulated tension is high enough. Rate-limited and
kill-switchable so we don't blow up the OpenAI budget.

Returns enriched event details that get merged into the outbound payload:
  - enriched event_type (e.g. heuristic "shot_on_goal" + AI confirms "goal")
  - rich description in natural language
  - sentiment ("calm"|"tense"|"euphoric"|"frustrated")
"""

import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

DISABLED = os.getenv("AI_ENRICHMENT_DISABLED", "").lower() in ("1", "true", "yes")


VALID_EVENT_TYPES = {
    "goal", "goal_chance", "celebration", "yellow_card", "red_card",
    "foul", "penalty", "offside", "substitution", "corner",
    "free_kick", "normal_play", "crowd_reaction",
}


class AIEnrichment:
    RATE_LIMIT_SEC = 3.0
    MAX_HISTORY = 5

    TRIGGER_EVENTS = {
        "shot_on_goal", "corner", "possession_change", "fast_break",
        "out_of_bounds", "foul", "stoppage",
    }
    TENSION_TRIGGER = 5.5  # was 7.0 — fire more often

    def __init__(self):
        self._last_call: float = 0.0
        self._history: List[Dict[str, Any]] = []
        self._client = None
        self.enabled = False

        if DISABLED:
            print("[ai_enrichment] disabled by AI_ENRICHMENT_DISABLED env var")
            return
        if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY):
            print("[ai_enrichment] disabled (no Azure OpenAI credentials)")
            return

        try:
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY,
                api_version=AZURE_OPENAI_API_VERSION,
            )
            self.enabled = True
            print("[ai_enrichment] enabled (GPT-4o via Azure)")
        except Exception as e:
            print(f"[ai_enrichment] init error: {e}")

    async def maybe_enrich(
        self,
        frame: np.ndarray,
        heuristic_event: Optional[Dict[str, Any]],
        tension: float,
        crowd_intensity: float,
        time_sec: float,
        possession: Optional[Dict[str, Any]] = None,
        match_context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Decide if this frame warrants a GPT-4o call, and if so, make it.

        match_context is a compact text summary of accumulated match state
        (from MatchContext.compact_text()) — injected into the prompt so the
        AI knows score, cards, momentum, and recent events.
        """
        if not self.enabled:
            return None

        # Rate limit
        now = time.time()
        if now - self._last_call < self.RATE_LIMIT_SEC:
            return None

        # Trigger logic
        event_type = heuristic_event.get("event_type") if heuristic_event else None
        should_call = (
            (event_type in self.TRIGGER_EVENTS)
            or (tension >= self.TENSION_TRIGGER)
        )
        if not should_call:
            return None

        self._last_call = now

        # Run the blocking SDK call off the event loop
        try:
            enriched = await asyncio.to_thread(
                self._call_gpt4o, frame, heuristic_event, tension,
                crowd_intensity, possession, match_context,
            )
        except Exception as e:
            print(f"[ai_enrichment] call failed: {e}")
            return None

        if enriched:
            self._history.append({
                "event_type": enriched.get("event_type", "normal_play"),
                "tension": tension,
                "time_sec": time_sec,
                "description": enriched.get("description", ""),
            })
            if len(self._history) > self.MAX_HISTORY:
                self._history = self._history[-self.MAX_HISTORY:]

        return enriched

    # ─── Narrative (periodic match summary) ────────────────────────────────

    async def generate_narrative(
        self,
        frame: np.ndarray,
        match_context: str,
    ) -> Optional[str]:
        """Compose a broadcast-style narrative summary from accumulated state.

        Called every NARRATIVE_INTERVAL_SEC of match time (not wall-clock).
        Uses a dedicated prompt that optimizes for coherence and journalist-
        grade phrasing, not event classification.
        """
        if not self.enabled:
            return None
        try:
            return await asyncio.to_thread(
                self._call_narrative, frame, match_context,
            )
        except Exception as e:
            print(f"[ai_enrichment] narrative call failed: {e}")
            return None

    def _call_narrative(self, frame: np.ndarray, match_context: str) -> Optional[str]:
        frame_b64 = self._frame_to_b64(frame)
        prompt = (
            "You are a live football commentator writing a brief match-status "
            "update for viewers tuning in. Based on the accumulated match "
            "state below and the current frame, write exactly ONE or TWO "
            "sentences that capture:\n"
            "- Current score and which team is dominating\n"
            "- A key recent development (last 5 minutes)\n"
            "- Tone matching the momentum (tense / euphoric / frustrated / calm)\n\n"
            "Output ONLY the sentences, no JSON, no quotes, no prefix.\n\n"
            f"MATCH STATE:\n{match_context}"
        )
        resp = self._client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{frame_b64}",
                            "detail": "low",
                        },
                    },
                ],
            }],
            max_tokens=150,
            temperature=0.5,  # slightly warmer for narrative voice
        )
        text = (resp.choices[0].message.content or "").strip()
        # Trim any accidental quotes the model adds
        return text.strip('"').strip("'") or None

    # ─── Internals ─────────────────────────────────────────────────────────

    def _call_gpt4o(
        self,
        frame: np.ndarray,
        heuristic_event: Optional[Dict[str, Any]],
        tension: float,
        crowd_intensity: float,
        possession: Optional[Dict[str, Any]],
        match_context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        frame_b64 = self._frame_to_b64(frame)
        prompt = self._build_prompt(
            heuristic_event, tension, crowd_intensity, possession, match_context,
        )

        resp = self._client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{frame_b64}",
                            "detail": "low",
                        },
                    },
                ],
            }],
            max_tokens=300,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        return self._parse_response(text)

    def _build_prompt(
        self,
        heuristic_event: Optional[Dict[str, Any]],
        tension: float,
        crowd_intensity: float,
        possession: Optional[Dict[str, Any]],
        match_context: Optional[str] = None,
    ) -> str:
        sections = [
            "You are a live football broadcast analyst. You are watching ONE frame "
            "but must infer what's happening from visible context. Respond ONLY in "
            "valid JSON (no markdown, no explanation):\n"
            "{\n"
            "  \"event_type\": \"goal\" | \"goal_chance\" | \"celebration\" | \"yellow_card\" | \"red_card\" | "
            "\"foul\" | \"penalty\" | \"offside\" | \"substitution\" | \"corner\" | \"free_kick\" | "
            "\"normal_play\" | \"crowd_reaction\",\n"
            "  \"description\": \"1-2 sentences broadcast-quality\",\n"
            "  \"sentiment\": \"calm\" | \"tense\" | \"euphoric\" | \"frustrated\",\n"
            "  \"confirmed\": true | false\n"
            "}\n\n"
            "BE DECISIVE. Broadcast viewers need fast, confident calls. If you see:\n"
            "- Referee holding a yellow/red card up → emit yellow_card / red_card\n"
            "- Referee whistle pose or raised arm + players crowded → foul\n"
            "- Ball on penalty spot or players lining up at box edge → penalty\n"
            "- Linesman flag raised → offside\n"
            "- Player crossing sideline with a replacement nearby → substitution\n"
            "- Ball in corner quadrant with player preparing → corner\n"
            "- Wall of players + ball outside the box → free_kick\n"
            "- Ball in net OR goalkeeper crumpled + players celebrating → goal\n"
            "- Shot visible heading at goal → goal_chance\n"
            "- Players clustered celebrating after a goal → celebration\n\n"
            "Use normal_play ONLY when nothing above applies. Err toward specificity "
            "over normal_play — we'd rather over-detect and let humans filter than miss events."
        ]

        if heuristic_event:
            sections.append(
                f"HEURISTIC SIGNAL: Our tracker flagged '{heuristic_event.get('event_type')}'"
                f" (team={heuristic_event.get('team', '?')}, {heuristic_event.get('description', '')}).\n"
                "This is a strong prior. Use the image to confirm the specific event type. "
                "Set 'confirmed': true when the visual evidence backs it up."
            )

        sections.append(
            f"CONTEXT: tension={tension:.1f}/10, crowd_intensity={crowd_intensity:.1f}/10."
        )

        if possession and possession.get("team"):
            sections.append(
                f"POSSESSION: {possession.get('team')} ({possession.get('home_percent', 50)}% home, "
                f"{possession.get('away_percent', 50)}% away)."
            )

        # Match-level accumulated context — the authoritative state.
        # Takes precedence over self._history (which is just last 3 AI calls).
        if match_context:
            sections.append(f"MATCH CONTEXT:\n{match_context}")
        elif self._history:
            recent = "\n".join(
                f"  - [{h['event_type']}] {h['description']}" for h in self._history[-3:]
            )
            sections.append(f"RECENT EVENTS:\n{recent}\nMaintain narrative continuity.")

        return "\n\n".join(sections)

    @staticmethod
    def _parse_response(text: str) -> Dict[str, Any]:
        cleaned = text.strip().strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            result = json.loads(cleaned)
        except Exception:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    result = json.loads(m.group())
                except Exception:
                    return {}
            else:
                return {}

        if result.get("event_type") not in VALID_EVENT_TYPES:
            result["event_type"] = "normal_play"
        return result

    @staticmethod
    def _frame_to_b64(frame: np.ndarray) -> str:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode("utf-8")
