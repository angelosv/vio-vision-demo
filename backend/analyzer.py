import base64
import json
import os
import subprocess
import time
from typing import Dict, Any, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

# ─── Config ──────────────────────────────────────────────────────────────────
# Gemma (local GPU)
GEMMA_ENDPOINT = os.getenv("GEMMA_ENDPOINT", "http://100.99.128.76:11434/v1/chat/completions")
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "gemma4:26b")

# Azure OpenAI (GPT-4o)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY  = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# Default AI mode: "cloud" (GPT-4o Azure) or "local" (Gemma)
DEFAULT_AI_MODE = os.getenv("AI_MODE", "cloud")

YOLO_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "yolo11n.pt")

# ─── YOLO (lazy-load once) ────────────────────────────────────────────────────
_yolo_model: Optional[YOLO] = None

def get_yolo_model() -> YOLO:  # type: ignore[return]
    global _yolo_model
    if _yolo_model is None:
        _yolo_model = YOLO(YOLO_MODEL_PATH)
    return _yolo_model

_LABEL_MAP = {
    "person":      "Player",
    "sports ball": "Ball",
    "goalkeeper":  "GK",
}

def detect_objects(frame: np.ndarray) -> List[Dict[str, Any]]:
    """Run YOLO on a frame and return normalized bounding boxes."""
    model = get_yolo_model()
    h, w = frame.shape[:2]
    results = model(frame, verbose=False, conf=0.3, iou=0.5)
    detections = []
    for result in results:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            raw_label = result.names[cls_id]
            label = _LABEL_MAP.get(raw_label, raw_label)
            conf = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append({
                "label": label,
                "confidence": round(conf, 2),
                "box": [x1 / w, y1 / h, x2 / w, y2 / h],
            })
    return detections


