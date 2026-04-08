import base64
import json
import os
import subprocess
from typing import Dict, Any, List, Optional

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


# ─── AI Service ──────────────────────────────────────────────────────────────
class AIService:
    def __init__(self, mode: str = DEFAULT_AI_MODE):
        self.mode = mode
        self.dataset_path = "training_dataset.jsonl"

    def analyze(self, frame: np.ndarray, teacher_mode: bool = False) -> Dict[str, Any]:
        frame_b64 = frame_to_jpeg_base64(frame)
        prompt = self._build_prompt()

        if teacher_mode or self.mode == "cloud":
            result = self.call_gpt4o(prompt, frame_b64)
            if teacher_mode:
                self.save_for_training(frame_b64, result)
            result["model_source"] = "gpt-4o (azure)"
        else:
            result = self.call_gemma(prompt, frame_b64)
            result["model_source"] = "gemma (local)"

        return result

    def _build_prompt(self) -> str:
        return (
            "You are an AI sports analyst. Analyze this football match frame and respond ONLY in valid JSON "
            "(no markdown, no explanation):\n"
            "{\n"
            "  \"event_type\": \"goal_chance\" | \"goal\" | \"celebration\" | \"normal_play\" | \"crowd_reaction\",\n"
            "  \"tension_score\": 0-10,\n"
            "  \"description\": \"1-2 sentences\",\n"
            "  \"sentiment\": \"calm\" | \"tense\" | \"euphoric\" | \"frustrated\"\n"
            "}"
        )

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
