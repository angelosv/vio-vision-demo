# VIO Live - Autonomous Vision Demo (PRO)

This repository contains the prototype for **Vio.live Vision Engine**, a real-time football match analyzer for social commerce.

## Features
- **Dynamic Download:** Pass a video URL to start the analyzer.
- **YOLOv11 Engine:** High-speed object detection for players and ball.
- **Narrative Intelligence:** Identifies high-pressure situations to trigger e-commerce overlays.
- **Full Match Tracking:** Logs event data in JSON for external integration.

## Installation
```bash
pip install opencv-python ultralytics requests
```

## Usage
Run the script by passing a direct video URL:
```bash
python main.py "https://your-video-url.mp4"
```

## Output Example
```json
{
  "timestamp": "12.4s",
  "status": "DANGER - OFFENSIVE PHASE",
  "narrative": "High pressure detected in opponent's half.",
  "vio_ad": "FC Barcelona Jersey 2025 - BUY NOW"
}
```

---
*Created by Lab - Vio.live R&D*