def extract_team_colors(frame: np.ndarray, detections: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add 'color' (hex) and 'team' (0|1) to player detections via jersey colour.

    Returns (updated_detections, [team0_hex, team1_hex]).
    """
    h, w = frame.shape[:2]
    players = []
    hues: List[float] = []

    for det in detections:
        if det["label"] not in ("Player", "GK"):
            continue
        x1, y1, x2, y2 = det["box"]
        # Convert normalised → pixel
        px1, py1 = int(x1 * w), int(y1 * h)
        px2, py2 = int(x2 * w), int(y2 * h)
        bw, bh = px2 - px1, py2 - py1
        if bw < 4 or bh < 4:
            continue
        # Crop jersey region: top 30-60% height, center 60% width
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
        # Convert dominant HSV to hex for display
        bgr = cv2.cvtColor(np.uint8([[[avg_hue, avg_sat, avg_val]]]), cv2.COLOR_HSV2BGR)
        b_c, g_c, r_c = int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])
        det["color"] = f"#{r_c:02x}{g_c:02x}{b_c:02x}"
        hues.append(avg_hue)
        players.append(det)

    # Cluster into 2 teams using simple threshold on hue
    team_colors = ["#FE9330", "#2C7A94"]  # fallback
    if len(hues) >= 2:
        hue_arr = np.array(hues).reshape(-1, 1).astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(hue_arr, 2, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        for i, det in enumerate(players):
            det["team"] = int(labels[i][0])
        # Derive team representative colours from cluster centers
        for t in range(2):
            ch = float(centers[t][0])
            bgr = cv2.cvtColor(np.uint8([[[ch, 180, 200]]]), cv2.COLOR_HSV2BGR)
            b_c, g_c, r_c = int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])
            team_colors[t] = f"#{r_c:02x}{g_c:02x}{b_c:02x}"

    return detections, team_colors


# ─── AI Service ──────────────────────────────────────────────────────────────
class AIService:
    MAX_HISTORY = 5
    ENGAGEMENT_COOLDOWN = 30  # seconds between engagement events

    def __init__(self, mode: str = DEFAULT_AI_MODE):
        self.mode = mode
        self.dataset_path = "training_dataset.jsonl"
        self.event_history: List[Dict[str, Any]] = []
        self.match_info: Dict[str, str] = {}  # {home_team, away_team}
        self._last_engagement_time: float = 0.0

    def clear_history(self) -> None:
        """Reset temporal context (e.g. on new analysis or seek)."""
        self.event_history = []
        self._last_engagement_time = 0.0

    def identify_match(self, frame: np.ndarray) -> Dict[str, str]:
        """Ask AI to identify teams from a frame. Stores result in self.match_info."""
        frame_b64 = frame_to_jpeg_base64(frame)
        prompt = (
            "Identify the two football teams in this image from jerseys, logos, "
            "scoreboard, or any visible clues. Respond ONLY in valid JSON "
            "(no markdown, no explanation):\n"
            "{\"home_team\": \"Team Name\", \"away_team\": \"Team Name\"}\n"
            "If you cannot identify, use descriptive names based on jersey color "
            "(e.g. \"Red Team\", \"Blue/White Team\")."
        )
        if self.mode == "cloud":
            result = self.call_gpt4o(prompt, frame_b64)
        else:
            result = self.call_gemma(prompt, frame_b64)
        self.match_info = {
            "home_team": result.get("home_team", "Home"),
            "away_team": result.get("away_team", "Away"),
        }
        return self.match_info

    def analyze(self, frame: np.ndarray, teacher_mode: bool = False,
                crowd_intensity: float = -1) -> Dict[str, Any]:
        frame_b64 = frame_to_jpeg_base64(frame)
        prompt = self._build_prompt(crowd_intensity=crowd_intensity)

        if teacher_mode or self.mode == "cloud":
            result = self.call_gpt4o(prompt, frame_b64)
            if teacher_mode:
                self.save_for_training(frame_b64, result)
            result["model_source"] = "gpt-4o (azure)"
        else:
            result = self.call_gemma(prompt, frame_b64)
            result["model_source"] = "gemma (local)"

        # Engagement cooldown — strip if too soon
        if result.get("engagement"):
            now = time.time()
            if now - self._last_engagement_time < self.ENGAGEMENT_COOLDOWN:
                result["engagement"] = None
            else:
                self._last_engagement_time = now

        # Store in history for temporal context
        self.event_history.append({
            "event_type": result.get("event_type", "normal_play"),
            "tension_score": result.get("tension_score", 0),
            "description": result.get("description", ""),
            "sentiment": result.get("sentiment", "calm"),
        })
        if len(self.event_history) > self.MAX_HISTORY:
            self.event_history = self.event_history[-self.MAX_HISTORY:]

        return result

    def _build_prompt(self, crowd_intensity: float = -1) -> str:
        base = (
            "You are an AI sports analyst. Analyze this football match frame and respond ONLY in valid JSON "
            "(no markdown, no explanation):\n"
            "{\n"
            "  \"event_type\": \"goal_chance\" | \"goal\" | \"celebration\" | \"normal_play\" | \"crowd_reaction\",\n"
            "  \"tension_score\": 0-10,\n"
            "  \"description\": \"1-2 sentences\",\n"
            "  \"sentiment\": \"calm\" | \"tense\" | \"euphoric\" | \"frustrated\",\n"
            "  \"engagement\": null | {\"type\": \"poll\" | \"sentiment\" | \"product\", \"text\": \"display text\"}\n"
            "}"
        )

        sections: List[str] = [base]

        # Match info
        if self.match_info:
            sections.append(
                f"MATCH: {self.match_info.get('home_team', 'Home')} vs "
                f"{self.match_info.get('away_team', 'Away')}. "
                "Use team names in descriptions and engagement text."
            )

        # Audio context
        if crowd_intensity >= 0:
            sections.append(
                f"AUDIO CONTEXT: Crowd noise intensity is {crowd_intensity}/10 "
                "(0 = silent, 10 = roaring crowd). "
                "Use this to inform your tension_score and sentiment — "
                "high crowd noise often indicates exciting or critical moments."
            )

        # Temporal context from recent events
        if self.event_history:
            context_lines = []
            for i, ev in enumerate(self.event_history):
                desc = ev.get("description") or "—"
                context_lines.append(
                    f"  {i+1}. [{ev['event_type']}] tension={ev['tension_score']}, "
                    f"sentiment={ev['sentiment']} — {desc}"
                )
            context = "\n".join(context_lines)
            sections.append(
                f"MATCH CONTEXT — Recent events (oldest to newest):\n{context}\n\n"
                "Use this context to maintain narrative continuity. "
                "Reference what happened before if relevant. Avoid repeating the same description."
            )

        # Engagement instructions
        sections.append(
            "ENGAGEMENT: Optionally include an \"engagement\" field for audience interaction.\n"
            "- \"poll\": A contextual question (e.g. \"Will they score in the next 5 minutes?\")\n"
            "- \"sentiment\": A prompt for audience emotion (e.g. \"Rate your excitement!\")\n"
            "- \"product\": A contextual suggestion (e.g. \"Get the official match jersey!\")\n"
            "Rules: ONLY when highly relevant. NEVER for normal_play. Max once every ~5 cycles. "
            "When not relevant, set engagement to null."
        )

        return "\n\n".join(sections)

    # ── GPT-4o via Azure OpenAI (multimodal) ──────────────────────────────────
    def call_gpt4o(self, prompt: str, frame_b64: str) -> Dict[str, Any]:
        if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
            # Graceful fallback if not configured
            return self.call_gemma(prompt, frame_b64)

        try:
            from openai import AzureOpenAI
            client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY,
                api_version=AZURE_OPENAI_API_VERSION,
            )
            response = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_b64}",
                                "detail": "low",   # low = faster + cheaper
                            }
                        }
                    ]
                }],
                max_tokens=300,
                temperature=0.2,
            )
            text = response.choices[0].message.content or ""
            return self._parse_response(text)
        except Exception as e:
            # Fallback to Gemma if Azure fails
            result = self.call_gemma(prompt, frame_b64)
            result["azure_error"] = str(e)
            return result

    # ── Gemma via local GPU ───────────────────────────────────────────────────
    def call_gemma(self, prompt: str, frame_b64: str) -> Dict[str, Any]:
        full_prompt = f"{prompt}\n\nFRAME_BASE64: {frame_b64[:2000]}..."
        payload = {
            "model": GEMMA_MODEL,
            "messages": [{"role": "user", "content": full_prompt}],
            "max_tokens": 300,
        }
        try:
            proc = subprocess.Popen(
                ["curl", "-s", "--max-time", "60", GEMMA_ENDPOINT,
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(payload)],
                stdout=subprocess.PIPE, text=True
            )
            out, _ = proc.communicate()
            data = json.loads(out)
            return self._parse_response(data["choices"][0]["message"]["content"])
        except Exception as e:
            return {"error": str(e), "event_type": "normal_play", "tension_score": 0,
                    "description": "Analysis unavailable", "sentiment": "calm"}

    def _parse_response(self, text: str) -> Dict[str, Any]:
        text = text.strip().strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
        try:
            return json.loads(text)
        except Exception:
            # Try to extract JSON block from text
            import re
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    pass
            return {"event_type": "normal_play", "tension_score": 0,
                    "description": "Parsing error", "sentiment": "calm"}

    def save_for_training(self, frame_b64: str, label: Dict[str, Any]):
        with open(self.dataset_path, "a") as f:
            f.write(json.dumps({"image": frame_b64, "label": label}) + "\n")


# ─── Helpers ─────────────────────────────────────────────────────────────────
def frame_to_jpeg_base64(frame: np.ndarray) -> str:
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buffer.tobytes()).decode("utf-8")


# Module-level instance (mode comes from AI_MODE env var, default "cloud")
ai_service = AIService()

def analyze_frame(frame: np.ndarray) -> Dict[str, Any]:
    return ai_service.analyze(frame)
